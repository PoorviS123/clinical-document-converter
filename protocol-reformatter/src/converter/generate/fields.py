"""Fill structured fields (Overall Design, interventions table, safety reporting, country requirements,
amendment history) by asking an LLM to read them from the protocol's own text."""

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from converter.generate.client import LlmClient, LlmError
from converter.generate.fill import now_iso
from converter.generate.prompts import parse_response
from converter.terminology import TermRegistry
from converter.terminology.registry import default_registry

type JsonObject = dict[str, object]
type Validator = Callable[[JsonObject], list[str]]

MAX_CONTEXT_CHARS = 30000
MAX_OFFERED_CODES = 40
LLM_CONFIDENCE = "medium"
ISO_COUNTRY_SYSTEM = "ISO 3166-1"
SCRIPT_ACTOR: JsonObject = {"kind": "script", "name": "converter.generate.fields", "version": "1"}

INSTRUCTIONS = """\
You are reading structured fields of an ICH M11 clinical trial protocol from the SOURCE PROTOCOL below.
Use only facts the source states. Do not guess: when the source does not state a value, answer null.
For a field with one of a list of allowed values, answer with one of them exactly as written.
Whole numbers only for number fields. Dates are YYYY-MM-DD.
"""


# ---------------------------------------------------------------- schema -> field descriptions

def resolve(schema: JsonObject, node: JsonObject) -> JsonObject:
    while "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
    return node


def code_options(code_schema: JsonObject) -> dict[str, str]:
    """decode -> code for a Code_* schema."""
    return {option["properties"]["decode"]["const"]: option["properties"]["code"]["const"]
            for option in code_schema["oneOf"]}


def describe(schema: JsonObject, node: JsonObject) -> JsonObject:
    """How to ask for and build a slot, a list of slots, or a record of them.

    kinds: code, enum, integer, duration, text, date, external (a coded term looked up in a terminology, else kept as source text), list, record,
    skip (left missing: too large a code list to offer the model).
    """
    node = resolve(schema, node)
    if node.get("type") == "array":
        return {"kind": "list", "item": describe(schema, node["items"]), "min_items": node.get("minItems", 0)}
    properties = node.get("properties", {})
    if "value" not in properties:
        return {"kind": "record", "fields": {name: with_lookup(describe(schema, child), name)
                                              for name, child in properties.items() if name != "custody"}}
    value = resolve(schema, properties["value"]["anyOf"][0])
    if "oneOf" in value:
        codes = code_options(value)
        return {"kind": "code", "codes": codes} if len(codes) <= MAX_OFFERED_CODES else {"kind": "skip"}
    if "enum" in value:
        return {"kind": "enum", "options": value["enum"]}
    if value.get("type") == "integer":
        return {"kind": "integer"}
    if "quantity" in value.get("properties", {}):
        return {"kind": "duration", "codes": code_options(resolve(schema, value["properties"]["unit"]))}
    if "system" in value.get("properties", {}):
        return {"kind": "external"}
    if value.get("format") == "date":
        return {"kind": "date"}
    return {"kind": "text"}


def with_lookup(description: JsonObject, name: str) -> JsonObject:
    """Tag external-term slots with their field name, which picks the terminology to look them up in."""
    if description["kind"] == "external":
        description["lookup"] = name
    elif description["kind"] == "list":
        with_lookup(description["item"], name)
    return description


def prompt_line(name: str, description: JsonObject) -> str:
    kind = description["kind"]
    if kind == "code":
        return f"{name}: one of {json.dumps(list(description['codes']))}"
    if kind == "enum":
        return f"{name}: one of {json.dumps(description['options'])}"
    if kind == "integer":
        return f"{name}: whole number"
    if kind == "duration":
        return f'{name}: {{"quantity": whole number, "unit": one of {json.dumps(list(description["codes"]))}}}'
    if kind == "date":
        return f"{name}: date YYYY-MM-DD"
    if kind == "external":
        return f"{name}: the term as the source writes it"
    if kind == "list":
        return f"{name}: list of ({prompt_line('item', description['item'])})"
    if kind == "record":
        inner = "; ".join(prompt_line(key, child) for key, child in description["fields"].items()
                          if child["kind"] != "skip")
        return f"{name}: object with {{{inner}}}"
    return f"{name}: text from the source"


# ---------------------------------------------------------------- context

@dataclass
class Context:
    text: str = ""
    sources: list[JsonObject] = field(default_factory=list)
    terms: TermRegistry = field(default_factory=default_registry)


