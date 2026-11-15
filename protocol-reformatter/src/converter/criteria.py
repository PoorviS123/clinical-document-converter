"""Split the placed text of 5.2 and 5.3 into M11 criteria: one numbered item per criterion, in one flat list."""

import copy
import re

type JsonObject = dict[str, object]

CRITERIA_SECTIONS = ("5.2", "5.3")
NUMBERED_CRITERION = re.compile(r"^\s*(\d+)[.)]\s+(?P<text>\S.*)$", re.DOTALL)
LETTERED_ITEM = re.compile(r"^\s*(\w|[ivx]+)[.)]\s+\S", re.IGNORECASE)
MAX_SUBHEADING_CHARS = 90
SUBHEADING_END = (".", ":", ";", ",")


def is_numbered(block: JsonObject) -> bool:
    return block["type"] == "paragraph" and bool(NUMBERED_CRITERION.match(block["text"]))


def is_subheading(block: JsonObject, following: JsonObject | None) -> bool:
    """A short unnumbered body-style line (not a list item) right before a numbered criterion: a sponsor's category label such as "Age"."""
    if block["type"] != "paragraph" or block["style"] != "Normal" or following is None or not is_numbered(following):
        return False
    text = block["text"].strip()
    return (len(text) <= MAX_SUBHEADING_CHARS and not text.endswith(SUBHEADING_END)
            and not LETTERED_ITEM.match(text))


def split_criteria(blocks: list[JsonObject]) -> tuple[list[JsonObject], list[list[JsonObject]], list[str]]:
    """Return (blocks before the first criterion, one block list per criterion, dropped subheading texts).

    Returns no criteria when the text has no numbered paragraph or holds a table, so the section keeps its blocks.
    """
    if not any(is_numbered(block) for block in blocks) or any(block["type"] == "table" for block in blocks):
        return blocks, [], []
    lead, criteria, dropped = [], [], []
    for index, block in enumerate(blocks):
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if is_numbered(block):
            criteria.append([block])
        elif is_subheading(block, following):
            dropped.append(block["text"].strip())
        elif criteria:
            criteria[-1].append(block)
        else:
            lead.append(block)
    return lead, criteria, dropped


def criterion_text(blocks: list[JsonObject]) -> str:
    first = NUMBERED_CRITERION.match(blocks[0]["text"]).group("text").strip()
    return "\n".join([first, *(block["text"].strip() for block in blocks[1:] if block["type"] == "paragraph")])


def merge_sources(blocks: list[JsonObject]) -> list[JsonObject]:
    merged: dict[str, JsonObject] = {}
    for block in blocks:
        for source in block["custody"]["v10_sources"]:
            existing = merged.setdefault(source["section_id"], copy.deepcopy(source))
            existing["source_indices"] = sorted(set(existing["source_indices"]) | set(source["source_indices"]))
    return list(merged.values())


def criterion_item(number: int, blocks: list[JsonObject]) -> JsonObject:
    custody = copy.deepcopy(blocks[0]["custody"])
    custody["v10_sources"] = merge_sources(blocks)
    return {"number": number, "text": {"value": criterion_text(blocks), "status": "filled", "custody": custody}}


def fill_criteria(protocol: JsonObject) -> list[str]:
    """Move numbered criteria of 5.2/5.3 from `content` into `fields.criteria`; return report notes."""
    notes = []
    eligibility = protocol["sections"]["5"]["subsections"]
    for number in CRITERIA_SECTIONS:
        section = eligibility[number]
        if section["status"] != "populated":
            continue
        lead, criteria, dropped = split_criteria(section["content"])
        if not criteria:
            continue
        section["content"] = lead
        section["fields"]["criteria"] = [criterion_item(index, blocks) for index, blocks in enumerate(criteria, start=1)]
        notes.append(f"{number}: {len(criteria)} criteria numbered 1 to {len(criteria)}")
        if dropped:
            notes.append(f"{number}: dropped sponsor subheadings {', '.join(dropped)} (M11 lists criteria flat)")
    return notes
