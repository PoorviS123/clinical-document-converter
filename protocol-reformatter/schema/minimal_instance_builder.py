"""Builds the smallest protocol instance the schema accepts, driven by the schema's own structure.

Shared by the schema tests, the converter (place.py) and the converter tests.
"""

import copy

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
type JsonObject = dict[str, Json]

ZERO_SHA256 = "0" * 64
TIMESTAMP = "2026-01-01T00:00:00Z"
SAMPLE_DATE = "2026-01-01"
TEST_ACTOR: JsonObject = {"kind": "script", "name": "test"}
PREFERRED_STATUSES = ["container", "missing"]
SAMPLE_SOURCE_INDEX = 7
REPEATED_ITEM_SECTIONS_WITHOUT_ITEMS = ["3.2", "3.3", "10.5"]


class MinimalInstanceBuilder:
    """Builds the smallest instance the schema accepts, driven by the schema's own structure."""

    def __init__(self, schema: JsonObject) -> None:
        self.schema = schema
        self.defs: JsonObject = schema["$defs"]

    def resolve(self, node: JsonObject) -> tuple[JsonObject, str | None]:
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            resolved = self.defs[name]
            merged = {**resolved, **{key: value for key, value in node.items() if key != "$ref"}}
            return merged, name
        return node, None

    def build(self, node: JsonObject, name: str = "") -> Json:
        node, def_name = self.resolve(node)
        if def_name == "Custody":
            return none_custody()
        if def_name is not None and def_name.endswith("Slot") and def_name != "SlotStatus":
            return missing_slot()
        if "const" in node:
            return node["const"]
        if "enum" in node:
            return self.pick_enum(node["enum"], name)
        options = node.get("oneOf") or node.get("anyOf")
        if options and node.get("type") != "object":
            if any(option.get("type") == "null" for option in options):
                return None
            return self.build(options[0], name)
        node_type = node.get("type")
        if node_type == "object":
            return {key: self.build(node["properties"][key], key) for key in node["required"]}
        if node_type == "array":
            if node.get("minItems", 0) > 0:
                return [self.build(node["items"], name)]
            return []
        return self.scalar(node, name)

    @staticmethod
    def pick_enum(values: list[str], name: str) -> str:
        if name == "status":
            return next((value for value in PREFERRED_STATUSES if value in values), values[0])
        return values[0]

    @staticmethod
    def scalar(node: JsonObject, name: str) -> Json:
        if node.get("format") == "date-time":
            return TIMESTAMP
        if node.get("format") == "date":
            return SAMPLE_DATE
        if name == "sha256":
            return ZERO_SHA256
        node_type = node.get("type")
        if node_type == "integer":
            return 0
        if node_type == "boolean":
            return False
        return "x"


def none_custody() -> JsonObject:
    return {
        "origin": "none",
        "v10_sources": [],
        "actor": copy.deepcopy(TEST_ACTOR),
        "confidence": "high",
        "needs_review": False,
    }


def missing_slot() -> JsonObject:
    return {"value": None, "status": "missing", "custody": none_custody()}


def template_custody() -> JsonObject:
    return {**none_custody(), "origin": "template_text"}


def v10_source(section_id: str) -> JsonObject:
    return {
        "section_id": section_id,
        "found_number": section_id.removeprefix("v10:"),
        "found_heading": "Heading",
        "match": "exact",
        "source_indices": [SAMPLE_SOURCE_INDEX],
        "portion": "all",
    }


def sections_of(instance: JsonObject) -> JsonObject:
    sections = instance["sections"]
    assert isinstance(sections, dict)
    return sections


def subsection(instance: JsonObject, number: str) -> JsonObject:
    node: JsonObject = sections_of(instance)[number.split(".")[0]]
    parts = number.split(".")
    for depth in range(2, len(parts) + 1):
        node = node["subsections"][".".join(parts[:depth])]
    return node


def objective_estimand(kind: str) -> JsonObject:
    return {
        "id": f"{kind}-1-estimand-1",
        "population": missing_slot(),
        "treatment": missing_slot(),
        "endpoint": missing_slot(),
        "population_level_summary": missing_slot(),
        "intercurrent_events": [],
        "custody": none_custody(),
    }


def objective(kind: str, section: str) -> JsonObject:
    return {
        "id": f"{kind}-1",
        "number": f"{section}.1",
        "title": f"{kind.title()} Objective 1",
        "category_label": missing_slot(),
        "statement": missing_slot(),
        "estimands": [objective_estimand(kind)],
        "additional_attributes": [],
        "content": [],
        "heading_source": None,
        "custody": none_custody(),
    }


def analysis(kind: str, section: str) -> JsonObject:
    child = {
        "title": "t",
        "status": "missing",
        "heading_source": None,
        "custody": none_custody(),
        "content": [],
    }
    keys = [
        "statistical_analysis_method",
        "handling_of_data",
        "handling_of_missing_data",
        "sensitivity_analysis",
        "supplementary_analysis",
    ]
    subsections = {
        key: {**child, "number": f"{section}.1.{index}"} for index, key in enumerate(keys, start=1)
    }
    return {
        "objective_id": f"{kind}-1",
        "number": f"{section}.1",
        "title": f"{kind.title()} Objective 1",
        "status": "missing",
        "heading_source": None,
        "custody": none_custody(),
        "content": [],
        "subsections": subsections,
    }


def minimal_instance(schema: JsonObject) -> JsonObject:
    instance = MinimalInstanceBuilder(schema).build(schema)
    assert isinstance(instance, dict)
    instance["title_page"]["sponsors"] = [
        {"role": "sponsor", "name": missing_slot(), "legal_address": missing_slot()}
    ]
    fields = subsection(instance, "1.1.2")["fields"]
    fields["intervention_codes"] = [missing_slot()]
    fields["intervention_nonproprietary_names"] = [missing_slot()]
    subsection(instance, "3.1")["objectives"] = [objective("primary", "3.1")]
    subsection(instance, "10.4")["objective_analyses"] = [analysis("primary", "10.4")]
    for number in REPEATED_ITEM_SECTIONS_WITHOUT_ITEMS:
        script_not_applicable(subsection(instance, number))
    return instance


def script_not_applicable(section: JsonObject) -> None:
    section["status"] = "not_applicable"
    section["custody"] = v10_section_custody("v10_derived", [v10_source("v10:3.2")])


def v10_section_custody(origin: str, sources: list[JsonObject]) -> JsonObject:
    return {**none_custody(), "origin": origin, "v10_sources": copy.deepcopy(sources)}