def merge_sources(source_lists: list[list[JsonObject]]) -> list[JsonObject]:
    merged: dict[str, JsonObject] = {}
    for sources in source_lists:
        for source in sources:
            existing = merged.setdefault(source["section_id"], copy.deepcopy(source))
            existing["source_indices"] = sorted(set(existing["source_indices"]) | set(source["source_indices"]))
    return list(merged.values())


def block_lines(blocks: list[JsonObject]) -> list[str]:
    lines = []
    for block in blocks:
        if block["type"] == "table":
            lines.extend(" | ".join(row) for row in block["rows"])
        elif block.get("text"):
            lines.append(block["text"])
    return lines


def text_parts(protocol: JsonObject) -> list[tuple[str, str, list[JsonObject]]]:
    """(number, heading text, sources) for every part of the protocol that has source text, in order."""
    parts: list[tuple[str, list[str], list[JsonObject]]] = []

    def visit(node: JsonObject, number: str) -> None:
        sources = node.get("custody", {}).get("v10_sources", [])
        lines = block_lines(node.get("content", []))
        if lines and node.get("status") in (None, "populated"):
            parts.append((f"{number} {node.get('title', '')}".strip(), lines, sources))
        for extra in node.get("additional_subsections", []):
            visit(extra, number)
        children = node.get("subsections")
        for key, child in (children.items() if isinstance(children, dict) else []):
            visit(child, str(child.get("number") or key))

    for key, section in protocol["sections"].items():
        visit(section, key)
    for item in protocol.get("unplaced_v10_content", []):
        heading = item["v10_source"].get("found_heading") or "front matter"
        parts.append((f"front matter {heading}", block_lines(item["blocks"]), [item["v10_source"]]))
    return [(heading, "\n".join(lines), sources) for heading, lines, sources in parts if lines]


def is_lead(number: str, lead_numbers: tuple[str, ...]) -> bool:
    return any(number == lead or number.startswith(lead + ".") for lead in lead_numbers)


def build_context(protocol: JsonObject, lead_numbers: tuple[str, ...]) -> Context:
    """Parts whose number starts with one of `lead_numbers` first, then the rest, cut at MAX_CONTEXT_CHARS."""
    parts = text_parts(protocol)
    leading = [part for part in parts if is_lead(part[0].split(" ")[0], lead_numbers)]
    rest = [part for part in parts if part not in leading]
    context, used, total = [], [], 0
    for heading, text, sources in leading + rest:
        block = f"## {heading}\n{text}"
        if total + len(block) > MAX_CONTEXT_CHARS:
            break
        context.append(block)
        used.append(sources)
        total += len(block)
    return Context("\n\n".join(context), merge_sources(used))


# ---------------------------------------------------------------- building slots

def missing_slot() -> JsonObject:
    custody = {"origin": "none", "v10_sources": [], "actor": dict(SCRIPT_ACTOR), "confidence": "high", "needs_review": False}
    return {"value": None, "status": "missing", "custody": custody}


def ai_custody(context: Context, client: LlmClient) -> JsonObject:
    return {
        "origin": "ai_transformed", "v10_sources": copy.deepcopy(context.sources),
        "actor": {"kind": "llm", "name": client.name, "model": client.model},
        "confidence": LLM_CONFIDENCE, "needs_review": True,
    }


