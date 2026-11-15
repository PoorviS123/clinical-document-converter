"""What the rendered docx must contain, derived from the protocol JSON in render order."""

from dataclasses import dataclass, field

from converter.render.docx import LEAD_INS, is_lead_in, is_title_field, natural_key

type JsonObject = dict[str, object]


@dataclass
class Expectation:
    """One block's text, whitespace-stripped, and where it came from (for the failure message)."""
    kind: str
    text: str
    where: str


@dataclass
class Audit:
    outline: list[tuple[int, str, str]] = field(default_factory=list)
    blocks: list[Expectation] = field(default_factory=list)
    figures: int = 0


def squash(text: str) -> str:
    """Run splits and line wrapping differ between JSON and docx, so compare without whitespace."""
    return "".join(text.split())


def table_text(rows: list[list[object]]) -> str:
    return "".join(str(cell) for row in rows for cell in row)


def build_audit(protocol: JsonObject) -> Audit:
    audit = Audit()
    counters: list[int] = []
    for item in protocol.get("unplaced_v10_content", []):
        if not is_title_field(item):
            add_blocks(audit, item["blocks"], f"front matter {item['v10_source']['found_heading']!r}")
    sections = protocol["sections"]
    for key in sorted(sections, key=int):
        add_section(audit, counters, sections[key], level=1)
    return audit


def add_section(audit: Audit, counters: list[int], section: JsonObject, level: int) -> None:
    if section["status"] == "omitted":
        return
    number = str(section.get("number") or "")
    add_heading(audit, counters, level, str(section["title"]))
    content = section.get("content", [])
    if number in LEAD_INS and content and is_lead_in(content[0]):
        content = content[1:]
    add_blocks(audit, content, f"section {number}")
    children = section.get("subsections", {})
    for key in sorted(children, key=natural_key):
        add_section(audit, counters, children[key], level + 1)
    for extra in section.get("additional_subsections", []):
        add_additional(audit, counters, extra, level + 1, number)


def add_additional(audit: Audit, counters: list[int], node: JsonObject, level: int, parent_number: str) -> None:
    add_heading(audit, counters, level, str(node["title"]))
    add_blocks(audit, node["content"], f"additional subsection {node['title']!r} under {parent_number}")
    for child in node["subsections"]:
        add_additional(audit, counters, child, level + 1, parent_number)


def add_heading(audit: Audit, counters: list[int], level: int, title: str) -> None:
    del counters[level:]
    while len(counters) < level:
        counters.append(0)
    counters[-1] += 1
    audit.outline.append((level, ".".join(map(str, counters)), title))


def add_blocks(audit: Audit, blocks: list[JsonObject], where: str) -> None:
    for block in blocks:
        if block.get("has_image"):
            audit.figures += 1
            continue
        text = table_text(block["rows"]) if block["type"] == "table" else str(block.get("text", ""))
        if squash(text):
            audit.blocks.append(Expectation(block["type"], squash(text), where))
