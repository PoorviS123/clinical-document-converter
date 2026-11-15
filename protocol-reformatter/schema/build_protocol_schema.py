"""Generate schema/protocol.schema.json from the verified research inputs.

Run with the project interpreter: .venv/bin/python schema/build_protocol_schema.py
The output is one self-contained JSON Schema (draft 2020-12). Data conflicts the
generator had to resolve are printed at the end so they can be reviewed.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
type JsonObject = dict[str, Json]

SCHEMA_DIR = Path(__file__).resolve().parent
ROOT = SCHEMA_DIR.parent
INPUT_DIR = ROOT / "resources" / "research" / "schema-inputs"
OUTPUT_PATH = SCHEMA_DIR / "protocol.schema.json"
ANNOTATED_OUTPUT_PATH = SCHEMA_DIR / "protocol.schema.annotated.json"
ANNOTATION_KEYWORDS = {"description", "default"}
# Keywords whose value maps names to subschemas, so its keys are not keywords.
SUBSCHEMA_MAP_KEYWORDS = {"properties", "$defs"}

DRAFT_URI = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID = "https://medconvert.local/schema/protocol.schema.json"
SCHEMA_TITLE = "CPT v10 to v11 protocol"
SCHEMA_VERSION = "1.0.0"
SOURCE_TEMPLATE = "CPT v010"
TARGET_TEMPLATE = "CPT v011"

SHA256_PATTERN = r"^[0-9a-f]{64}$"
NCIT_CODE_PATTERN = r"^C\d+$"
ACRONYM_MAX_LENGTH = 14
SHORT_TITLE_MAX_LENGTH = 300
MAX_HEADING_LEVEL = 9
MAX_DEFINITION_CHARS = 240
MAX_PERCENT = 100
TOP_LEVEL_SECTION_COUNT = 14

V10_SECTION_ID_PATTERN = (
    r"^v10:(title_page\.[a-z0-9_]+|front\.[a-z0-9_.]+|1\.1\.synopsis\.[a-z0-9_]+"
    r"|\d+(\.\d+){0,4}"
    r"|(\d+(\.\d+){0,4}|x)\.(unnumbered|label)\.[a-z0-9_]+"
    r"|(\d+(\.\d+){0,4}|x|body)\.unmapped)$"
)
UNMAPPED_ID_SUFFIX_PATTERN = r"\.unmapped$"

CUSTODY_ORIGINS = [
    "v10_verbatim",
    "v10_derived",
    "ai_transformed",
    "ai_generated",
    "template_text",
    "human_entered",
    "none",
]
ORIGINS_NEEDING_V10_SOURCE = ["v10_verbatim", "v10_derived", "ai_transformed"]
ORIGINS_WITHOUT_V10_SOURCE = ["ai_generated", "template_text", "none"]
AI_ORIGINS = ["ai_transformed", "ai_generated"]
ORIGINS_WITHOUT_DECISION = ["template_text", "none"]
REVIEW_CLEARING_STATUSES = ["accepted", "edited"]
REVIEW_OPEN_STATUSES = ["unreviewed", "rejected"]
REVIEW_SIGNED_STATUSES = ["accepted", "edited", "rejected"]
SLOT_STATUSES = ["filled", "unsure", "missing", "not_applicable"]
EXTERNAL_CODE_SYSTEMS = [
    "MedDRA", "ISO 3166-1", "ISO 3166-2", "GENC", "EDQM", "IDMP", "SNOMED CT", "UCUM",
    "WHO INN", "CDISC SDTM", "other",
]
V10_MATCH_KINDS = [
    "exact", "renamed", "renumbered", "renamed_and_renumbered", "protocol_added", "inferred",
]
UNPLACED_REASONS = [
    "no_v11_slot", "dropped_per_crosswalk", "unrecognized",
    "duplicate_of_placed_content", "v10_instruction_text",
]

# Inventory nodes replaced by the objective / analysis arrays.
REPEATING_ROOTS = ["3.1.1", "3.2.1", "3.3.1", "10.4.1", "10.5.1"]
OBJECTIVE_KINDS = {
    "primary": {"section": "3.1", "root": "3.1.1", "statement_code": "C85826"},
    "secondary": {"section": "3.2", "root": "3.2.1", "statement_code": "C85827"},
    "exploratory": {"section": "3.3", "root": "3.3.1", "statement_code": "C163559"},
}
ANALYSIS_SECTIONS = {"primary": "10.4", "secondary": "10.5"}
ANALYSIS_SUBSECTIONS = [
    "statistical_analysis_method",
    "handling_of_data",
    "handling_of_missing_data",
    "sensitivity_analysis",
    "supplementary_analysis",
]
# Section 12.3 holds these two unnumbered inventory nodes as fields instead.
FIELD_ONLY_PARENT = "12.3"
UNNUMBERED_ALIASES_NOTE = "crosswalk target number absent from the v11 inventory"

TITLE_PAGE_ROW_PREFIX = "TP."
SYNOPSIS_ROW_PREFIX = "SYN."
UNNUMBERED_ROW_IDS = {
    "TP.18": ["v10:front.amendment_summary"],
    "TP.18a": ["v10:front.amendment_summary"],
    "TP.18b": [
        "v10:front.amendment_summary.amendment_heading",
        "v10:front.amendment_summary.substantial_statement",
    ],
    "TP.18c": ["v10:front.amendment_summary.overall_rationale"],
    "TP.18d": ["v10:front.amendment_summary"],
    "TP.6b": ["v10:6.1"],
    "TP.19": ["v10:front.table_of_contents"],
    "TP.20": ["v10:front.list_of_abbreviations"],
    "3.u1": ["v10:3.unnumbered.estimands_for_primary_objectives"],
    "3.u2": ["v10:3.label.supplementary_estimands"],
    "3.u3": ["v10:3.unnumbered.estimands_for_secondary_objectives"],
    "3.u4": ["v10:3.label.supplementary_estimands_2"],
    "3.u5": ["v10:3.unnumbered.estimands_for_tertiary_exploratory_other_objecti"],
    "3.u6": [
        "v10:3.label.tertiary_exploratory_other_estimands_label",
        "v10:3.label.supplementary_estimands_3",
    ],
    "10.7.1a": ["v10:10.7.1.label.medical_device_ae_and_ade_definition"],
    "10.7.2a": [
        "v10:10.7.2.label.sade_definition",
        "v10:10.7.2.label.unanticipated_sade_usade_definition",
    ],
    "10.7.6a": ["v10:10.7.6.label.sade_reporting_to_x"],
    "10.9a": ["v10:10.9.label.amendment_amendment_number_date"],
}
# Row titles that do not contain the matched id's title, checked by fragment instead.
ROW_TITLE_FRAGMENTS = {
    "TP.6b": "investigational product name",
    "TP.18a": "protocol amendment summary",
    "TP.18b": "substantial",
    "TP.18d": "protocol amendment summary",
    "3.u6": "estimand",
    "10.7.2a": "sade",
}
PLACEHOLDER = re.compile(r"\[[^\]]*\]")
NUMBERED_ROW = re.compile(r"^\d+(\.\d+)*$")

TITLE_PAGE_V10_FIELDS: dict[str, tuple[list[str], str]] = {
    "full_title": (["protocol_title"], "full title"),
    "sponsor_protocol_identifier": (["protocol_number"], "sponsor protocol identifier"),
    "amendment_identifier": (["amendment_number"], "amendment identifier"),
    "amendment_scope": (["amendment_scope", "country_region_identifier"], "amendment scope"),
    "amendment_scope_identifiers": (
        ["amendment_scope", "country_region_identifier"], "amendment scope",
    ),
    "investigational_product_codes": (["compound"], "investigational product code"),
    "trial_phase": (["study_phase"], "trial phase"),
    "short_title": (["brief_title"], "short title"),
    "trial_acronym": (["acronym"], "trial acronym"),
    "sponsors": (["sponsor_name", "legal_registered_address"], "sponsor name and address"),
    "device_manufacturers": (["manufacturer"], "device manufacturer"),
    "regulatory_identifiers": (
        ["regulatory_agency_identifier_numbers", "pediatric_investigational_plan_number"],
        "regulatory or clinical trial identifier",
    ),
    "sponsor_approval": (["approval_date"], "sponsor approval"),
    "sponsor_signatories": (["sponsor_signatory"], "sponsor signatory"),
    "medical_expert_contact": (
        ["medical_monitor_name_and_contact_information"], "medical expert contact",
    ),
}
SCOPE_IDENTIFIER_ELEMENT_ID = 18
SPONSOR_ADDRESS_ELEMENT_ID = 30
SPONSOR_ADDRESS_CODE = "C218677"
SIGNATURE_KINDS = ["image", "text", "location"]
PRODUCT_NAMES_ROWS = ["TP.6", "TP.6b"]
ORIGINAL_PROTOCOL_ROW = "TP.3"
AMENDMENT_NUMBER_ID = "v10:title_page.amendment_number"
VERSION_NUMBER_ID = "v10:title_page.protocol_version"
SUMMARY_AND_REASONS_ROW = "TP.18c"
SUBSTANTIAL_IMPACT_ROW = "TP.18b"
DOCUMENT_HISTORY_ROW = "TP.18a"
AMENDMENT_DETAILS_ROWS = ["TP.18", "TP.18b", "TP.18c", "TP.18d"]
AMENDMENT_CHANGES_ROW = "TP.18d"

REGULATORY_ID_TYPES = [
    "eu_ct_number", "fda_ind_number", "ide_number", "jrct_number", "nct_number",
    "nmpa_ind_number", "who_utn_number", "pediatric_investigational_plan_number", "other",
]
REGULATORY_ID_CODES_NOTE = (
    "M11 codes: eu_ct_number C218684, fda_ind_number C218685, ide_number C218686, "
    "jrct_number C218687, nct_number C172240, nmpa_ind_number C218688, who_utn_number C218689, "
    "other C218690. pediatric_investigational_plan_number has no M11 element of its own."
)

data_notes: list[str] = []


def read_json(file_name: str) -> Json:
    with open(INPUT_DIR / file_name, encoding="utf-8") as handle:
        return json.load(handle)


def ref(def_name: str) -> JsonObject:
    return {"$ref": f"#/$defs/{def_name}"}


def when(condition: JsonObject, then: JsonObject) -> JsonObject:
    return {"if": condition, "then": then}


def property_is(name: str, value: Json) -> JsonObject:
    return {"properties": {name: {"const": value}}, "required": [name]}


def property_in(name: str, values: list[str]) -> JsonObject:
    return {"properties": {name: {"enum": values}}, "required": [name]}


def strict_object(properties: JsonObject, required: list[str] | None = None) -> JsonObject:
    names = list(properties) if required is None else required
    return {
        "type": "object",
        "properties": properties,
        "required": names,
        "additionalProperties": False,
    }


def array_of(items: JsonObject, **extra: Json) -> JsonObject:
    return {"type": "array", "items": items, **extra}


def pascal_case(text: str) -> str:
    return "".join(word[:1].upper() + word[1:] for word in re.findall(r"[A-Za-z0-9]+", text))


def snake_case(text: str) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", text.lower()))


def normalize_title(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("study", "trial"))
    return " ".join(words)


# ---------------------------------------------------------------------------
# M11 element and codelist lookup
# ---------------------------------------------------------------------------


class M11Index:
    def __init__(self, elements: list[JsonObject]) -> None:
        self.elements = elements

    def find(
        self, code: str | None = None, section: str | None = None, element_id: int | None = None
    ) -> JsonObject | None:
        if element_id is not None:
            return next(item for item in self.elements if item["id"] == element_id)
        matches = [item for item in self.elements if item["ncit_code"] == code]
        if section is not None:
            matches = [item for item in matches if item["m11_section"] == section]
        if not matches:
            raise KeyError(f"no M11 element for code={code} section={section}")
        return matches[0]

    @staticmethod
    def element_name(element: JsonObject) -> str:
        name = element["ich_preferred_term"] or re.sub(r"[<>{}\[\]]", "", element["element_name"])
        return name.strip()

    def parts_of_section(self, section: str) -> list[JsonObject]:
        return [
            {
                "element": self.element_name(element),
                "ncit_code": element["ncit_code"],
                "conformance": element["conformance"]["level"],
            }
            for element in self.elements
            if element["m11_section"] == section
        ]

    def annotations(
        self, code: str | None = None, section: str | None = None, element_id: int | None = None
    ) -> JsonObject:
        element = self.find(code, section, element_id)
        if element is None or not element["ncit_code"]:
            return {}
        name = self.element_name(element)
        definition = (element["definition"] or "").strip()
        short_definition = definition.split(". ")[0][:MAX_DEFINITION_CHARS]
        return {
            "x-m11": {
                "element": name,
                "ncit_code": element["ncit_code"],
                "conformance": element["conformance"]["level"],
                "cardinality": element["cardinality"],
            },
            "description": short_definition,
        }


class Codelists:
    """Value codelists as $defs: Code_<Name> and Coded_<Name>Slot."""

    def __init__(self, codelists: list[JsonObject]) -> None:
        self.by_name: dict[str, JsonObject] = {}
        for codelist in codelists:
            if codelist["role"] != "data_element_value_codelist":
                continue
            if codelist["extensible"] != "No":
                raise ValueError(f"extensible codelist: {codelist['codelist_name']}")
            self.by_name[self.short_name(codelist["codelist_name"])] = codelist

    @staticmethod
    def short_name(codelist_name: str) -> str:
        return pascal_case(re.sub(r" (Response )?Terminology$", "", codelist_name))

    def code_def(self, short_name: str) -> JsonObject:
        codelist = self.by_name[short_name]
        terms = []
        for term in codelist["terms"]:
            terms.append(
                {
                    "properties": {
                        "code": {"const": term["ncit_code"]},
                        "decode": {"const": term["submission_value"]},
                    },
                    "required": ["code", "decode"],
                }
            )
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "pattern": NCIT_CODE_PATTERN},
                "decode": {"type": "string"},
            },
            "required": ["code", "decode"],
            "oneOf": terms,
            "x-codelist": {
                "ncit_code": codelist["codelist_ncit_code"],
                "name": codelist["codelist_name"],
            },
        }

    def defs(self) -> JsonObject:
        result: JsonObject = {}
        for short_name in self.by_name:
            result[f"Code_{short_name}"] = self.code_def(short_name)
            result[f"Coded_{short_name}Slot"] = slot_def(ref(f"Code_{short_name}"))
        return result

    def slot(self, short_name: str) -> str:
        if short_name not in self.by_name:
            raise KeyError(f"unknown codelist {short_name}")
        return f"Coded_{short_name}Slot"


# ---------------------------------------------------------------------------
# Custody, slots and content blocks
# ---------------------------------------------------------------------------


def review_clears_flag() -> JsonObject:
    """Matches a custody whose review was accepted or edited by a person."""
    return {
        "properties": {
            "review": {
                "properties": {"status": {"enum": REVIEW_CLEARING_STATUSES}},
                "required": ["status"],
            }
        },
        "required": ["review"],
    }


def review_status_in(statuses: list[str]) -> JsonObject:
    return {
        "properties": {
            "review": {"properties": {"status": {"enum": statuses}}, "required": ["status"]}
        },
        "required": ["review"],
    }


def custody_defs() -> JsonObject:
    ai_without_acceptance = {
        "properties": {"origin": {"enum": AI_ORIGINS}},
        "required": ["origin"],
        "not": review_clears_flag(),
    }
    low_without_acceptance = {
        **property_is("confidence", "low"),
        "not": review_clears_flag(),
    }
    needs_review = {"properties": {"needs_review": {"const": True}}, "required": ["needs_review"]}
    custody = strict_object(
        {
            "origin": {"enum": CUSTODY_ORIGINS},
            "v10_sources": array_of(ref("V10Source")),
            "actor": ref("Actor"),
            "confidence": {"enum": ["high", "medium", "low"]},
            "needs_review": {"type": "boolean"},
            "review": ref("Review"),
            "edited_from": strict_object(
                {"origin": {"enum": CUSTODY_ORIGINS}, "actor": ref("Actor")}
            ),
            "note": {"type": "string"},
        },
        ["origin", "v10_sources", "actor", "confidence", "needs_review"],
    )
    custody["description"] = (
        "Chain of custody: where a value or block came from. Never record a v10 origin "
        "without a resolvable source_index. If you cannot tell whether text came from v10, "
        "it is ai_generated (or ai_transformed with the v10 sources you do know); when in "
        "doubt, attribute to AI. template_text is only for headings and fixed sentences "
        "copied from the v11 template, never for a decision such as Not applicable. "
        "edited_from holds the origin and actor a reviewer replaced when editing."
    )
    custody["allOf"] = [
        when(
            property_in("origin", ORIGINS_NEEDING_V10_SOURCE),
            {"properties": {"v10_sources": {"minItems": 1}}},
        ),
        when(
            property_in("origin", ORIGINS_WITHOUT_V10_SOURCE),
            {"properties": {"v10_sources": {"maxItems": 0}}},
        ),
        when(
            property_in("origin", AI_ORIGINS),
            {
                "properties": {
                    "actor": {**property_is("kind", "llm"), "required": ["kind", "model"]}
                }
            },
        ),
        when(
            property_is("origin", "v10_derived"),
            {"properties": {"actor": property_is("kind", "script")}},
        ),
        when(
            property_is("origin", "template_text"),
            {"properties": {"actor": property_is("kind", "script")}},
        ),
        when(
            property_is("origin", "human_entered"),
            {"properties": {"actor": property_is("kind", "human")}},
        ),
        when(ai_without_acceptance, needs_review),
        when(low_without_acceptance, needs_review),
        when(review_status_in(REVIEW_OPEN_STATUSES), needs_review),
        when(
            review_status_in(["edited"]),
            {"properties": {"origin": {"const": "human_entered"}}, "required": ["edited_from"]},
        ),
        when(
            {"not": property_is("origin", "human_entered")},
            {"not": {"required": ["edited_from"]}},
        ),
    ]
    v10_source = strict_object(
        {
            "section_id": {"type": "string", "pattern": V10_SECTION_ID_PATTERN},
            "found_number": {"type": ["string", "null"]},
            "found_heading": {"type": ["string", "null"]},
            "match": {"enum": V10_MATCH_KINDS},
            "source_indices": array_of({"type": "integer", "minimum": 0}, minItems=1),
            "portion": {"enum": ["all", "part"]},
            "selector": table_selector(),
        },
        ["section_id", "found_number", "found_heading", "match", "source_indices", "portion"],
    )
    v10_source["description"] = (
        "section_id is the TEMPLATE section this content corresponds to, not the number "
        "printed in the file. found_number / found_heading record what the file actually "
        "contains. protocol_added: heading not in the v10 template; section_id is the "
        "nearest preceding template parent id plus .unmapped (v10:body.unmapped for a new H1 "
        "after the last section or before Section 1), never the number printed in the file. "
        "selector narrows a partial table source to rows and columns of the v10 table."
    )
    v10_source["allOf"] = [
        when(
            property_is("match", "protocol_added"),
            {"properties": {"section_id": {"pattern": UNMAPPED_ID_SUFFIX_PATTERN}}},
        )
    ]
    return {
        "Custody": custody,
        "V10Source": v10_source,
        "Actor": strict_object(
            {
                "kind": {"enum": ["script", "llm", "human"]},
                "name": {"type": "string"},
                "version": {"type": "string"},
                "model": {"type": "string"},
            },
            ["kind", "name"],
        ),
        "Review": review_def(),
        "HeadingSource": heading_source_def(),
    }


def table_selector() -> JsonObject:
    index_list = array_of({"type": "integer", "minimum": 0}, minItems=1)
    selector = strict_object(
        {
            "rows": index_list,
            "columns": index_list,
            "row_labels": array_of({"type": "string"}, minItems=1),
        },
        [],
    )
    selector["description"] = "Which part of a v10 table a partial source covers (0-based indices)."
    return selector


def review_def() -> JsonObject:
    review = strict_object(
        {
            "status": {"enum": ["unreviewed", "accepted", "edited", "rejected"]},
            "by": {"type": "string"},
            "at": {"type": "string", "format": "date-time"},
            "note": {"type": "string"},
        },
        ["status"],
    )
    review["allOf"] = [
        when(
            property_in("status", REVIEW_SIGNED_STATUSES),
            {"required": ["by", "at"]},
        )
    ]
    return review


def heading_source_def() -> JsonObject:
    definition = {
        "allOf": [ref("V10Source"), {"properties": {"portion": {"const": "all"}}}],
        "description": (
            "The v10 heading a section was matched to, whatever its status (container, "
            "missing and not_applicable included). Independent of custody, which describes "
            "the content only."
        ),
    }
    return definition


def heading_source_property() -> JsonObject:
    return {"anyOf": [ref("HeadingSource"), {"type": "null"}]}


def not_a_decision_origin() -> JsonObject:
    return {
        "properties": {
            "custody": {
                "properties": {"origin": {"not": {"enum": ORIGINS_WITHOUT_DECISION}}},
                "required": ["origin"],
            }
        },
        "required": ["custody"],
    }


def unsure_without_value_rule() -> JsonObject:
    """An unsure slot with no candidate value may only claim v10 derivation if it quotes the text."""
    return {
        "properties": {
            "custody": {
                "properties": {"origin": {"enum": ["none", "v10_derived"]}},
                "required": ["origin"],
            }
        },
        "required": ["custody"],
        "if": {"properties": {"custody": property_is("origin", "v10_derived")}, "required": ["custody"]},
        "then": {"required": ["raw_text"]},
    }


def slot_rules_def() -> JsonObject:
    needs_review = {"properties": {"needs_review": {"const": True}}, "required": ["needs_review"]}
    return {
        "description": "Status rules shared by every slot.",
        "allOf": [
            when(
                property_is("status", "filled"),
                {"properties": {"value": {"not": {"type": "null"}}}},
            ),
            when(
                property_in("status", ["missing", "not_applicable"]),
                {"properties": {"value": {"type": "null"}}},
            ),
            when(
                property_is("status", "missing"),
                {"properties": {"custody": property_is("origin", "none")}},
            ),
            when(
                property_is("status", "unsure"),
                {
                    "properties": {
                        "custody": {
                            "if": {"not": review_clears_flag()},
                            "then": needs_review,
                        }
                    }
                },
            ),
            when(property_is("status", "not_applicable"), not_a_decision_origin()),
            when(
                {
                    "properties": {"status": {"const": "unsure"}, "value": {"type": "null"}},
                    "required": ["status", "value"],
                },
                unsure_without_value_rule(),
            ),
        ],
    }


def slot_def(value_schema: JsonObject) -> JsonObject:
    slot = strict_object(
        {
            "value": {"anyOf": [value_schema, {"type": "null"}]},
            "status": ref("SlotStatus"),
            "custody": ref("Custody"),
            "raw_text": {"type": "string"},
            "note": {"type": "string"},
        },
        ["value", "status", "custody"],
    )
    slot["allOf"] = [ref("SlotRules")]
    return slot


def text_value(max_length: int | None = None) -> JsonObject:
    value: JsonObject = {"type": "string", "minLength": 1}
    if max_length is not None:
        value["maxLength"] = max_length
    return value


def enum_slot_name(name: str) -> str:
    return f"Enum_{name}Slot"


def basic_slot_defs() -> JsonObject:
    date_or_text = {
        "oneOf": [
            strict_object({"date": {"type": "string", "format": "date"}}),
            strict_object({"text": text_value()}),
        ]
    }
    external_code = strict_object(
        {
            "system": {"enum": EXTERNAL_CODE_SYSTEMS},
            "code": text_value(),
            "decode": text_value(),
        }
    )
    duration = strict_object(
        {"quantity": {"type": "integer", "minimum": 0}, "unit": ref("Code_UnitOfMeasure")}
    )
    return {
        "SlotStatus": {"enum": SLOT_STATUSES},
        "SlotRules": slot_rules_def(),
        "TextSlot": slot_def(text_value()),
        f"TextSlot_Max{ACRONYM_MAX_LENGTH}": slot_def(text_value(ACRONYM_MAX_LENGTH)),
        f"TextSlot_Max{SHORT_TITLE_MAX_LENGTH}": slot_def(text_value(SHORT_TITLE_MAX_LENGTH)),
        "IntegerSlot": slot_def({"type": "integer", "minimum": 0}),
        "NumberSlot": slot_def({"type": "number", "minimum": 0}),
        "DateSlot": slot_def({"type": "string", "format": "date"}),
        "DateOrTextSlot": slot_def(date_or_text),
        "ExternalCodeSlot": slot_def(external_code),
        "DurationSlot": slot_def(duration),
    }


ENUM_SLOTS = {
    "TargetMaximum": ["Target", "Maximum"],
    "ParticipantNumberBasis": ["randomly assigned to trial intervention", "enrolled"],
    "EnrollmentUnit": ["number", "percent"],
}


def enum_slot_defs() -> JsonObject:
    return {
        enum_slot_name(name): slot_def({"enum": values}) for name, values in ENUM_SLOTS.items()
    }


def block_defs() -> JsonObject:
    custody_ref = ref("Custody")
    needs_index = ref("VerbatimNeedsSourceIndex")
    source_index = {"type": "integer", "minimum": 0}
    part = {
        "type": "string",
        "description": (
            "The M11 element or v11 sub-label this block belongs to (see x-parts on the "
            "section). A sub-label printed as a heading is a Heading block with origin "
            "template_text."
        ),
    }
    paragraph = strict_object(
        {
            "type": {"const": "paragraph"},
            "text": {"type": "string"},
            "style": {"type": "string"},
            "source_index": source_index,
            "has_image": {"type": "boolean"},
            "strip_prefix_chars": {"type": "integer", "minimum": 0},
            "part": part,
            "custody": custody_ref,
        },
        ["type", "text", "style", "custody"],
    )
    paragraph["description"] = (
        "style is the Word style name as found; for AI text use the v11 body style Normal. "
        "strip_prefix_chars is the number of leading characters of the v10 paragraph that are "
        "a label and are removed when copying; text is the paragraph text after removal."
    )
    table = strict_object(
        {
            "type": {"const": "table"},
            "rows": array_of(array_of({"type": "string"})),
            "source_index": source_index,
            "part": part,
            "custody": custody_ref,
        },
        ["type", "rows", "custody"],
    )
    heading = strict_object(
        {
            "type": {"const": "heading"},
            "text": {"type": "string"},
            "level": {"type": "integer", "minimum": 1, "maximum": MAX_HEADING_LEVEL},
            "source_index": source_index,
            "part": part,
            "custody": custody_ref,
        },
        ["type", "text", "level", "custody"],
    )
    heading["description"] = (
        "A v10 sub-heading with no v11 heading of its own, carried inside the v11 section."
    )
    for block in (paragraph, table, heading):
        block["allOf"] = [needs_index]
    for block in (table, heading):
        block["allOf"].append(ref("VerbatimSourcesAreWhole"))
    paragraph["allOf"].append(ref("StrippedPrefixIsPartialVerbatim"))
    paragraph["allOf"].append(
        when(
            {"not": {"properties": {"strip_prefix_chars": {"minimum": 1}}, "required": ["strip_prefix_chars"]}},
            ref("VerbatimSourcesAreWhole"),
        )
    )
    return {
        "VerbatimSourcesAreWhole": when(
            {
                "properties": {"custody": property_is("origin", "v10_verbatim")},
                "required": ["custody"],
            },
            {
                "properties": {
                    "custody": {
                        "properties": {
                            "v10_sources": {
                                "items": {"properties": {"portion": {"const": "all"}}}
                            }
                        }
                    }
                }
            },
        ),
        "VerbatimNeedsSourceIndex": when(
            {
                "properties": {"custody": property_is("origin", "v10_verbatim")},
                "required": ["custody"],
            },
            {"required": ["source_index"]},
        ),
        "StrippedPrefixIsPartialVerbatim": when(
            {"properties": {"strip_prefix_chars": {"minimum": 1}}, "required": ["strip_prefix_chars"]},
            {
                "properties": {
                    "custody": {
                        "properties": {
                            "origin": {"const": "v10_verbatim"},
                            "v10_sources": {
                                "contains": {
                                    "properties": {"portion": {"const": "part"}},
                                    "required": ["portion"],
                                }
                            },
                        },
                        "required": ["origin", "v10_sources"],
                    }
                },
                "required": ["custody"],
            },
        ),
        "Paragraph": paragraph,
        "Table": table,
        "Heading": heading,
        "Block": {"oneOf": [ref("Paragraph"), ref("Table"), ref("Heading")]},
    }


# ---------------------------------------------------------------------------
# Crosswalk: v10 default sources
# ---------------------------------------------------------------------------


class CrosswalkSources:
    def __init__(self, crosswalk: JsonObject, v10_ids: JsonObject) -> None:
        self.rows: list[JsonObject] = crosswalk["rows"]
        self.rows_by_number = {row["v10_number"]: row for row in self.rows}
        self.no_source: dict[str, JsonObject] = {
            entry["number"]: entry for entry in crosswalk["v11_without_v10_source"]
        }
        self.id_titles = {entry["id"]: entry["title"] for entry in v10_ids["ids"]}
        self.title_page_ids = [
            entry["id"] for entry in v10_ids["ids"] if entry["kind"] == "title_page_field"
        ]
        self.synopsis_ids = [
            entry["id"] for entry in v10_ids["ids"] if entry["kind"] == "synopsis_field"
        ]
        self.row_ids = {row["v10_number"]: self.map_row_to_ids(row) for row in self.rows}
        self.check_reserved_ids(v10_ids["reserved_ids"])

    def check_reserved_ids(self, reserved_ids: JsonObject) -> None:
        pattern = re.compile(V10_SECTION_ID_PATTERN)
        for reserved in reserved_ids:
            if not pattern.match(reserved):
                data_notes.append(
                    f"reserved id {reserved} (v10_section_ids.json) does not match the spec's "
                    "section_id pattern, so it cannot be used in v10_sources"
                )

    def map_row_to_ids(self, row: JsonObject) -> list[str | None]:
        number = row["v10_number"]
        if NUMBERED_ROW.match(number):
            ids = [f"v10:{number}"]
        elif number == "FM":
            return []
        elif number in UNNUMBERED_ROW_IDS:
            ids = UNNUMBERED_ROW_IDS[number]
        elif number.startswith(TITLE_PAGE_ROW_PREFIX):
            ids = [self.title_page_ids[self.row_index(number, TITLE_PAGE_ROW_PREFIX)]]
        elif number.startswith(SYNOPSIS_ROW_PREFIX):
            ids = [self.synopsis_ids[self.row_index(number, SYNOPSIS_ROW_PREFIX)]]
        else:
            raise ValueError(f"no v10 id mapping for crosswalk row {number}")
        for v10_id in ids:
            self.verify_title(row, v10_id)
        return list(ids)

    @staticmethod
    def row_index(number: str, prefix: str) -> int:
        return int(number.removeprefix(prefix)) - 1

    def verify_title(self, row: JsonObject, v10_id: str) -> None:
        if v10_id not in self.id_titles:
            raise ValueError(f"crosswalk row {row['v10_number']} maps to unknown id {v10_id}")
        number = row["v10_number"]
        row_title = normalize_title(row["v10_title"])
        id_title = normalize_title(self.id_titles[v10_id])
        fragment = ROW_TITLE_FRAGMENTS.get(number)
        if fragment is not None:
            agrees = fragment in row_title
        elif NUMBERED_ROW.match(number):
            agrees = id_title in row_title or row_title in id_title
        else:
            agrees = id_title in row_title
        if not agrees:
            data_notes.append(
                f"title mismatch: crosswalk row {number} '{row['v10_title']}' vs {v10_id} "
                f"'{self.id_titles[v10_id]}'"
            )

    def entries_for_row(self, row: JsonObject, target: JsonObject) -> list[JsonObject]:
        entries = []
        for v10_id in self.row_ids[row["v10_number"]]:
            entry: JsonObject = {
                "section_id": v10_id,
                "v10_title": row["v10_title"],
                "portion": target["portion"],
                "change_type": row["change_type"],
                "confidence": row["confidence"],
                "evidence": row["evidence"],
            }
            if "part" in target:
                entry["part"] = target["part"]
            entries.append(entry)
        return entries

    def sources_for_number(self, number: str, aliases: dict[str, str]) -> list[JsonObject]:
        entries: list[JsonObject] = []
        for row in self.rows:
            for target in row["v11_targets"]:
                if aliases.get(target["number"], target["number"]) == number:
                    entries.extend(self.entries_for_row(row, target))
        return entries

    def sources_for_part(
        self, parent_number: str, part_title: str, aliases: dict[str, str]
    ) -> list[JsonObject]:
        """Sources whose target names this unnumbered child as the part of its parent."""
        wanted = normalize_title(part_title)
        entries: list[JsonObject] = []
        for row in self.rows:
            for target in row["v11_targets"]:
                resolved = aliases.get(target["number"], target["number"])
                is_part = normalize_title(target.get("part", "")) == wanted
                if resolved == parent_number and is_part:
                    entries.extend(self.entries_for_row(row, target))
        return entries

    def derived_original_protocol_entry(self) -> JsonObject:
        row = self.rows_by_number[ORIGINAL_PROTOCOL_ROW]
        return {
            "section_id": AMENDMENT_NUMBER_ID,
            "v10_title": self.id_titles[AMENDMENT_NUMBER_ID],
            "portion": "all",
            "change_type": "derived",
            "confidence": "inferred-strong",
            "evidence": row["note"],
        }

    def sources_for_rows(self, row_numbers: list[str]) -> list[JsonObject]:
        entries: list[JsonObject] = []
        for number in row_numbers:
            row = self.rows_by_number[number]
            for target in row["v11_targets"]:
                entries.extend(self.entries_for_row(row, target))
        return entries


# ---------------------------------------------------------------------------
# Typed field definitions for sections, title page and amendment details
# ---------------------------------------------------------------------------


class FieldFactory:
    def __init__(self, m11: M11Index, codelists: Codelists) -> None:
        self.m11 = m11
        self.codelists = codelists

    def slot(
        self,
        def_name: str,
        code: str | None = None,
        section: str | None = None,
        element_id: int | None = None,
    ) -> JsonObject:
        node = ref(def_name)
        if code is not None or element_id is not None:
            node.update(self.m11.annotations(code, section, element_id))
        return node

    def text(
        self, code: str | None = None, section: str | None = None, element_id: int | None = None
    ) -> JsonObject:
        return self.slot("TextSlot", code, section, element_id)

    def coded(
        self,
        short_name: str,
        code: str | None = None,
        section: str | None = None,
        element_id: int | None = None,
    ) -> JsonObject:
        return self.slot(self.codelists.slot(short_name), code, section, element_id)

    def enum(self, name: str, code: str, section: str) -> JsonObject:
        return self.slot(enum_slot_name(name), code, section)

    def many(self, item: JsonObject, required: bool = False) -> JsonObject:
        """An array of slots; `required` is for M11 Required elements, which need one slot."""
        annotations = {key: item[key] for key in ("x-m11", "description") if key in item}
        plain_item = {key: value for key, value in item.items() if key not in annotations}
        if required:
            annotations["minItems"] = 1
        return array_of(plain_item, **annotations)

    def overall_design_fields(self) -> JsonObject:
        section = "1.1.2"
        no_yes = "NoYes"
        age = strict_object(
            {
                "minimum": self.slot("IntegerSlot", element_id=112),
                "minimum_unit": self.coded("UnitOfMeasure", element_id=113),
                "maximum": self.slot("IntegerSlot", element_id=115),
                "maximum_unit": self.coded("UnitOfMeasure", element_id=116),
            }
        )
        age["description"] = "Participant age range (C49693 minimum, C49694 maximum, units C50400)."
        return strict_object(
            {
                "intervention_codes": self.many(self.text("C218675", section)),
                "intervention_nonproprietary_names": self.many(self.text("C97054", section)),
                "intervention_model": self.coded("InterventionModel", "C98746", section),
                "population_type": self.coded("PopulationType", "C218703", section),
                "population_diagnosis_or_condition": self.many(
                    self.slot("ExternalCodeSlot", "C112038", section), required=True
                ),
                "control_type": self.coded("ControlType", "C49647", section),
                "control_description": self.text(element_id=106),
                "control_nonproprietary_names": self.many(
                    self.slot("ExternalCodeSlot", element_id=107)
                ),
                "control_inns": self.many(self.slot("ExternalCodeSlot", element_id=108)),
                "control_description_not_applicable": self.text(element_id=109),
                "population_age": age,
                "intervention_assignment_method": self.coded(
                    "TrialInterventionAssignmentMethod", "C218475", section
                ),
                "randomisation_type": self.text("C223137", section),
                "other_assignment_method": self.text("C223138", section),
                "stratification": self.coded(no_yes, "C223136", section),
                "site_distribution": self.coded("TrialSiteDistribution", "C218704", section),
                "site_geographic_scope": self.coded("TrialSiteGeographicScope", "C218705", section),
                "master_protocol": self.coded(no_yes, "C218707", section),
                "drug_device_combination_product": self.coded(no_yes, "C218708", section),
                "adaptive_trial_design": self.coded(no_yes, "C218706", section),
                "number_of_arms": self.slot("IntegerSlot", "C98771", section),
                "trial_blind_schema": self.coded("TrialBlindingSchema", "C49658", section),
                "blinded_roles": self.many(
                    self.coded("TrialBlindingRole", "C218709", section), required=True
                ),
                "participant_number_qualifier": self.enum("TargetMaximum", "C218710", section),
                "number_of_participants": self.slot("IntegerSlot", "C49692", section),
                "participant_number_basis": self.enum("ParticipantNumberBasis", "C218711", section),
                "intervention_duration": self.slot("DurationSlot", "C218712", section),
                "intervention_duration_description": self.text("C218714", section),
                "participation_duration": self.slot("DurationSlot", "C218715", section),
                "participation_duration_description": self.text("C218717", section),
                "additional_duration_description": self.text("C218838", section),
                "independent_committees": self.many(
                    self.coded("IndependentCommitteeName", "C218718", section), required=True
                ),
                "other_committees": self.text("C218719", section),
            }
        )

    def summary_rows_fields(self) -> JsonObject:
        row = strict_object(
            {
                "objective_id": {"type": "string", "pattern": r"^(primary|secondary)-\d+$"},
                "objective": self.text(),
                "estimand_summary": self.text(),
            }
        )
        rows = array_of(row)
        rows.update(self.m11.annotations("C218839", "1.1.1"))
        return strict_object({"summary_rows": rows})

    def criteria_fields(self, code: str, section: str) -> JsonObject:
        criterion = strict_object(
            {"number": {"type": ["integer", "null"], "minimum": 1}, "text": self.text(code, section)}
        )
        return strict_object({"criteria": array_of(criterion)})

    def intervention_fields(self) -> JsonObject:
        section = "6"
        intervention = strict_object(
            {
                "arm_name": self.text("C93729", section),
                "arm_type": self.coded("TrialArmType", "C172457", section),
                "intervention_name": self.text("C177930", section),
                "intervention_type": self.coded("InterventionType", "C98747", section),
                "dose_form": self.slot("ExternalCodeSlot", "C42636", section),
                "dosage_strengths": self.text("C142517", section),
                "dosage_levels": self.text("C94394", section),
                "route": self.slot("ExternalCodeSlot", "C38114", section),
                "regimen": self.text("C15697", section),
                "use": self.coded("InterventionUse", "C218748", section),
                "imp_or_nimp": self.coded("InvestigationalMedicinalProductIndicator", "C218749", section),
                "sourcing": self.many(
                    self.coded("InvestigationalInterventionSourcing", "C218750", section)
                ),
            }
        )
        intervention["description"] = (
            "The v011 table omits Sourcing but its instructions list it; kept here."
        )
        return strict_object(
            {
                "overview": self.text("C218747", section),
                "interventions": array_of(intervention),
            }
        )

    def safety_reporting_fields(self) -> JsonObject:
        section = "9.2"
        event = strict_object(
            {
                "event_type": self.coded("TrialRelatedSafetyEventType", "C218784", section),
                "other_event_type": self.text("C222263", section),
                "situational_scope": self.text("C218785", section),
                "reportable_period_start": self.text("C218786", section),
                "reportable_period_end": self.text("C218787", section),
                "timing_for_reporting": self.text("C218788", section),
                "reporting_method": self.text("C218789", section),
                "backup_reporting_method": self.text("C218790", section),
            }
        )
        return strict_object({"safety_event_reporting": array_of(event)})

    def country_region_fields(self) -> JsonObject:
        section = "12.2"
        country_region = strict_object(
            {
                "identifier": self.slot("ExternalCodeSlot", "C218832", section),
                "requirements": self.block_list("C218833", section),
                "clarifications": self.block_list("C218834", section),
                "custody": ref("Custody"),
            }
        )
        country_region["description"] = (
            "One entry per country or region. The list is empty when section 12.2 is "
            "not_applicable or missing."
        )
        return strict_object({"country_regions": array_of(country_region)})

    def block_list(self, code: str, section: str) -> JsonObject:
        return array_of(ref("Block"), **self.m11.annotations(code, section))

    def prior_amendment_fields(self) -> JsonObject:
        section = "12.3"
        prior_amendment = strict_object(
            {
                "amendment_identifier": self.text(element_id=561),
                "sponsor_approval_date": self.slot("DateSlot", element_id=562),
                "approximate_enrollment": self.text("C218478", section),
                "enrollment_scope": self.coded(
                    "AmendmentScopeEnrollmentDescription", "C218695", section
                ),
                "changes": array_of(ref("AmendmentChange")),
            }
        )
        return strict_object(
            {
                "amendment_statement": self.coded("AmendmentDetailsStatement", "C218835", section),
                "overview_location": self.text("C218836", section),
                "prior_amendments": array_of(prior_amendment),
                "original_protocol_approval_date": self.slot("DateSlot", element_id=555),
            }
        )

    def amendment_change_def(self) -> JsonObject:
        section = "Amendment Details"
        return strict_object(
            {
                "description": self.text("C218483", section),
                "rationale": self.text("C181233", section),
                "section": self.coded("ProtocolSectionNameAndNumber", "C218479", section),
            }
        )

    def amendment_details_def(self, crosswalk: CrosswalkSources) -> JsonObject:
        section = "Amendment Details"
        no_yes = "NoYes"
        reason = "ReasonForAmendment"
        approximate = strict_object(
            {
                "quantity": self.slot("NumberSlot", "C218478", section),
                "unit": self.slot(enum_slot_name("EnrollmentUnit")),
            }
        )
        approximate["allOf"] = [
            when(
                unit_value_is("percent"),
                {"properties": {"quantity": {"properties": {"value": {"maximum": MAX_PERCENT}}}}},
            ),
            when(
                unit_value_is("number"),
                {"properties": {"quantity": {"properties": {"value": {"type": ["integer", "null"]}}}}},
            ),
        ]
        approximate["description"] = (
            "Approximately <#/%> enrolled (C218478). A number is an integer; a percent is at "
            "most 100 with one decimal (the decimal is checked by validate_protocol.py)."
        )
        details = strict_object(
            {
                "statement": self.coded("AmendmentDetailsStatement", "C218694", section),
                "approximate_enrolled": approximate,
                "enrollment_scope": self.coded(
                    "AmendmentScopeEnrollmentDescription", "C218695", section
                ),
                "primary_reasons": self.many(self.coded(reason, "C218696", section)),
                "primary_reason_other": self.text(element_id=66),
                "secondary_reasons": self.many(self.coded(reason, "C218697", section)),
                "secondary_reason_other": self.text(element_id=70),
                "summary": self.text("C42581", section),
                "substantial_impact_on_safety_or_rights": self.coded(no_yes, "C218698", section),
                "safety_or_rights_explanation": self.text("C218699", section),
                "substantial_impact_on_data_reliability": self.coded(no_yes, "C218700", section),
                "data_reliability_explanation": self.text("C218701", section),
                "changes": array_of(ref("AmendmentChange")),
            }
        )
        details["x-v10-default-sources"] = crosswalk.sources_for_rows(AMENDMENT_DETAILS_ROWS)
        properties = details["properties"]
        summary_sources = crosswalk.sources_for_rows([SUMMARY_AND_REASONS_ROW])
        impact_sources = crosswalk.sources_for_rows([SUBSTANTIAL_IMPACT_ROW])
        for name in ("summary", "primary_reasons"):
            properties[name]["x-v10-default-sources"] = summary_sources
        for name in ("substantial_impact_on_safety_or_rights", "substantial_impact_on_data_reliability"):
            properties[name]["x-v10-default-sources"] = impact_sources
        properties["changes"]["x-v10-default-sources"] = crosswalk.sources_for_rows(
            [AMENDMENT_CHANGES_ROW]
        )
        return details


def scope_identifiers_def(fields: FieldFactory) -> JsonObject:
    code_kinds = ["country", "region"]
    identifier = strict_object(
        {
            "kind": {"enum": code_kinds + ["site"]},
            "country_or_region": fields.slot("ExternalCodeSlot"),
            "site": fields.text(),
        },
        ["kind"],
    )
    identifier["allOf"] = [
        when(
            property_in("kind", code_kinds),
            {"required": ["country_or_region"], "not": {"required": ["site"]}},
        ),
        when(
            property_is("kind", "site"),
            {"required": ["site"], "not": {"required": ["country_or_region"]}},
        ),
    ]
    scope_identifiers = array_of(identifier)
    scope_identifiers.update(fields.m11.annotations(element_id=SCOPE_IDENTIFIER_ELEMENT_ID))
    scope_identifiers["description"] = (
        "Empty when amendment_scope is Global. country_or_region is coded (ISO 3166 or GENC), "
        "site is free text."
    )
    return scope_identifiers


def unit_value_is(unit: str) -> JsonObject:
    return {
        "properties": {"unit": {"properties": {"value": {"const": unit}}, "required": ["value"]}},
        "required": ["unit"],
    }


VERSION_NUMBER_ENTRY: JsonObject = {
    "section_id": VERSION_NUMBER_ID,
    "v10_title": "Protocol Version",
    "portion": "all",
    "change_type": "protocol_added",
    "confidence": "inferred-weak",
    "evidence": (
        "A title page field some protocols add (test1). It is not in v10_section_ids.json and "
        "has no crosswalk row."
    ),
}


def title_page_def(
    fields: FieldFactory, crosswalk: CrosswalkSources, no_source_note: JsonObject | None
) -> JsonObject:
    page = "Title Page"
    sponsor_roles = ["sponsor", "co_sponsor", "local_sponsor"]
    sponsor_text = fields.text("C222495", page)
    sponsor_address = fields.text(element_id=SPONSOR_ADDRESS_ELEMENT_ID)
    # The M11 PDF repeats Sponsor Name's code on the address row; the terminology says C218677.
    sponsor_address["x-m11"].update(
        {"ncit_code": SPONSOR_ADDRESS_CODE, "element": "Sponsor Legal Address"}
    )
    properties: JsonObject = {
        "sponsor_confidentiality_statement": fields.text("C181236", page),
        "full_title": fields.text("C132346", page),
        "full_title_components": strict_object(
            {
                "intervention_model": fields.text(),
                "primary_purpose": fields.text(),
                "trial_phase": fields.text(),
                "blinding": fields.text(),
                "number_of_arms": fields.slot("IntegerSlot"),
                "health_measurement_or_outcome": fields.text(),
                "investigational_intervention": fields.text(),
                "intervention_form": fields.text(),
                "participant_sex": fields.text(),
                "participant_age_range": fields.text(),
                "condition_or_disease": fields.text(),
            }
        ),
        "trial_acronym": fields.slot(f"TextSlot_Max{ACRONYM_MAX_LENGTH}", "C94108", page),
        "sponsor_protocol_identifier": fields.text("C132351", page),
        "original_protocol": fields.coded("NoYes", "C218672", page),
        "version_number": fields.text("C181232", page),
        "version_date": fields.slot("DateSlot", "C93813", page),
        "amendment_identifier": fields.text("C218477", page),
        "amendment_scope": fields.coded("AmendmentScope", "C218673", page),
        "amendment_scope_identifiers": scope_identifiers_def(fields),
        "investigational_product_codes": fields.many(fields.text("C218675", page)),
        "investigational_product_names": array_of(
            strict_object(
                {
                    "kind": {"enum": ["nonproprietary", "proprietary"]},
                    "name": fields.text(),
                }
            ),
            description="Nonproprietary C97054, proprietary C71898.",
        ),
        "trial_phase": fields.coded("TrialPhase", "C48281", page),
        "short_title": fields.slot(f"TextSlot_Max{SHORT_TITLE_MAX_LENGTH}", "C94105", page),
        "sponsors": array_of(
            strict_object(
                {
                    "role": {"enum": sponsor_roles},
                    "name": sponsor_text,
                    "legal_address": sponsor_address,
                }
            ),
            minItems=1,
            description=(
                "Exactly one entry has role sponsor (C222495). co_sponsor: C218678/C218679; "
                "local_sponsor: C218680/C218681."
            ),
        ),
        "device_manufacturers": array_of(
            strict_object(
                {
                    "name": fields.text("C218682", page),
                    "legal_address": fields.text("C218683", page),
                }
            )
        ),
        "regulatory_identifiers": array_of(
            strict_object(
                {
                    "type": {"enum": REGULATORY_ID_TYPES, "description": REGULATORY_ID_CODES_NOTE},
                    "label": {"type": "string"},
                    "id": fields.text(),
                }
            )
        ),
        "sponsor_approval": {
            **fields.slot("DateOrTextSlot", "C132352", page),
            "description": (
                "Sponsor Approval Date, or a statement of where it is located. The M11 PDF "
                "marks this element repeatable; it is kept single here."
            ),
        },
        "sponsor_signatories": array_of(
            strict_object(
                {
                    "name": fields.text("C222014", page),
                    "title": fields.text(),
                    "date": fields.slot("DateSlot"),
                    "signature": strict_object(
                        {"kind": {"enum": SIGNATURE_KINDS}, "value": fields.text()}
                    ),
                }
            )
        ),
        "medical_expert_contact": fields.text("C218693", page),
    }
    attach_title_page_sources(properties, crosswalk)
    definition = strict_object(properties)
    if no_source_note is not None:
        definition["x-no-v10-source"] = no_source_note
    return definition


def attach_title_page_sources(properties: JsonObject, crosswalk: CrosswalkSources) -> None:
    for property_name, (v10_keys, target_fragment) in TITLE_PAGE_V10_FIELDS.items():
        entries: list[JsonObject] = []
        for key in v10_keys:
            v10_id = f"v10:title_page.{key}"
            row = next(
                (item for item in crosswalk.rows if v10_id in crosswalk.row_ids[item["v10_number"]]),
                None,
            )
            if row is None:
                data_notes.append(f"no crosswalk row for {v10_id} (title page {property_name})")
                continue
            target = row["v11_targets"][0]
            if target_fragment not in target["title"].lower():
                data_notes.append(
                    f"title page {property_name}: spec maps {v10_id} but crosswalk row "
                    f"{row['v10_number']} targets '{target['title']}'"
                )
            entries.extend(crosswalk.entries_for_row(row, target))
        properties[property_name]["x-v10-default-sources"] = entries
    properties["investigational_product_names"]["x-v10-default-sources"] = (
        crosswalk.sources_for_rows(PRODUCT_NAMES_ROWS)
    )
    properties["original_protocol"]["x-v10-default-sources"] = [
        crosswalk.derived_original_protocol_entry()
    ]
    properties["version_number"]["x-v10-default-sources"] = [VERSION_NUMBER_ENTRY]


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def section_status_def() -> JsonObject:
    return {"enum": ["populated", "not_applicable", "omitted", "missing", "container"]}


def additional_subsection_def() -> JsonObject:
    definition = strict_object(
        {
            "number": {"type": ["string", "null"]},
            "title": {"type": "string", "minLength": 1},
            "level": {"type": "integer", "minimum": 1, "maximum": MAX_HEADING_LEVEL},
            "status": {"enum": ["populated", "container"]},
            "heading_source": {
                "allOf": [ref("HeadingSource"), property_is("match", "protocol_added")]
            },
            "custody": ref("Custody"),
            "content": array_of(ref("Block")),
            "subsections": array_of(ref("AdditionalSubsection")),
        }
    )
    definition["allOf"] = [ref("SectionStatusRules")]
    definition["description"] = (
        "A heading the protocol added that has no v11 heading: level 3 and deeper headings in "
        "any section, and additional appendices in section 12 (12.5 and up, M11 12.X). "
        "number is the number printed in the protocol, or null."
    )
    return definition


def section_status_rules_def() -> JsonObject:
    return {
        "description": (
            "A section without text status carries no content blocks. Not applicable is a "
            "decision, so its custody is never template_text or none."
        ),
        "allOf": [
            when(
                property_in("status", ["not_applicable", "omitted", "missing", "container"]),
                {"properties": {"content": {"maxItems": 0}}},
            ),
            when(property_is("status", "not_applicable"), not_a_decision_origin()),
        ],
    }


class SectionBuilder:
    def __init__(
        self,
        inventory: list[JsonObject],
        crosswalk: CrosswalkSources,
        fields: FieldFactory,
    ) -> None:
        self.crosswalk = crosswalk
        self.fields = fields
        self.defs: JsonObject = {}
        self.body = [node for node in inventory if node["group"] == "body"]
        self.nodes = [node for node in self.body if self.is_section_node(node)]
        self.by_key = {self.key_of(node): node for node in self.nodes}
        self.aliases = self.find_aliases()
        self.field_defs = {
            "1.1.1": ("Fields_1_1_1", fields.summary_rows_fields()),
            "1.1.2": ("Fields_1_1_2", fields.overall_design_fields()),
            "5.2": ("Fields_5_2", fields.criteria_fields("C25532", "5.2")),
            "5.3": ("Fields_5_3", fields.criteria_fields("C25370", "5.3")),
            "6": ("Fields_6", fields.intervention_fields()),
            "9.2": ("Fields_9_2", fields.safety_reporting_fields()),
            "12.2": ("Fields_12_2", fields.country_region_fields()),
            "12.3": ("Fields_12_3", fields.prior_amendment_fields()),
        }

    @staticmethod
    def is_section_node(node: JsonObject) -> bool:
        number = node["number"]
        if node.get("parent_number") == FIELD_ONLY_PARENT:
            return False
        if number is None:
            return True
        return not any(number == root or number.startswith(root + ".") for root in REPEATING_ROOTS)

    @staticmethod
    def key_of(node: JsonObject) -> str:
        if node["number"] is not None:
            return node["number"]
        return f"{node['parent_number']}#{snake_case(node['title'])}"

    @staticmethod
    def def_name(key: str) -> str:
        return "Section_" + key.replace(".", "_").replace("#", "__")

    def find_aliases(self) -> dict[str, str]:
        """Map crosswalk targets that are not inventory numbers to the node with that title."""
        aliases: dict[str, str] = {}
        known = {node["number"] for node in self.body if node["number"]}
        for row in self.crosswalk.rows:
            for target in row["v11_targets"]:
                number = target["number"]
                if number in known or number == "title-page" or number in aliases:
                    continue
                wanted = normalize_title(target["title"])
                matches = [
                    self.key_of(node)
                    for node in self.nodes
                    if normalize_title(node["title"]) == wanted
                ]
                if len(matches) != 1:
                    raise ValueError(f"crosswalk target {number} '{target['title']}' unresolved")
                aliases[number] = matches[0]
                data_notes.append(
                    f"crosswalk target {number} '{target['title']}' not in inventory "
                    f"({UNNUMBERED_ALIASES_NOTE}); attached to {matches[0]}"
                )
        return aliases

    def children_of(self, key: str) -> list[JsonObject]:
        children = []
        for node in self.nodes:
            number = node["number"]
            if number is None:
                is_child = node["parent_number"] == key
            else:
                is_child = number.rpartition(".")[0] == key
            if is_child:
                children.append(node)
        return children

    def parent_of(self, node: JsonObject) -> JsonObject | None:
        if node["number"] is None:
            parent_key = node["parent_number"]
        else:
            parent_key = node["number"].rpartition(".")[0]
        return self.by_key.get(parent_key)

    def in_optional_tree(self, node: JsonObject) -> bool:
        current: JsonObject | None = node
        while current is not None:
            if current["optional"]:
                return True
            current = self.parent_of(current)
        return False

    def allowed_statuses(self, node: JsonObject) -> list[str]:
        expected = node["text_expected"]["value"]
        if expected == "none":
            statuses = ["container"]
        elif expected == "none_unless_not_applicable":
            statuses = ["container", "not_applicable"]
        elif expected == "text":
            statuses = ["populated", "not_applicable", "missing"]
        else:
            raise ValueError(f"unhandled text_expected {expected} on {node['title']}")
        if self.in_optional_tree(node):
            statuses.append("omitted")
        return statuses

    def omission_rules(self, children: list[JsonObject]) -> list[JsonObject]:
        """An omitted section omits all children; a kept one keeps its non-optional children."""
        omitted = property_is("status", "omitted")
        child_status = lambda child, condition: {
            self.key_of(child): {"properties": {"status": condition}}
        }
        omit_all: JsonObject = {}
        keep_required: JsonObject = {}
        for child in children:
            omit_all.update(child_status(child, {"const": "omitted"}))
            if not child["optional"]:
                keep_required.update(child_status(child, {"not": {"const": "omitted"}}))
        rules = [when(omitted, {"properties": {"subsections": {"properties": omit_all}}})]
        if keep_required:
            rules.append(
                when(
                    {"not": omitted},
                    {"properties": {"subsections": {"properties": keep_required}}},
                )
            )
        return rules

    def default_sources(self, node: JsonObject, key: str) -> list[JsonObject]:
        sources = self.crosswalk.sources_for_number(key, self.aliases)
        if node["number"] is None:
            sources += self.crosswalk.sources_for_part(
                node["parent_number"], node["title"], self.aliases
            )
        return sources

    def annotations(self, node: JsonObject, key: str) -> JsonObject:
        result: JsonObject = {
            "description": f"v11 section {key}: {node['title']}",
            "x-v11": {
                "style": node["style"],
                "optional": node["optional"],
                "optional_basis": node["optional_basis"],
                "required": node.get("required"),
                "required_basis": node.get("required_basis"),
                "text_expected": node["text_expected"],
                "not_applicable_rule": node["not_applicable_rule"],
                "heading_policy": node.get("heading_policy"),
            },
            "x-guidance": node["key_instructions"],
            "x-m11": node["m11"],
            "x-v10-default-sources": self.default_sources(node, key),
        }
        if key in self.crosswalk.no_source:
            entry = self.crosswalk.no_source[key]
            result["x-no-v10-source"] = {k: v for k, v in entry.items() if k != "number"}
        return result

    @staticmethod
    def title_property(node: JsonObject) -> JsonObject:
        title = node["title"]
        if node["number"] is not None:
            return {"type": "string", "minLength": 1, "default": title}
        if PLACEHOLDER.search(title):
            return {"type": "string", "minLength": 1, "x-title-template": title}
        return {"const": title}

    def section_def(self, node: JsonObject) -> JsonObject:
        key = self.key_of(node)
        number = node["number"]
        children = self.children_of(key)
        properties: JsonObject = {
            "number": {"const": number},
            "title": self.title_property(node),
            "status": {"enum": self.allowed_statuses(node)},
            "heading_source": heading_source_property(),
            "custody": ref("Custody"),
            "content": array_of(ref("Block")),
            "additional_subsections": array_of(ref("AdditionalSubsection")),
        }
        if children:
            properties["subsections"] = strict_object(
                {self.key_of(child): ref(self.def_name(self.key_of(child))) for child in children}
            )
        if key in self.field_defs:
            properties["fields"] = ref(self.field_defs[key][0])
        for kind, info in OBJECTIVE_KINDS.items():
            if info["section"] == key:
                kwargs = {"minItems": 1} if kind == "primary" else {}
                properties["objectives"] = array_of(ref(f"Objective_{kind.title()}"), **kwargs)
        for kind, analysis_key in ANALYSIS_SECTIONS.items():
            if analysis_key == key:
                kwargs = {"minItems": 1} if kind == "primary" else {}
                properties["objective_analyses"] = array_of(
                    ref(f"ObjectiveAnalysis_{analysis_key.replace('.', '_')}"), **kwargs
                )
        definition = strict_object(properties)
        definition["allOf"] = [ref("SectionStatusRules")]
        if children:
            definition["allOf"] += self.omission_rules(children)
        definition["allOf"] += self.repeated_item_rules(key)
        definition.update(self.annotations(node, key))
        definition["x-parts"] = self.fields.m11.parts_of_section(key)
        return definition

    @staticmethod
    def repeated_item_rules(key: str) -> list[JsonObject]:
        """container needs at least one objective or analysis; not_applicable needs none."""
        rules: list[JsonObject] = []
        list_names = [
            "objectives" for info in OBJECTIVE_KINDS.values() if info["section"] == key
        ] + ["objective_analyses" for analysis_key in ANALYSIS_SECTIONS.values() if analysis_key == key]
        for list_name in list_names:
            rules.append(
                when(
                    property_is("status", "container"),
                    {"properties": {list_name: {"minItems": 1}}},
                )
            )
            rules.append(
                when(
                    property_is("status", "not_applicable"),
                    {"properties": {list_name: {"maxItems": 0}}},
                )
            )
        return rules

    def build(self) -> JsonObject:
        for node in self.nodes:
            self.defs[self.def_name(self.key_of(node))] = self.section_def(node)
        for def_name, definition in self.field_defs.values():
            self.defs[def_name] = definition
        return self.defs


# ---------------------------------------------------------------------------
# Objectives, estimands and per-objective analyses
# ---------------------------------------------------------------------------


class ObjectiveBuilder:
    def __init__(
        self,
        inventory: list[JsonObject],
        crosswalk: CrosswalkSources,
        fields: FieldFactory,
        aliases: dict[str, str],
    ) -> None:
        self.nodes = {node["number"]: node for node in inventory if node["number"]}
        self.crosswalk = crosswalk
        self.fields = fields
        self.aliases = aliases

    def estimand_def(self, kind: str) -> JsonObject:
        elements = f"{OBJECTIVE_KINDS[kind]['section']}.X"
        fields = self.fields
        intercurrent = strict_object(
            {
                "description": fields.text("C188856", elements),
                "strategy": fields.text("C188857", elements),
            }
        )
        return strict_object(
            {
                "id": {"type": "string", "pattern": rf"^{kind}-\d+-estimand-\d+$"},
                "population": fields.text("C70833", elements),
                "treatment": fields.text("C49236", elements),
                "endpoint": fields.text("C25212", elements),
                "population_level_summary": fields.text("C188853", elements),
                "intercurrent_events": array_of(intercurrent),
                "custody": ref("Custody"),
            }
        )

    def objective_def(self, kind: str) -> JsonObject:
        info = OBJECTIVE_KINDS[kind]
        section = info["section"]
        elements = f"{section}.X"
        estimand_kwargs = {"minItems": 1} if kind == "primary" else {}
        attribute = strict_object({"label": {"type": "string"}, "value": self.fields.text()})
        definition = strict_object(
            {
                "id": {"type": "string", "pattern": rf"^{kind}-\d+$"},
                "number": {"type": "string", "pattern": rf"^{re.escape(section)}\.\d+$"},
                "title": {"type": "string"},
                "category_label": self.fields.text(),
                "statement": self.fields.text(info["statement_code"], elements),
                "estimands": array_of(ref(f"Estimand_{kind.title()}"), **estimand_kwargs),
                "additional_attributes": array_of(attribute),
                "content": array_of(ref("Block")),
                "heading_source": heading_source_property(),
                "custody": ref("Custody"),
            }
        )
        definition["description"] = (
            f"{kind.title()} objective (v11 {info['root']}). additional_attributes keeps v10 "
            "columns that have no M11 home. category_label is the v10 category as written, "
            "for example Key secondary."
        )
        definition["x-v10-default-sources"] = self.crosswalk.sources_for_number(
            info["root"], self.aliases
        )
        return definition

    def analysis_subsection_def(self, section: str, node: JsonObject) -> JsonObject:
        statuses = ["populated", "not_applicable", "missing"]
        if node["optional"]:
            statuses.append("omitted")
        number_pattern = rf"^{re.escape(section)}\.\d+\.[1-5]$"
        definition = strict_object(
            {
                "number": {"type": "string", "pattern": number_pattern},
                "title": {"type": "string"},
                "status": {"enum": statuses},
                "heading_source": heading_source_property(),
                "custody": ref("Custody"),
                "content": array_of(ref("Block")),
            }
        )
        definition["allOf"] = [ref("SectionStatusRules")]
        return definition

    def build(self) -> JsonObject:
        defs: JsonObject = {}
        for kind in OBJECTIVE_KINDS:
            defs[f"Estimand_{kind.title()}"] = self.estimand_def(kind)
            defs[f"Objective_{kind.title()}"] = self.objective_def(kind)
        for kind, section in ANALYSIS_SECTIONS.items():
            defs.update(self.analysis_defs(kind, section))
        return defs

    def analysis_defs(self, kind: str, section: str) -> JsonObject:
        section_tag = section.replace(".", "_")
        root = self.nodes[f"{section}.1"]
        child_numbers = [f"{section}.1.{index}" for index in range(1, len(ANALYSIS_SUBSECTIONS) + 1)]
        subsection_properties: JsonObject = {}
        defs: JsonObject = {}
        for name, number in zip(ANALYSIS_SUBSECTIONS, child_numbers, strict=True):
            node = self.nodes[number]
            suffix = "Optional" if node["optional"] else "Required"
            def_name = f"AnalysisSubsection_{section_tag}_{suffix}"
            defs[def_name] = self.analysis_subsection_def(section, node)
            subsection_properties[name] = {
                **ref(def_name),
                "x-template-title": node["title"],
                "x-v10-default-sources": self.crosswalk.sources_for_number(number, self.aliases),
            }
        analysis = strict_object(
            {
                "objective_id": {"type": "string", "pattern": rf"^{kind}-\d+$"},
                "number": {"type": "string", "pattern": rf"^{re.escape(section)}\.\d+$"},
                "title": {"type": "string"},
                "status": {"enum": ["populated", "not_applicable", "missing"]},
                "heading_source": heading_source_property(),
                "custody": ref("Custody"),
                "content": array_of(ref("Block")),
                "subsections": strict_object(subsection_properties),
            }
        )
        analysis["allOf"] = [ref("SectionStatusRules")]
        analysis["description"] = (
            f"Analyses for one {kind} objective (v11 {root['number']}). content is allowed for "
            "introductory text under the objective heading: the inventory's text_expected none "
            "for this node is relaxed because v10 9.3.1 and 9.4.1 carry text."
        )
        analysis["x-v10-default-sources"] = self.crosswalk.sources_for_number(
            root["number"], self.aliases
        )
        defs[f"ObjectiveAnalysis_{section_tag}"] = analysis
        return defs


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


def unplaced_and_log_defs() -> JsonObject:
    return {
        "UnplacedItem": strict_object(
            {
                "v10_source": ref("V10Source"),
                "reason": {"enum": UNPLACED_REASONS},
                "blocks": array_of(ref("Block")),
                "note": {"type": "string"},
            },
            ["v10_source", "reason", "blocks"],
        ),
        "ProcessingStep": strict_object(
            {
                "step": {"type": "string"},
                "actor": ref("Actor"),
                "at": {"type": "string", "format": "date-time"},
                "summary": {"type": "string"},
            }
        ),
    }


def top_level_schema(defs: JsonObject) -> JsonObject:
    section_keys = [str(number) for number in range(1, TOP_LEVEL_SECTION_COUNT + 1)]
    properties: JsonObject = {
        "schema_version": {"const": SCHEMA_VERSION},
        "source": strict_object(
            {
                "path": {"type": "string"},
                "file_name": {"type": "string"},
                "sha256": {"type": "string", "pattern": SHA256_PATTERN},
                "template": {"const": SOURCE_TEMPLATE},
                "extracted_at": {"type": "string", "format": "date-time"},
            }
        ),
        "target": strict_object(
            {"template": {"const": TARGET_TEMPLATE}, "template_file": {"type": "string"}}
        ),
        "title_page": ref("TitlePage"),
        "amendment_details": ref("AmendmentDetails"),
        "sections": strict_object({key: ref(f"Section_{key}") for key in section_keys}),
        "unplaced_v10_content": array_of(ref("UnplacedItem")),
        "processing_log": array_of(ref("ProcessingStep")),
    }
    schema = strict_object(properties)
    ordered_defs = {name: defs[name] for name in sorted(defs)}
    return {
        "$schema": DRAFT_URI,
        "$id": SCHEMA_ID,
        "title": SCHEMA_TITLE,
        "description": (
            "One JSON document per protocol, shaped like the CPT v11 template. A v10 protocol "
            "is extracted into it and a v11 docx is rendered from it. Every value and content "
            "block records its chain of custody."
        ),
        **schema,
        "$defs": ordered_defs,
    }


def build_schema() -> JsonObject:
    inventory = read_json("v11_inventory.json")
    crosswalk = CrosswalkSources(read_json("crosswalk_verified.json"), read_json("v10_section_ids.json"))
    codelists = Codelists(read_json("m11_codelists.json"))
    m11 = M11Index(read_json("m11_elements.json"))
    fields = FieldFactory(m11, codelists)

    sections = SectionBuilder(inventory, crosswalk, fields)
    objectives = ObjectiveBuilder(inventory, crosswalk, fields, sections.aliases)

    defs: JsonObject = {}
    defs.update(custody_defs())
    defs.update(basic_slot_defs())
    defs.update(enum_slot_defs())
    defs.update(codelists.defs())
    defs.update(block_defs())
    defs["SectionStatus"] = section_status_def()
    defs["SectionStatusRules"] = section_status_rules_def()
    defs["AdditionalSubsection"] = additional_subsection_def()
    defs.update(sections.build())
    defs.update(objectives.build())
    defs["AmendmentChange"] = fields.amendment_change_def()
    defs["TitlePage"] = title_page_def(fields, crosswalk, crosswalk.no_source.get("title-page"))
    defs["AmendmentDetails"] = fields.amendment_details_def(crosswalk)
    prior_amendments = defs["Fields_12_3"]["properties"]["prior_amendments"]
    prior_amendments["x-v10-default-sources"] = crosswalk.sources_for_rows([DOCUMENT_HISTORY_ROW])
    defs.update(unplaced_and_log_defs())
    report_unmapped_rows(crosswalk)
    return top_level_schema(defs)


def report_unmapped_rows(crosswalk: CrosswalkSources) -> None:
    for row in crosswalk.rows:
        number = row["v10_number"]
        if number == "FM":
            data_notes.append(
                "crosswalk row FM (template front matter) has no v10 id and no v11 target; "
                "not represented in the schema"
            )
        if number == "TP.19":
            data_notes.append(
                "crosswalk row TP.19 (Table of Contents) maps to no schema property; the TOC is generated"
            )


def strip_annotations(node: Any, is_name_map: bool = False) -> Any:
    if isinstance(node, list):
        return [strip_annotations(item) for item in node]
    if not isinstance(node, dict):
        return node
    stripped = {}
    for key, value in node.items():
        is_annotation = key in ANNOTATION_KEYWORDS or key.startswith("x-")
        if is_annotation and not is_name_map:
            continue
        stripped[key] = strip_annotations(value, key in SUBSCHEMA_MAP_KEYWORDS)
    return stripped


def write_json(path: Path, document: JsonObject) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main() -> int:
    schema = build_schema()
    Draft202012Validator.check_schema(schema)
    lean_schema = strip_annotations(schema)
    Draft202012Validator.check_schema(lean_schema)
    write_json(ANNOTATED_OUTPUT_PATH, schema)
    write_json(OUTPUT_PATH, lean_schema)
    section_defs = [name for name in schema["$defs"] if name.startswith("Section_")]
    print(f"wrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")
    print(f"$defs: {len(schema['$defs'])}, Section defs: {len(section_defs)}")
    for note in data_notes:
        print(f"NOTE: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