def typed_value(description: JsonObject, raw: object) -> object | None:
    """The slot value for the model's raw answer, or None when it isn't one the schema allows."""
    kind = description["kind"]
    if kind == "code":
        code = description["codes"].get(raw) if isinstance(raw, str) else None
        return {"code": code, "decode": raw} if code else None
    if kind == "enum":
        return raw if raw in description["options"] else None
    if kind == "integer":
        return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None
    if kind == "duration" and isinstance(raw, dict):
        unit = typed_value({"kind": "code", "codes": description["codes"]}, raw.get("unit"))
        quantity = raw.get("quantity")
        if unit and isinstance(quantity, int) and not isinstance(quantity, bool) and quantity >= 0:
            return {"quantity": quantity, "unit": unit}
        return None
    if kind == "date" and isinstance(raw, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            date.fromisoformat(raw)
        except ValueError:
            return None
        return raw
    if kind == "text" and isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def build_slot(description: JsonObject, raw: object, context: Context, client: LlmClient) -> JsonObject:
    """A filled slot, an `unsure` slot holding the source term (external codes), or a missing slot."""
    if description["kind"] == "external":
        if not (isinstance(raw, str) and raw.strip()):
            return missing_slot()
        term = context.terms.resolve(description.get("lookup", ""), raw.strip())
        if term:
            return {"value": {"system": term.system, "code": term.code, "decode": term.decode},
                    "status": "filled", "raw_text": raw.strip(), "custody": ai_custody(context, client)}
        return {"value": None, "status": "unsure", "raw_text": raw.strip(), "custody": {
            "origin": "v10_derived", "v10_sources": copy.deepcopy(context.sources), "actor": dict(SCRIPT_ACTOR),
            "confidence": LLM_CONFIDENCE, "needs_review": True}}
    value = typed_value(description, raw)
    if value is None:
        return missing_slot()
    return {"value": value, "status": "filled", "custody": ai_custody(context, client)}


def build_value(description: JsonObject, raw: object, context: Context, client: LlmClient) -> object:
    """A slot, list of slots, or record of them for the model's raw answer."""
    kind = description["kind"]
    if kind == "list":
        items = raw if isinstance(raw, list) else []
        built = [build_value(description["item"], item, context, client) for item in items]
        built = [item for item in built if has_content(item)]
        return built or ([missing_slot()] if description["min_items"] and description["item"]["kind"] != "record" else [])
    if kind == "record":
        answer = raw if isinstance(raw, dict) else {}
        return {name: build_value(child, answer.get(name), context, client) for name, child in description["fields"].items()}
    return build_slot(description, raw, context, client)


def has_content(built: object) -> bool:
    if isinstance(built, dict) and "status" in built:
        return built["status"] != "missing"
    if isinstance(built, dict):
        return any(has_content(value) for value in built.values())
    if isinstance(built, list):
        return any(has_content(item) for item in built)
    return False


def mark_section_ai(section: JsonObject, context: Context, client: LlmClient) -> None:
    """A section holding an LLM-read slot is ai_transformed as a whole (validator rule)."""
    custody = section["custody"]
    custody["origin"] = "ai_transformed"
    custody["v10_sources"] = merge_sources([custody.get("v10_sources", []), context.sources])
    custody["actor"] = {"kind": "llm", "name": client.name, "model": client.model}
    custody["needs_review"] = True
    custody.pop("review", None)
    if section["status"] == "missing":
        section["status"] = "populated"


# ---------------------------------------------------------------- one extraction

def ask(prompt: str, client: LlmClient) -> JsonObject | None:
    try:
        return parse_response(client.complete(prompt))
    except (LlmError, ValueError, json.JSONDecodeError):
        return None


def section_at(protocol: JsonObject, number: str) -> JsonObject:
    parts = number.split(".")
    node = protocol["sections"][parts[0]]
    for depth in range(2, len(parts) + 1):
        node = node["subsections"][".".join(parts[:depth])]
    return node


def slot_at(fields: JsonObject, path: str) -> object:
    node: object = fields
    for part in path.split("."):
        node = node[part]
    return node


def set_slot_at(fields: JsonObject, path: str, built: object) -> None:
    *parents, last = path.split(".")
    node = fields
    for part in parents:
        node = node[part]
    node[last] = built


@dataclass
class Extraction:
    """What one prompt asks for, and where the answers go."""
    label: str
    section: str
    lead_numbers: tuple[str, ...]
    ask_for: str  # extra instruction, task-specific
    scalars: dict[str, JsonObject] = field(default_factory=dict)  # fields path -> description
    records: tuple[str, JsonObject] | None = None  # (fields key, list description) of record-per-row answers


def extract(protocol: JsonObject, client: LlmClient, schema: JsonObject, validate: Validator,
            extraction: Extraction) -> JsonObject:
    """Return a copy of `protocol` with the extraction's answers applied; answers that don't validate are dropped."""
    result = copy.deepcopy(protocol)
    section = section_at(result, extraction.section)
    context = build_context(result, extraction.lead_numbers)
    filled, rejected = [], []
    if context.text and context.sources:
        prompt = build_prompt(extraction, context)
        answers = ask(prompt, client)
        if answers is None:
            rejected = [extraction.label]
        else:
            apply_scalars(result, section, extraction, answers, context, client, validate, filled, rejected)
            apply_records(result, section, extraction, answers, context, client, validate, filled, rejected)
    log = result.setdefault("processing_log", [])
    assert isinstance(log, list)
    log.append({"step": f"extract_{extraction.label}", "actor": {"kind": "llm", "name": "converter.generate.fields",
                "model": client.model}, "at": now_iso(),
                "summary": f"{extraction.label}: filled {len(filled)}: {', '.join(filled) or 'none'}; "
                           f"rejected {len(rejected)}: {', '.join(rejected) or 'none'}"})
    return result


def build_prompt(extraction: Extraction, context: Context) -> str:
    lines = [f"- {prompt_line(path, description)}" for path, description in extraction.scalars.items()]
    if extraction.records:
        name, description = extraction.records
        lines.append(f"- {prompt_line(name, description)}")
    return (f"{INSTRUCTIONS}\n{extraction.ask_for}\n\nFIELDS (answer as one JSON object keyed by field name, "
            f"each value the answer or null):\n" + "\n".join(lines) + f"\n\nSOURCE PROTOCOL:\n{context.text}\n")


def try_apply(protocol: JsonObject, section: JsonObject, apply: Callable[[], None], context: Context,
              client: LlmClient, validate: Validator) -> bool:
    """Run `apply`, mark the section, validate; undo everything on errors."""
    snapshot = copy.deepcopy(section)
    apply()
    mark_section_ai(section, context, client)
    if validate(protocol):
        section.clear()
        section.update(snapshot)
        return False
    return True


def apply_scalars(protocol, section, extraction, answers, context, client, validate, filled, rejected) -> None:
    fields = section["fields"]
    for path, description in extraction.scalars.items():
        raw = answers.get(path)
        if raw is None:
            continue
        built = build_value(description, raw, context, client)
        if not has_content(built):
            continue
        ok = try_apply(protocol, section, lambda: set_slot_at(section["fields"], path, built), context, client, validate)
        (filled if ok else rejected).append(path)


def apply_records(protocol, section, extraction, answers, context, client, validate, filled, rejected) -> None:
    if not extraction.records:
        return
    name, description = extraction.records
    rows = answers.get(name)
    if not isinstance(rows, list):
        return
    kept: list[object] = []
    for index, row in enumerate(rows):
        built = build_value(description["item"], row, context, client)
        if not has_content(built):
            continue
        candidate = [*kept, built]
        if try_apply(protocol, section, lambda: section["fields"].__setitem__(name, candidate), context, client, validate):
            kept = candidate
            filled.append(f"{name}[{index}]")
        else:
            rejected.append(f"{name}[{index}]")


# ---------------------------------------------------------------- what to extract

def navigate(description: JsonObject, path: str) -> JsonObject:
    for part in path.split("."):
        description = description["fields"][part]
    return description


def fields_description(schema: JsonObject, def_name: str) -> JsonObject:
    return describe(schema, schema["$defs"][def_name])


def overall_design_extraction(schema: JsonObject) -> Extraction:
    design = fields_description(schema, "Fields_1_1_2")
    paths = [
        "intervention_model", "population_type", "control_type", "control_description", "intervention_assignment_method",
        "randomisation_type", "other_assignment_method", "stratification", "site_distribution", "site_geographic_scope",
        "master_protocol", "drug_device_combination_product", "adaptive_trial_design", "number_of_arms",
        "trial_blind_schema", "blinded_roles", "participant_number_qualifier", "number_of_participants",
        "participant_number_basis", "intervention_duration", "participation_duration",
        "intervention_duration_description", "participation_duration_description", "independent_committees",
        "other_committees", "population_age.minimum", "population_age.minimum_unit", "population_age.maximum",
        "population_age.maximum_unit",
    ]
    return Extraction(
        "overall_design", "1.1.2", ("1.1", "1.1.1", "1.1.2", "4.1", "5.1", "6.1"),
        "Fields of the Overall Design: read the trial's design, blinding, size, duration and age limits. Give a "
        "duration description (text) only when the duration is not a single number of units. Give "
        "randomisation_type only for randomised trials and other_assignment_method only when the method is Other.",
        {path: navigate(design, path) for path in paths},
    )


def interventions_extraction(schema: JsonObject) -> Extraction:
    description = fields_description(schema, "Fields_6")["fields"]["interventions"]
    return Extraction(
        "interventions", "6", ("6", "6.1", "1.1.2", "4.1"),
        "interventions: one row for each combination of arm and trial intervention (drug, placebo, background "
        "therapy, device). Dose form and route: the term as the source writes it.",
        records=("interventions", description),
    )


def safety_reporting_extraction(schema: JsonObject) -> Extraction:
    description = fields_description(schema, "Fields_9_2")["fields"]["safety_event_reporting"]
    return Extraction(
        "safety_event_reporting", "9.2", ("9.2", "9.1", "9", "8.4"),
        "safety_event_reporting: one row for each kind of safety event the protocol says to report to the sponsor, "
        "with when reporting starts and ends, how fast, and by what method.",
        records=("safety_event_reporting", description),
    )


def amendment_extraction(schema: JsonObject) -> Extraction:
    history = fields_description(schema, "Fields_12_3")["fields"]
    return Extraction(
        "amendment_history", "12.3", ("12.3",),
        "Amendment history: read it from the document history, summary of changes or title page. Answer null when "
        "the protocol shows no amendment information. For prior_amendments, one row per amendment already issued.",
        {name: history[name] for name in ("amendment_statement", "overview_location", "original_protocol_approval_date")},
        records=("prior_amendments", history["prior_amendments"]),
    )


def structured_extractions(schema: JsonObject) -> list[Extraction]:
    return [overall_design_extraction(schema), interventions_extraction(schema),
            safety_reporting_extraction(schema), amendment_extraction(schema)]


def extract_structured_fields(protocol: JsonObject, client: LlmClient, schema: JsonObject,
                              validate: Validator) -> JsonObject:
    result = protocol
    for extraction in structured_extractions(schema):
        result = extract(result, client, schema, validate, extraction)
    result = extract_country_regions(result, client, schema, validate)
    return extract_objectives(result, client, schema, validate)


# ---------------------------------------------------------------- 12.2 country and region requirements

COUNTRY_INSTRUCTIONS = """\
Country or region specific requirements: read only text the source gives for one named country or region
(for example "European Union", "Japan", "United States"). One entry per country or region:
  {"country_code": ISO 3166-1 alpha-3 code, "country_name": name, "requirements": [paragraph, ...],
   "clarifications": [paragraph, ...]}
requirements are the specific requirements and clarifications are explanations of differences from the global
protocol. Copy the source's wording. Answer {"country_regions": []} when the source has no such text.
"""
ALPHA3 = re.compile(r"^[A-Z]{3}$")


def ai_block(text: str, context: Context, client: LlmClient) -> JsonObject:
    return {"type": "paragraph", "text": text.strip(), "style": "Normal", "custody": ai_custody(context, client)}


def country_region(entry: object, context: Context, client: LlmClient) -> JsonObject | None:
    if not isinstance(entry, dict):
        return None
    code, name = entry.get("country_code"), entry.get("country_name")
    if not (isinstance(code, str) and ALPHA3.match(code) and isinstance(name, str) and name.strip()):
        return None
    texts = {key: [text for text in entry.get(key) or [] if isinstance(text, str) and text.strip()]
             for key in ("requirements", "clarifications")}
    if not any(texts.values()):
        return None
    identifier = {"value": {"system": ISO_COUNTRY_SYSTEM, "code": code, "decode": name.strip()}, "status": "filled",
                  "custody": ai_custody(context, client)}
    return {"identifier": identifier,
            "requirements": [ai_block(text, context, client) for text in texts["requirements"]],
            "clarifications": [ai_block(text, context, client) for text in texts["clarifications"]],
            "custody": ai_custody(context, client)}


def extract_country_regions(protocol: JsonObject, client: LlmClient, schema: JsonObject,
                            validate: Validator) -> JsonObject:
    """Fill 12.2 from country-specific text found anywhere in the protocol; only when 12.2 itself is empty."""
    result = copy.deepcopy(protocol)
    section = section_at(result, "12.2")
    filled: list[str] = []
    if section["status"] == "missing":
        context = build_context(result, ("12.2", "12"))
        answers = ask(f"{INSTRUCTIONS}\n{COUNTRY_INSTRUCTIONS}\nAnswer as one JSON object.\n\nSOURCE PROTOCOL:\n"
                      f"{context.text}\n", client) if context.sources else None
        regions: list[JsonObject] = []
        for entry in (answers or {}).get("country_regions") or []:
            region = country_region(entry, context, client)
            if region is None:
                continue
            candidate = [*regions, region]
            if try_apply(result, section, lambda: section["fields"].__setitem__("country_regions", candidate),
                         context, client, validate):
                regions = candidate
                filled.append(region["identifier"]["value"]["code"])
    log = result.setdefault("processing_log", [])
    assert isinstance(log, list)
    log.append({"step": "extract_country_regions", "actor": {"kind": "llm", "name": "converter.generate.fields",
                "model": client.model}, "at": now_iso(),
                "summary": f"country_regions: filled {len(filled)}: {', '.join(filled) or 'none'}"})
    return result


# ---------------------------------------------------------------- 3 objectives and estimands

OBJECTIVE_KINDS = {"primary": "3.1", "secondary": "3.2", "exploratory": "3.3"}
OBJECTIVE_INSTRUCTIONS = """\
Trial objectives, endpoints and estimands (ICH E9(R1)): copy the source's wording. Answer one JSON object:
{"primary": [objective, ...], "secondary": [...], "exploratory": [...]}
where each objective is
  {"category": "Efficacy"/"Safety"/etc. or null, "statement": the objective as written,
   "estimands": [{"population": text or null, "treatment": text or null, "endpoint": the endpoint(s) that
     measure this objective, "population_level_summary": text or null,
     "intercurrent_events": [{"description": text, "strategy": text}]}]}
Give one estimand per objective (more only when the source defines several) and always give its endpoint.
Leave population, treatment, summary and intercurrent events null unless the source states them. Use [] for a
kind with no objectives.
"""
TEXT_DESCRIPTION: JsonObject = {"kind": "text"}


def text_slot(raw: object, context: Context, client: LlmClient) -> JsonObject:
    return build_slot(TEXT_DESCRIPTION, raw, context, client)


def build_estimand(kind: str, index: int, number: int, entry: object, context: Context, client: LlmClient) -> JsonObject:
    entry = entry if isinstance(entry, dict) else {}
    events = [{"description": text_slot(event.get("description"), context, client),
               "strategy": text_slot(event.get("strategy"), context, client)}
              for event in entry.get("intercurrent_events") or []
              if isinstance(event, dict) and has_content(text_slot(event.get("description"), context, client))]
    return {"id": f"{kind}-{index}-estimand-{number}",
            **{name: text_slot(entry.get(name), context, client)
               for name in ("population", "treatment", "endpoint", "population_level_summary")},
            "intercurrent_events": events, "custody": ai_custody(context, client)}


def build_objective(kind: str, index: int, entry: object, context: Context, client: LlmClient) -> JsonObject | None:
    if not isinstance(entry, dict) or not has_content(text_slot(entry.get("statement"), context, client)):
        return None
    estimands = [build_estimand(kind, index, number, estimand, context, client)
                 for number, estimand in enumerate(entry.get("estimands") or [{}], start=1)]
    return {"id": f"{kind}-{index}", "number": f"{OBJECTIVE_KINDS[kind]}.{index}",
            "title": f"{kind.title()} Objective {index}",
            "category_label": text_slot(entry.get("category"), context, client),
            "statement": text_slot(entry["statement"], context, client),
            "estimands": estimands or [build_estimand(kind, index, 1, {}, context, client)],
            "additional_attributes": [], "content": [], "heading_source": None,
            "custody": ai_custody(context, client)}


def extract_objectives(protocol: JsonObject, client: LlmClient, schema: JsonObject, validate: Validator) -> JsonObject:
    """Fill the 3.1-3.3 objectives; the source text they replace is dropped from that section's extra headings."""
    result = copy.deepcopy(protocol)
    context = build_context(result, ("3", "1.1.1"))
    answers = ask(f"{INSTRUCTIONS}\n{OBJECTIVE_INSTRUCTIONS}\n\nSOURCE PROTOCOL:\n{context.text}\n", client) \
        if context.sources else None
    filled: list[str] = []
    for kind, number in OBJECTIVE_KINDS.items():
        section = section_at(result, number)
        entries = (answers or {}).get(kind)
        objectives = [build_objective(kind, index, entry, context, client)
                      for index, entry in enumerate(entries if isinstance(entries, list) else [], start=1)]
        objectives = [objective for objective in objectives if objective]
        if objectives and try_apply(result, section, lambda: apply_objectives(section, objectives), context, client, validate):
            filled.append(f"{kind}[{len(objectives)}]")
    log = result.setdefault("processing_log", [])
    assert isinstance(log, list)
    log.append({"step": "extract_objectives", "actor": {"kind": "llm", "name": "converter.generate.fields",
                "model": client.model}, "at": now_iso(),
                "summary": f"objectives: filled {len(filled)}: {', '.join(filled) or 'none'}"})
    return result


def apply_objectives(section: JsonObject, objectives: list[JsonObject]) -> None:
    section["objectives"] = objectives
    section["status"] = "container"
    section["additional_subsections"] = []
