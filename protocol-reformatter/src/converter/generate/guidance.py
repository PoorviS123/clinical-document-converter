"""Per-section drafting guidance: ICH M11 Technical Specification user guidance, then the CPT v011 key instructions."""

import json
from functools import cache
from pathlib import Path

RESEARCH_DIR = Path(__file__).resolve().parents[3] / "resources" / "research"
M11_GUIDANCE_PATH = RESEARCH_DIR / "m11_user_guidance.json"
STYLE_EXAMPLES_PATH = RESEARCH_DIR / "m11_style_examples.json"
V11_INVENTORY_PATH = RESEARCH_DIR / "schema-inputs" / "v11_inventory.json"


@cache
def m11_guidance() -> dict[str, str]:
    return json.loads(M11_GUIDANCE_PATH.read_text())


@cache
def cpt_key_instructions() -> dict[str, list[str]]:
    inventory = json.loads(V11_INVENTORY_PATH.read_text())
    return {entry["number"]: entry["key_instructions"] for entry in inventory if entry.get("number")}


@cache
def style_examples() -> dict[str, str]:
    examples = json.loads(STYLE_EXAMPLES_PATH.read_text())
    return {number: text for number, text in examples.items() if not number.startswith("_")}


def section_guidance(number: str) -> list[str]:
    """The M11 spec's wording wins; the CPT key instructions are the fallback for sections the spec has no text for."""
    if number in m11_guidance():
        return [m11_guidance()[number]]
    return cpt_key_instructions().get(number, [])


def style_example(number: str) -> str | None:
    return style_examples().get(number)
