"""Validate a protocol JSON document against protocol.schema.json plus cross-field checks.

Usage: .venv/bin/python schema/validate_protocol.py <protocol.json>
Exit code 1 when any error is found; warnings do not change the exit code.
"""

import hashlib
import json
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError, best_match

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
type JsonObject = dict[str, Json]

SCHEMA_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = SCHEMA_DIR / "protocol.schema.json"
V10_IDS_PATH = SCHEMA_DIR.parent / "resources" / "research" / "schema-inputs" / "v10_section_ids.json"

MAX_MESSAGE_CHARS = 300
SHA256_READ_BYTES = 1 << 20
BLOCK_TYPES = ("paragraph", "table", "heading")
AI_ORIGINS = ("ai_transformed", "ai_generated")
NON_ORIGINS = ("template_text", "none")
FIELD_FOLLOWING_STATUSES = ("not_applicable", "missing")
NUMBERED_V10_ID = re.compile(r"^v10:\d+(\.\d+){0,4}$")
UNPLACED_V10_ID = re.compile(r"^v10:.*\.unmapped$")
OBJECTIVE_SECTIONS = {"primary": "3.1", "secondary": "3.2", "exploratory": "3.3"}
ANALYSIS_SECTIONS = {"primary": "10.4", "secondary": "10.5"}
NOT_GLOBAL_DECODE = "Not Global"
MAX_PERCENT_DECIMALS = 1
YES_DECODE = "Yes"
OTHER_REASON_CODE = "C17649"
CONDITIONAL_AMENDMENT_SLOTS = [
    "enrollment_scope", "primary_reason_other", "secondary_reason_other", "summary",
    "substantial_impact_on_safety_or_rights", "safety_or_rights_explanation",
    "substantial_impact_on_data_reliability", "data_reliability_explanation",
]
DURATION_PAIRS = [
    ("intervention_duration", "intervention_duration_description"),
    ("participation_duration", "participation_duration_description"),
]
ASSIGNMENT_METHOD_FIELDS = {"randomisation_type": "Randomisation", "other_assignment_method": "Other"}


def build_format_checker() -> FormatChecker:
    checker = FormatChecker()

    @checker.checks("date-time", raises=ValueError)
    def is_date_time(value: object) -> bool:
        if isinstance(value, str):
            datetime.fromisoformat(value)
        return True

    return checker


def load_json(path: Path) -> Json:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def format_path(path_parts: list[str | int]) -> str:
    return "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in path_parts)


def describe_error(error: ValidationError) -> str:
    message = error.message
    if error.context:
        deepest = best_match(error.context)
        if deepest is not None:
            message += f" | because: {format_path(list(deepest.absolute_path))}: {deepest.message}"
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[:MAX_MESSAGE_CHARS] + "..."
    return message


def schema_errors(instance: Json, schema: JsonObject) -> list[str]:
    validator = Draft202012Validator(schema, format_checker=build_format_checker())
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(map(str, item.path)))
    return [f"{format_path(list(error.absolute_path))}: {describe_error(error)}" for error in errors]


def dig(node: Json, *keys: str) -> Json:
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def section_node(instance: JsonObject, number: str) -> Json:
    node: Json = dig(instance, "sections", number.split(".")[0])
    for depth in range(2, len(number.split(".")) + 1):
        node = dig(node, "subsections", ".".join(number.split(".")[:depth]))
    return node


