"""Fill `missing` sections of a protocol JSON by asking an LLM to draft them from the source text."""

import copy
import json
from collections.abc import Callable, Iterator
from datetime import datetime, timezone

from converter.generate.client import LlmClient, LlmError
from converter.generate.guidance import section_guidance, style_example
from converter.generate.prompts import MAX_DRAFT_WORDS, build_prompt, parse_response

type JsonObject = dict[str, object]
type DocumentValidator = Callable[[JsonObject], list[str]]

MAX_ATTEMPTS = 2
GENERATED_STATUSES = ("populated", "not_applicable", "missing")
SCRIPT_NAME = "converter.generate.fill"


def walk_keyed_sections(sections: dict[str, JsonObject]) -> Iterator[tuple[str, JsonObject]]:
    for key, section in sections.items():
        yield key, section
        children = section.get("subsections")
        if isinstance(children, dict):
            yield from walk_keyed_sections(children)


def walk_sections(sections: dict[str, JsonObject]) -> Iterator[JsonObject]:
    for _, section in walk_keyed_sections(sections):
        yield section


def allowed_statuses(schema: JsonObject, key: str) -> list[str]:
    definition_name = "Section_" + key.replace(".", "_").replace("#", "__")
    definition = schema["$defs"].get(definition_name, {})
    return definition.get("properties", {}).get("status", {}).get("enum", [])


def missing_sections(
    protocol: JsonObject, schema: JsonObject | None = None, has_v10_source: frozenset[str] = frozenset()
) -> list[JsonObject]:
    """Missing sections; with a schema, only those whose status may become `populated`.

    Sections in `has_v10_source` (v011 numbers the crosswalk fills from v010) are skipped: a gap there is a
    placement miss, and drafting it would duplicate source content that exists elsewhere in the protocol.
    """
    sections = protocol["sections"]
    assert isinstance(sections, dict)
    return [
        section for key, section in walk_keyed_sections(sections)
        if section.get("status") == "missing"
        and key.split("#")[0] not in has_v10_source
        and (schema is None or "populated" in allowed_statuses(schema, key))
    ]


def source_text(protocol: JsonObject) -> str:
    """Plain text of every populated section, headed by number and title."""
    sections = protocol["sections"]
    assert isinstance(sections, dict)
    parts: list[str] = []
    for section in walk_sections(sections):
        if section.get("status") != "populated":
            continue
        body = block_texts(section.get("content", []))
        parts.append(f"## {section['number']} {section['title']}\n" + "\n".join(body))
    return "\n\n".join(parts)


def block_texts(blocks: object) -> list[str]:
    texts: list[str] = []
    for block in blocks if isinstance(blocks, list) else []:
        if block.get("type") == "table":
            texts.extend(" | ".join(row) for row in block["rows"])
        else:
            texts.append(block.get("text", ""))
    return texts


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def llm_custody(client: LlmClient, confidence: str) -> JsonObject:
    return {
        "origin": "ai_generated",
        "v10_sources": [],
        "actor": {"kind": "llm", "name": client.name, "model": client.model},
        "confidence": confidence,
        "needs_review": True,
    }


def apply_draft(section: JsonObject, draft: JsonObject, client: LlmClient) -> None:
    status = draft.get("status")
    if status not in GENERATED_STATUSES:
        raise ValueError(f"bad status {status!r}")
    if status == "missing":
        return
    confidence = draft.get("confidence", "low")
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    paragraphs = draft.get("paragraphs", [])
    if status == "populated" and not (isinstance(paragraphs, list) and paragraphs and all(isinstance(p, str) and p.strip() for p in paragraphs)):
        raise ValueError("populated draft has no paragraphs")
    if status == "populated" and sum(len(text.split()) for text in paragraphs) > MAX_DRAFT_WORDS:
        raise ValueError(f"draft is longer than {MAX_DRAFT_WORDS} words")
    custody = llm_custody(client, str(confidence))
    section["status"] = status
    section["custody"] = custody
    section["content"] = [
        {"type": "paragraph", "text": text.strip(), "style": "Normal", "custody": copy.deepcopy(custody)}
        for text in (paragraphs if status == "populated" else [])
    ]


def generate_missing(
    protocol: JsonObject, client: LlmClient, validate: DocumentValidator | None = None,
    schema: JsonObject | None = None, has_v10_source: frozenset[str] = frozenset(),
) -> JsonObject:
    """Return a copy of `protocol` with missing sections drafted. Failures leave the section missing and are logged."""
    result = copy.deepcopy(protocol)
    context = source_text(result)
    filled, no_basis, failed = [], [], []
    for section in missing_sections(result, schema, has_v10_source):
        number, title = str(section["number"] or section["title"]), str(section["title"])
        guidance = section_guidance(number) or [str(line) for line in section.get("x-guidance", [])]
        prompt = build_prompt(number, title, guidance, context, style_example(number))
        outcome = try_fill(section, prompt, client, (lambda: validate(result)) if validate else None)
        {"filled": filled, "no_basis": no_basis, "failed": failed}[outcome].append(number)
    log = result.setdefault("processing_log", [])
    assert isinstance(log, list)
    log.append({
        "step": "generate_missing_sections",
        "actor": {"kind": "llm", "name": SCRIPT_NAME, "model": client.model},
        "at": now_iso(),
        "summary": f"drafted {len(filled)} section(s): {', '.join(filled) or 'none'}; "
                   f"no basis in source for {len(no_basis)}: {', '.join(no_basis) or 'none'}; "
                   f"failed {len(failed)}: {', '.join(failed) or 'none'}",
    })
    return result


def try_fill(section: JsonObject, prompt: str, client: LlmClient, validate: Callable[[], list[str]] | None) -> str:
    """'filled', 'no_basis' (model answered missing) or 'failed' (call, parse or validation error)."""
    for _ in range(MAX_ATTEMPTS):
        snapshot = copy.deepcopy(section)
        try:
            apply_draft(section, parse_response(client.complete(prompt)), client)
        except (LlmError, ValueError, json.JSONDecodeError):
            restore(section, snapshot)
            continue
        if section["status"] == "missing":
            return "no_basis"
        if validate is None or not validate():
            return "filled"
        restore(section, snapshot)
    return "failed"


def restore(section: JsonObject, snapshot: JsonObject) -> None:
    section.clear()
    section.update(snapshot)