def list_at(node: Json, key: str) -> list[JsonObject]:
    value = dig(node, key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def collect_objectives(instance: JsonObject) -> dict[str, list[JsonObject]]:
    return {
        kind: list_at(section_node(instance, number), "objectives")
        for kind, number in OBJECTIVE_SECTIONS.items()
    }


def check_objective_references(instance: JsonObject, errors: list[str]) -> None:
    objectives = collect_objectives(instance)
    known_ids = {kind: {item.get("id") for item in items} for kind, items in objectives.items()}
    rows = list_at(dig(section_node(instance, "1.1.1"), "fields"), "summary_rows")
    for index, row in enumerate(rows):
        objective_id = row.get("objective_id")
        kind = str(objective_id).split("-")[0]
        if objective_id not in known_ids.get(kind, set()):
            errors.append(
                f"section 1.1.1 fields.summary_rows[{index}].objective_id: "
                f"'{objective_id}' does not match any section 3 objective"
            )
    for kind, number in ANALYSIS_SECTIONS.items():
        analyses = list_at(section_node(instance, number), "objective_analyses")
        for index, analysis in enumerate(analyses):
            objective_id = analysis.get("objective_id")
            if objective_id not in known_ids[kind]:
                errors.append(
                    f"section {number} objective_analyses[{index}].objective_id: "
                    f"'{objective_id}' is not a {kind} objective in section 3"
                )


def check_objective_ids_and_numbers(instance: JsonObject, errors: list[str]) -> None:
    seen_ids: set[object] = set()
    for kind, items in collect_objectives(instance).items():
        section = OBJECTIVE_SECTIONS[kind]
        for position, objective in enumerate(items, start=1):
            expected_number = f"{section}.{position}"
            if objective.get("number") != expected_number:
                errors.append(
                    f"section {section} objective {position}: number is "
                    f"'{objective.get('number')}', expected '{expected_number}'"
                )
            estimands = list_at(objective, "estimands")
            for item_id in [objective.get("id")] + [est.get("id") for est in estimands]:
                if item_id in seen_ids:
                    errors.append(f"duplicate objective or estimand id '{item_id}'")
                seen_ids.add(item_id)


def check_single_sponsor(instance: JsonObject, errors: list[str]) -> None:
    sponsors = list_at(dig(instance, "title_page"), "sponsors")
    count = sum(1 for sponsor in sponsors if sponsor.get("role") == "sponsor")
    if count != 1:
        errors.append(f"$.title_page.sponsors: expected exactly one role 'sponsor', found {count}")


def walk(node: Json, path: list[str | int]):
    yield path, node
    if isinstance(node, dict):
        for key, child in node.items():
            yield from walk(child, path + [key])
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from walk(child, path + [index])


def is_block(node: Json) -> bool:
    return isinstance(node, dict) and node.get("type") in BLOCK_TYPES


def custody_sources(node: JsonObject) -> list[JsonObject]:
    sources = dig(node, "custody", "v10_sources")
    return [source for source in sources if isinstance(source, dict)] if isinstance(sources, list) else []


def source_indices_of(sources: list[JsonObject]) -> set[int]:
    indices: set[int] = set()
    for source in sources:
        listed = source.get("source_indices")
        if isinstance(listed, list):
            indices.update(index for index in listed if isinstance(index, int))
    return indices


def selector_key(block: JsonObject, source_index: int) -> str:
    """Selectors of the block's sources that cover this index; blocks with different ones may share it."""
    selectors = [
        source.get("selector")
        for source in custody_sources(block)
        if source_index in source_indices_of([source])
    ]
    return json.dumps(selectors, sort_keys=True)


def check_source_indices(instance: JsonObject, warnings: list[str]) -> None:
    first_seen: dict[tuple[int, str], str] = {}
    for path, node in walk(instance, []):
        if not isinstance(node, dict) or not is_block(node):
            continue
        source_index = node.get("source_index")
        if not isinstance(source_index, int):
            continue
        where = format_path(path)
        key = (source_index, selector_key(node, source_index))
        if key in first_seen:
            warnings.append(f"source_index {source_index} used by {first_seen[key]} and {where}")
        else:
            first_seen[key] = where


def check_block_source_indices(instance: JsonObject, errors: list[str]) -> None:
    for path, node in walk(instance, []):
        if not isinstance(node, dict) or not is_block(node):
            continue
        source_index = node.get("source_index")
        if isinstance(source_index, int) and source_index not in source_indices_of(custody_sources(node)):
            errors.append(
                f"{format_path(path)}: source_index {source_index} is not in its own "
                "custody.v10_sources[].source_indices"
            )


def is_section_like(node: Json) -> bool:
    return (
        isinstance(node, dict)
        and "status" in node
        and isinstance(node.get("content"), list)
        and isinstance(node.get("custody"), dict)
    )


def is_slot(node: Json) -> bool:
    return isinstance(node, dict) and {"value", "status", "custody"} <= node.keys()


def section_items(section: JsonObject) -> list[JsonObject]:
    """Content blocks and the slots under fields: the things that make up the section's origin."""
    items = [block for block in section["content"] if isinstance(block, dict)]
    for _path, node in walk(section.get("fields"), []):
        if is_slot(node):
            items.append(node)
    return items


def expected_section_origin(items: list[JsonObject]) -> str | None:
    custodies = [item["custody"] for item in items if isinstance(item.get("custody"), dict)]
    decisions = [custody for custody in custodies if custody.get("origin") not in NON_ORIGINS]
    if not decisions:
        return None
    origins = [custody["origin"] for custody in decisions]
    if any(origin in AI_ORIGINS for origin in origins):
        return "ai_generated" if all(origin == "ai_generated" for origin in origins) else "ai_transformed"
    section_ids = {source.get("section_id") for custody in decisions for source in custody_sources({"custody": custody})}
    if all(origin == "v10_verbatim" for origin in origins) and len(section_ids) == 1:
        return "v10_verbatim"
    if all(origin in ("v10_verbatim", "v10_derived") for origin in origins):
        return "v10_derived"
    return "human_entered"


def has_value(node: Json) -> bool:
    return isinstance(node, dict) and node.get("value") is not None


def decode_of(slot: Json) -> Json:
    return dig(slot, "value", "decode")


def check_original_protocol(instance: JsonObject, errors: list[str]) -> None:
    title_page = dig(instance, "title_page")
    if decode_of(dig(title_page, "original_protocol")) != YES_DECODE:
        return
    details = dig(instance, "amendment_details")
    value_slots = {
        "title_page": ["amendment_identifier", "amendment_scope"],
        "amendment_details": CONDITIONAL_AMENDMENT_SLOTS,
    }
    for owner_name, names in value_slots.items():
        owner = dig(instance, owner_name)
        for name in names:
            if has_value(dig(owner, name)):
                errors.append(f"$.{owner_name}.{name}: must have no value when original_protocol is Yes")
    for name in ("quantity", "unit"):
        if has_value(dig(details, "approximate_enrolled", name)):
            errors.append(
                f"$.amendment_details.approximate_enrolled.{name}: must have no value when "
                "original_protocol is Yes"
            )
    array_owners = {
        "title_page": ["amendment_scope_identifiers"],
        "amendment_details": ["primary_reasons", "secondary_reasons", "changes"],
    }
    for owner_name, names in array_owners.items():
        for name in names:
            if list_at(dig(instance, owner_name), name):
                errors.append(f"$.{owner_name}.{name}: must be empty when original_protocol is Yes")


def check_other_reason_descriptions(instance: JsonObject, errors: list[str]) -> None:
    details = dig(instance, "amendment_details")
    for reasons_key, other_key in (
        ("primary_reasons", "primary_reason_other"),
        ("secondary_reasons", "secondary_reason_other"),
    ):
        if not has_value(dig(details, other_key)):
            continue
        codes = [dig(reason, "value", "code") for reason in list_at(details, reasons_key)]
        if OTHER_REASON_CODE not in codes:
            errors.append(
                f"$.amendment_details.{other_key}: has a value but no entry in {reasons_key} "
                f"has code {OTHER_REASON_CODE}"
            )


def check_overall_design_conditionals(instance: JsonObject, errors: list[str]) -> None:
    fields = dig(section_node(instance, "1.1.2"), "fields")
    for duration, description in DURATION_PAIRS:
        if has_value(dig(fields, duration)) and has_value(dig(fields, description)):
            errors.append(f"section 1.1.2 fields: fill only one of {duration} and {description}")
    method = decode_of(dig(fields, "intervention_assignment_method"))
    for name, required_method in ASSIGNMENT_METHOD_FIELDS.items():
        if has_value(dig(fields, name)) and method != required_method:
            errors.append(
                f"section 1.1.2 fields.{name}: only allowed when "
                f"intervention_assignment_method is '{required_method}'"
            )


def check_field_statuses_follow_section(instance: JsonObject, errors: list[str]) -> None:
    for path, node in walk(instance, []):
        status = node.get("status") if is_section_like(node) else None
        if status not in FIELD_FOLLOWING_STATUSES:
            continue
        for field_path, slot in walk(node.get("fields"), []):
            if is_slot(slot) and slot.get("status") != status:
                where = format_path(path + ["fields"] + field_path)
                errors.append(
                    f"{where}: slot status is '{slot.get('status')}' but the section is '{status}'"
                )


def check_section_custody(instance: JsonObject, errors: list[str]) -> None:
    for path, node in walk(instance, []):
        if not is_section_like(node):
            continue
        where = format_path(path)
        blocks = [block for block in node["content"] if isinstance(block, dict)]
        section_indices = source_indices_of(custody_sources(node))
        for position, block in enumerate(blocks):
            block_indices = source_indices_of(custody_sources(block))
            if not block_indices <= section_indices:
                errors.append(
                    f"{where}.content[{position}]: v10 source indices "
                    f"{sorted(block_indices - section_indices)} are missing from the section's "
                    "custody.v10_sources"
                )
        flagged = any(dig(block, "custody", "needs_review") is True for block in blocks)
        if flagged and dig(node, "custody", "needs_review") is not True:
            errors.append(f"{where}.custody.needs_review: must be true when a block needs review")
        expected = expected_section_origin(section_items(node))
        actual = dig(node, "custody", "origin")
        if expected is not None and actual != expected:
            errors.append(
                f"{where}.custody.origin: is '{actual}' but its content and fields give '{expected}'"
            )


def check_v10_section_ids(instance: JsonObject, errors: list[str]) -> None:
    known_ids = {entry["id"] for entry in load_json(V10_IDS_PATH)["ids"]}
    for path, node in walk(instance, []):
        if not isinstance(node, dict):
            continue
        sources: list[Json] = []
        if isinstance(node.get("v10_sources"), list):
            sources = node["v10_sources"]
        elif isinstance(node.get("v10_source"), dict):
            sources = [node["v10_source"]]
        elif isinstance(node.get("heading_source"), dict):
            sources = [node["heading_source"]]
        for source in sources:
            if not isinstance(source, dict):
                continue
            section_id = str(source.get("section_id"))
            is_known = (
                section_id in known_ids
                or NUMBERED_V10_ID.match(section_id)
                or UNPLACED_V10_ID.match(section_id)
            )
            if not is_known:
                errors.append(f"{format_path(path)}: unknown v10 section_id '{section_id}'")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(SHA256_READ_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_source_hash(instance: JsonObject, errors: list[str], warnings: list[str]) -> None:
    source = dig(instance, "source")
    if not isinstance(source, dict) or not isinstance(source.get("path"), str):
        return
    path = Path(source["path"])
    if not path.exists():
        warnings.append(f"$.source.path: file not found, sha256 not checked ({path})")
    elif sha256_of(path) != source.get("sha256"):
        errors.append(f"$.source.sha256: does not match the file at {path}")


def check_scope_identifier(instance: JsonObject, errors: list[str]) -> None:
    title_page = dig(instance, "title_page")
    scope_value = dig(title_page, "amendment_scope", "value")
    decode = scope_value.get("decode") if isinstance(scope_value, dict) else None
    identifiers = list_at(title_page, "amendment_scope_identifiers")
    if identifiers and decode != NOT_GLOBAL_DECODE:
        errors.append(
            "$.title_page.amendment_scope_identifiers: must be empty unless amendment_scope "
            f"is '{NOT_GLOBAL_DECODE}'"
        )


def is_missing_slot(slot: JsonObject) -> bool:
    return slot.get("status") == "missing"


def check_intervention_identifiers(instance: JsonObject, errors: list[str]) -> None:
    fields = dig(section_node(instance, "1.1.2"), "fields")
    codes = list_at(fields, "intervention_codes")
    names = list_at(fields, "intervention_nonproprietary_names")
    has_value = any(not is_missing_slot(slot) for slot in codes + names)
    both_single_missing = len(codes) == 1 and len(names) == 1 and not has_value
    if not has_value and not both_single_missing:
        errors.append(
            "section 1.1.2 fields: intervention_codes or intervention_nonproprietary_names "
            "needs a slot that is not missing, or both must be one missing slot"
        )


def check_enrolled_percent_decimals(instance: JsonObject, errors: list[str]) -> None:
    enrolled = dig(instance, "amendment_details", "approximate_enrolled")
    if dig(enrolled, "unit", "value") != "percent":
        return
    quantity = dig(enrolled, "quantity", "value")
    if isinstance(quantity, (int, float)) and not has_at_most_one_decimal(quantity):
        errors.append(
            "$.amendment_details.approximate_enrolled.quantity: a percent allows one decimal, "
            f"got {quantity}"
        )


def has_at_most_one_decimal(number: int | float) -> bool:
    decimal_places = -Decimal(str(number)).as_tuple().exponent
    return decimal_places <= MAX_PERCENT_DECIMALS


def cross_field_checks(instance: JsonObject) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    check_objective_references(instance, errors)
    check_objective_ids_and_numbers(instance, errors)
    check_single_sponsor(instance, errors)
    check_source_indices(instance, warnings)
    check_block_source_indices(instance, errors)
    check_section_custody(instance, errors)
    check_original_protocol(instance, errors)
    check_other_reason_descriptions(instance, errors)
    check_overall_design_conditionals(instance, errors)
    check_field_statuses_follow_section(instance, errors)
    check_v10_section_ids(instance, errors)
    check_source_hash(instance, errors, warnings)
    check_scope_identifier(instance, errors)
    check_intervention_identifiers(instance, errors)
    check_enrolled_percent_decimals(instance, errors)
    return errors, warnings


def validate_document(instance: Json) -> tuple[list[str], list[str]]:
    schema = load_json(SCHEMA_PATH)
    errors = schema_errors(instance, schema)
    warnings: list[str] = []
    if isinstance(instance, dict):
        cross_errors, warnings = cross_field_checks(instance)
        errors += cross_errors
    return errors, warnings


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    errors, warnings = validate_document(load_json(Path(argv[1])))
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
