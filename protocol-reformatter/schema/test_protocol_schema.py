"""Self-test for protocol.schema.json and validate_protocol.py.

Builds a minimal valid instance from the schema itself, then checks that positive
variants pass and that each negative variant fails with the expected error.

Usage: .venv/bin/python schema/test_protocol_schema.py [--write-dir DIR]
With --write-dir the instances are also saved as JSON files for inspection.
"""

import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path

from jsonschema import Draft202012Validator

from minimal_instance_builder import (  # noqa: F401
    REPEATED_ITEM_SECTIONS_WITHOUT_ITEMS,
    MinimalInstanceBuilder,
    SAMPLE_DATE,
    SAMPLE_SOURCE_INDEX,
    TEST_ACTOR,
    TIMESTAMP,
    ZERO_SHA256,
    analysis,
    minimal_instance,
    missing_slot,
    none_custody,
    objective,
    sections_of,
    subsection,
    template_custody,
    v10_section_custody,
    v10_source,
)
from validate_protocol import SCHEMA_PATH, load_json, validate_document

ANNOTATED_SCHEMA_PATH = SCHEMA_PATH.with_name("protocol.schema.annotated.json")
SPONSOR_ADDRESS_CODE = "C218677"

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
type JsonObject = dict[str, Json]

LLM_ACTOR: JsonObject = {"kind": "llm", "name": "test-extractor", "model": "test-model"}
TOO_LONG_ACRONYM = "A" * 15
REVIEWER = "reviewer"
OPTIONAL_PARENT = "9.1.3.1"
OPTIONAL_PARENT_CHILD_KEYS = [
    "9.1.3.1#definition_of_medical_device_ae_and_ade",
    "9.1.3.1#definition_of_medical_device_sae_sade_and_usade",
    "9.1.3.1#definition_of_device_deficiency",
]
LABEL_LENGTH = 24


def filled_slot(value: Json, custody: JsonObject | None = None) -> JsonObject:
    return {"value": value, "status": "filled", "custody": custody or template_custody()}


def verbatim_paragraph() -> JsonObject:
    custody = {**none_custody(), "origin": "v10_verbatim", "v10_sources": [v10_source("v10:2.3.1")]}
    return {
        "type": "paragraph",
        "text": "Copied text",
        "style": "Normal",
        "source_index": SAMPLE_SOURCE_INDEX,
        "custody": custody,
    }


def ai_paragraph() -> JsonObject:
    custody = {
        **none_custody(),
        "origin": "ai_generated",
        "actor": copy.deepcopy(LLM_ACTOR),
        "needs_review": True,
    }
    return {"type": "paragraph", "text": "AI text", "style": "Normal", "custody": custody}


def code_with_decode(schema: JsonObject, codelist: str, decode: str) -> JsonObject:
    for term in schema["$defs"][f"Code_{codelist}"]["oneOf"]:
        if term["properties"]["decode"]["const"] == decode:
            return {"code": term["properties"]["code"]["const"], "decode": decode}
    raise KeyError(f"{decode} not in {codelist}")


def external_code(system: str = "other") -> JsonObject:
    return {"system": system, "code": "X1", "decode": "Example"}


def first_code(schema: JsonObject, codelist: str) -> JsonObject:
    term = schema["$defs"][f"Code_{codelist}"]["oneOf"][0]["properties"]
    return {"code": term["code"]["const"], "decode": term["decode"]["const"]}


def populated_instance(schema: JsonObject) -> JsonObject:
    instance = minimal_instance(schema)
    verbatim = verbatim_paragraph()
    populate_section(
        instance, [verbatim, ai_paragraph()], mixed_ai_section_custody(sources_of(verbatim))
    )
    instance["title_page"]["trial_phase"] = filled_slot(first_code(schema, "TrialPhase"))
    instance["title_page"]["trial_acronym"] = filled_slot("ACRO")
    rows = subsection(instance, "1.1.1")["fields"]["summary_rows"]
    rows.append({"objective_id": "primary-1", "objective": missing_slot(), "estimand_summary": missing_slot()})
    return instance


def ai_custody(**overrides: Json) -> JsonObject:
    return {**ai_paragraph()["custody"], **overrides}


def reviewed(status: str) -> JsonObject:
    return {"status": status, "by": REVIEWER, "at": TIMESTAMP}


def populate_section(
    instance: JsonObject, content: list[JsonObject], custody: JsonObject
) -> JsonObject:
    section = subsection(instance, "2.1.1")
    section["status"] = "populated"
    section["content"] = content
    section["custody"] = custody
    return section


def human_section_custody(sources: list[JsonObject]) -> JsonObject:
    custody = v10_section_custody("human_entered", sources)
    custody["actor"] = {"kind": "human", "name": REVIEWER}
    return custody


def mixed_ai_section_custody(sources: list[JsonObject]) -> JsonObject:
    return {**ai_custody(origin="ai_transformed"), "v10_sources": copy.deepcopy(sources)}


def sources_of(block: JsonObject) -> list[JsonObject]:
    return block["custody"]["v10_sources"]


def set_not_applicable(instance: JsonObject, custody: JsonObject) -> None:
    section = subsection(instance, "2.1.1")
    section["status"] = "not_applicable"
    section["custody"] = custody


def edited_custody() -> JsonObject:
    return {
        **none_custody(),
        "origin": "human_entered",
        "actor": {"kind": "human", "name": REVIEWER},
        "v10_sources": [v10_source("v10:2.1.1")],
        "review": reviewed("edited"),
        "edited_from": {"origin": "ai_transformed", "actor": copy.deepcopy(LLM_ACTOR)},
    }


def label_paragraph(portion: str = "part", origin: str = "v10_verbatim") -> JsonObject:
    paragraph = verbatim_paragraph()
    paragraph["strip_prefix_chars"] = LABEL_LENGTH
    paragraph["custody"]["origin"] = origin
    paragraph["custody"]["v10_sources"][0]["portion"] = portion
    return paragraph


APPENDIX_SOURCE_INDEX = 768


def appendix_source(section_id: str) -> JsonObject:
    return {
        "section_id": section_id,
        "found_number": "12",
        "found_heading": "Appendix 11: Questionnaires",
        "match": "protocol_added",
        "source_indices": [APPENDIX_SOURCE_INDEX],
        "portion": "all",
    }


def appendix_unplaced_item(section_id: str) -> JsonObject:
    heading_custody = {
        **none_custody(),
        "origin": "v10_verbatim",
        "v10_sources": [appendix_source(section_id)],
    }
    heading = {
        "type": "heading",
        "text": "Appendix 11: Questionnaires",
        "level": 1,
        "source_index": APPENDIX_SOURCE_INDEX,
        "custody": heading_custody,
    }
    return {
        "v10_source": appendix_source(section_id),
        "reason": "no_v11_slot",
        "blocks": [heading],
    }


# Positive mutations: each changes a valid instance into another valid one.


def allow_heading_source_on_container(instance: JsonObject) -> None:
    sections_of(instance)["4"]["heading_source"] = {**v10_source("v10:9.1"), "match": "renamed"}


def allow_inline_label_paragraph(instance: JsonObject) -> None:
    paragraph = label_paragraph()
    populate_section(instance, [paragraph], v10_section_custody("v10_verbatim", sources_of(paragraph)))


def allow_ai_decided_not_applicable(instance: JsonObject) -> None:
    set_not_applicable(instance, ai_custody())


def allow_script_decided_not_applicable(instance: JsonObject) -> None:
    custody = {**none_custody(), "origin": "v10_derived", "v10_sources": [v10_source("v10:2.1.1")]}
    set_not_applicable(instance, custody)


def allow_accepted_low_confidence(instance: JsonObject) -> None:
    custody = instance["title_page"]["full_title"]["custody"]
    custody.update({"confidence": "low", "review": reviewed("accepted"), "needs_review": False})


def allow_accepted_unsure_slot(instance: JsonObject) -> None:
    slot = filled_slot("x", {**template_custody(), "review": reviewed("accepted")})
    slot["status"] = "unsure"
    instance["title_page"]["full_title"] = slot


def allow_reviewer_edit(instance: JsonObject) -> None:
    paragraph = ai_paragraph()
    paragraph["custody"] = edited_custody()
    populate_section(instance, [paragraph], human_section_custody(sources_of(paragraph)))


def allow_accepted_ai_text(instance: JsonObject) -> None:
    paragraph = ai_paragraph()
    paragraph["custody"].update({"review": reviewed("accepted"), "needs_review": False})
    section_custody = ai_custody(review=reviewed("accepted"), needs_review=False)
    populate_section(instance, [paragraph], section_custody)


def allow_protocol_added_body_unmapped(instance: JsonObject) -> None:
    instance["unplaced_v10_content"].append(appendix_unplaced_item("v10:body.unmapped"))


def allow_control_product_names(instance: JsonObject) -> None:
    section = subsection(instance, "1.1.2")
    section["status"] = "populated"
    fields = section["fields"]
    fields["control_nonproprietary_names"] = [filled_slot(external_code("WHO INN"))]
    fields["control_inns"] = [filled_slot(external_code("WHO INN"))]
    fields["control_description_not_applicable"] = filled_slot("Not applicable")


def set_not_global_scope(instance: JsonObject, identifiers: list[JsonObject]) -> None:
    schema = load_json(SCHEMA_PATH)
    page = instance["title_page"]
    page["amendment_scope"] = filled_slot(code_with_decode(schema, "AmendmentScope", "Not Global"))
    page["amendment_scope_identifiers"] = identifiers


def allow_country_and_site_scope_identifiers(instance: JsonObject) -> None:
    country = {"kind": "country", "country_or_region": filled_slot(external_code("GENC"))}
    site = {"kind": "site", "site": filled_slot("Site 12")}
    set_not_global_scope(instance, [country, site])


def allow_multiple_amendment_reasons(instance: JsonObject) -> None:
    schema = load_json(SCHEMA_PATH)
    reasons = [
        filled_slot(first_code(schema, "ReasonForAmendment")),
        filled_slot(code_with_decode(schema, "ReasonForAmendment", "Other")),
    ]
    details = instance["amendment_details"]
    details["primary_reasons"] = reasons
    details["secondary_reasons"] = reasons[:1]


def set_enrolled(instance: JsonObject, quantity: int | float, unit: str) -> None:
    enrolled = instance["amendment_details"]["approximate_enrolled"]
    enrolled["quantity"] = filled_slot(quantity)
    enrolled["unit"] = filled_slot(unit)


def allow_percent_enrolled_with_one_decimal(instance: JsonObject) -> None:
    set_enrolled(instance, 33.3, "percent")


def allow_integer_number_enrolled(instance: JsonObject) -> None:
    set_enrolled(instance, 70, "number")


def allow_country_region_entry(instance: JsonObject) -> None:
    entry = {
        "identifier": filled_slot(external_code("ISO 3166-1")),
        "requirements": [],
        "clarifications": [],
        "custody": none_custody(),
    }
    section = subsection(instance, "12.2")
    section["status"] = "populated"
    section["fields"]["country_regions"] = [entry]


def table_block(origin: str, portion: str, selector: JsonObject | None = None) -> JsonObject:
    source = {**v10_source("v10:2.3.1"), "portion": portion}
    if selector is not None:
        source["selector"] = selector
    custody = {**none_custody(), "origin": origin, "v10_sources": [source]}
    if origin == "v10_derived":
        custody["actor"] = copy.deepcopy(TEST_ACTOR)
    return {
        "type": "table",
        "rows": [["Objective", "Estimand"]],
        "source_index": SAMPLE_SOURCE_INDEX,
        "custody": custody,
    }


def populate_with_block(instance: JsonObject, block: JsonObject, origin: str) -> None:
    populate_section(instance, [block], v10_section_custody(origin, sources_of(block)))


def allow_partial_derived_table_with_selector(instance: JsonObject) -> None:
    block = table_block("v10_derived", "part", {"rows": [0, 1], "columns": [0]})
    populate_with_block(instance, block, "v10_derived")


def allow_part_label_on_block(instance: JsonObject) -> None:
    paragraph = verbatim_paragraph()
    paragraph["part"] = "Severity"
    populate_with_block(instance, paragraph, "v10_verbatim")


def allow_template_heading_beside_verbatim_paragraph(instance: JsonObject) -> None:
    heading = {"type": "heading", "text": "Severity", "level": 4, "custody": template_custody()}
    paragraph = verbatim_paragraph()
    content = [heading, paragraph]
    populate_section(instance, content, v10_section_custody("v10_verbatim", sources_of(paragraph)))


def added_subsection(**overrides: Json) -> JsonObject:
    paragraph = verbatim_paragraph()
    subsection_node = {
        "number": "12.5",
        "title": "Appendix 12: Questionnaires",
        "level": 2,
        "status": "populated",
        "heading_source": appendix_source("v10:12.unmapped"),
        "custody": v10_section_custody("v10_verbatim", sources_of(paragraph)),
        "content": [paragraph],
        "subsections": [],
    }
    return {**subsection_node, **overrides}


def allow_additional_appendix_in_section_12(instance: JsonObject) -> None:
    child = added_subsection(number=None, title="Nested", level=3)
    section = sections_of(instance)["12"]
    section["additional_subsections"] = [added_subsection(subsections=[child])]


def allow_secondary_objectives_under_container(instance: JsonObject) -> None:
    section = subsection(instance, "3.2")
    section["status"] = "container"
    section["custody"] = none_custody()
    section["objectives"] = [objective("secondary", "3.2")]


def allow_omitted_optional_parent_with_omitted_children(instance: JsonObject) -> None:
    parent = subsection(instance, OPTIONAL_PARENT)
    parent["status"] = "omitted"
    for key in OPTIONAL_PARENT_CHILD_KEYS:
        parent["subsections"][key]["status"] = "omitted"


def mark_fields_not_applicable(instance: JsonObject) -> None:
    section = subsection(instance, "1.1.2")
    section["status"] = "not_applicable"
    section["custody"] = ai_custody()

    def visit(node: Json) -> None:
        if isinstance(node, dict) and {"value", "status", "custody"} <= node.keys():
            node.update({"value": None, "status": "not_applicable", "custody": ai_custody()})
        elif isinstance(node, dict):
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(section["fields"])


SADE_REPORTING_PARENT = "9.2.3"
SADE_REPORTING_KEY = "9.2.3#sade_reporting_to_x"
RECORDING_KEY = "9.2.2#recording"


def unnumbered_child(instance: JsonObject, parent_number: str, key: str) -> JsonObject:
    return subsection(instance, parent_number)["subsections"][key]


def allow_placeholder_title_resolved(instance: JsonObject) -> None:
    child = unnumbered_child(instance, SADE_REPORTING_PARENT, SADE_REPORTING_KEY)
    child["title"] = "SADE Reporting to Sponsor"


def allow_signature_and_criteria_numbers(instance: JsonObject) -> None:
    signatory = {
        "name": missing_slot(),
        "title": missing_slot(),
        "date": missing_slot(),
        "signature": {"kind": "image", "value": missing_slot()},
    }
    instance["title_page"]["sponsor_signatories"] = [signatory]
    criteria = subsection(instance, "5.2")["fields"]["criteria"]
    criteria.extend([{"number": 1, "text": missing_slot()}, {"number": None, "text": missing_slot()}])


def allow_cdisc_dose_form(instance: JsonObject) -> None:
    section = subsection(instance, "6")
    section["status"] = "populated"
    item_schema = load_json(SCHEMA_PATH)["$defs"]["Fields_6"]["properties"]["interventions"]["items"]
    schema = load_json(SCHEMA_PATH)
    intervention = MinimalInstanceBuilder(schema).build(item_schema)
    intervention["dose_form"] = filled_slot(external_code("CDISC SDTM"))
    section["fields"]["interventions"] = [intervention]


def overall_design_fields(instance: JsonObject) -> JsonObject:
    section = subsection(instance, "1.1.2")
    section["status"] = "populated"
    return section["fields"]


def sample_duration(schema: JsonObject) -> JsonObject:
    return {"quantity": 36, "unit": code_with_decode(schema, "UnitOfMeasure", "Weeks")}


def set_original_protocol_yes(instance: JsonObject) -> None:
    schema = load_json(SCHEMA_PATH)
    instance["title_page"]["original_protocol"] = filled_slot(code_with_decode(schema, "NoYes", "Yes"))


def allow_original_protocol_with_empty_amendment_slots(instance: JsonObject) -> None:
    set_original_protocol_yes(instance)


def allow_other_reason_description_with_other_code(instance: JsonObject) -> None:
    schema = load_json(SCHEMA_PATH)
    details = instance["amendment_details"]
    details["primary_reasons"] = [filled_slot(code_with_decode(schema, "ReasonForAmendment", "Other"))]
    details["primary_reason_other"] = filled_slot("Sponsor decision")


def allow_one_duration_of_each_pair(instance: JsonObject) -> None:
    fields = overall_design_fields(instance)
    fields["intervention_duration"] = filled_slot(sample_duration(load_json(SCHEMA_PATH)))
    fields["participation_duration_description"] = filled_slot("Until the last visit")


def allow_assignment_method_details(instance: JsonObject) -> None:
    schema = load_json(SCHEMA_PATH)
    fields = overall_design_fields(instance)
    method = code_with_decode(schema, "TrialInterventionAssignmentMethod", "Randomisation")
    fields["intervention_assignment_method"] = filled_slot(method)
    fields["randomisation_type"] = filled_slot("Block randomisation")


def allow_other_assignment_method_description(instance: JsonObject) -> None:
    schema = load_json(SCHEMA_PATH)
    fields = overall_design_fields(instance)
    method = code_with_decode(schema, "TrialInterventionAssignmentMethod", "Other")
    fields["intervention_assignment_method"] = filled_slot(method)
    fields["other_assignment_method"] = filled_slot("Minimisation")


def unsure_slot(origin: str, raw_text: str | None) -> JsonObject:
    custody = {**none_custody(), "origin": origin, "needs_review": True}
    if origin == "v10_derived":
        custody["v10_sources"] = [v10_source("v10:title_page.protocol_title")]
    slot = {"value": None, "status": "unsure", "custody": custody}
    if raw_text is not None:
        slot["raw_text"] = raw_text
    return slot


def allow_unsure_without_value_and_no_origin(instance: JsonObject) -> None:
    instance["title_page"]["full_title"] = unsure_slot("none", None)


def allow_unsure_without_value_derived_with_raw_text(instance: JsonObject) -> None:
    instance["title_page"]["full_title"] = unsure_slot("v10_derived", "Study of X (draft)")


POSITIVES: dict[str, Callable[[JsonObject], None]] = {
    "heading_source on a container section": allow_heading_source_on_container,
    "inline label paragraph with strip_prefix_chars": allow_inline_label_paragraph,
    "AI-decided not applicable flagged for review": allow_ai_decided_not_applicable,
    "script-decided not applicable with a v10 source": allow_script_decided_not_applicable,
    "accepted low-confidence item": allow_accepted_low_confidence,
    "accepted unsure slot": allow_accepted_unsure_slot,
    "reviewer edit recorded as human_entered": allow_reviewer_edit,
    "accepted AI text keeps its AI origin": allow_accepted_ai_text,
    "protocol_added appendix under v10:body.unmapped": allow_protocol_added_body_unmapped,
    "control product names, INNs and not applicable": allow_control_product_names,
    "country and site amendment scope identifiers": allow_country_and_site_scope_identifiers,
    "several primary and secondary amendment reasons": allow_multiple_amendment_reasons,
    "percent enrolled with one decimal": allow_percent_enrolled_with_one_decimal,
    "integer number enrolled": allow_integer_number_enrolled,
    "country/region entry in 12.2": allow_country_region_entry,
    "partial derived table with a selector": allow_partial_derived_table_with_selector,
    "part label on a block": allow_part_label_on_block,
    "template heading beside a verbatim paragraph": allow_template_heading_beside_verbatim_paragraph,
    "additional appendix under section 12": allow_additional_appendix_in_section_12,
    "secondary objectives under a container 3.2": allow_secondary_objectives_under_container,
    "not applicable section with not applicable fields": mark_fields_not_applicable,
    "placeholder title resolved to a real title": allow_placeholder_title_resolved,
    "signatory signature and criteria numbers": allow_signature_and_criteria_numbers,
    "CDISC SDTM dose form": allow_cdisc_dose_form,
    "original protocol with empty amendment slots": allow_original_protocol_with_empty_amendment_slots,
    "other reason description with an Other reason": allow_other_reason_description_with_other_code,
    "one duration of each pair": allow_one_duration_of_each_pair,
    "randomisation type with Randomisation": allow_assignment_method_details,
    "other assignment method with Other": allow_other_assignment_method_description,
    "unsure slot without value and origin none": allow_unsure_without_value_and_no_origin,
    "unsure slot without value, derived with raw_text": allow_unsure_without_value_derived_with_raw_text,
    "omitted optional parent with omitted children": allow_omitted_optional_parent_with_omitted_children,
}


# Negative mutations: each returns nothing and breaks one rule.


def break_verbatim_without_index(instance: JsonObject) -> None:
    paragraph = verbatim_paragraph()
    del paragraph["source_index"]
    populate_section(instance, [paragraph], v10_section_custody("v10_verbatim", sources_of(paragraph)))


def break_ai_with_sources(instance: JsonObject) -> None:
    paragraph = ai_paragraph()
    paragraph["custody"]["v10_sources"] = [v10_source("v10:2.1.1")]
    populate_section(instance, [paragraph], ai_custody())


def break_filled_slot_null(instance: JsonObject) -> None:
    instance["title_page"]["full_title"] = {**filled_slot("x"), "value": None}


def break_container_with_content(instance: JsonObject) -> None:
    sections_of(instance)["2"]["status"] = "container"
    sections_of(instance)["2"]["content"] = [ai_paragraph()]


def break_unknown_code_pair(instance: JsonObject) -> None:
    instance["title_page"]["trial_phase"] = filled_slot({"code": "C1", "decode": "Not a phase"})


def break_ai_actor_not_llm(instance: JsonObject) -> None:
    paragraph = ai_paragraph()
    paragraph["custody"]["actor"] = copy.deepcopy(TEST_ACTOR)
    populate_section(instance, [paragraph], ai_custody())


def break_low_confidence_without_review(instance: JsonObject) -> None:
    instance["title_page"]["full_title"]["custody"]["confidence"] = "low"


def break_populated_on_heading_only_section(instance: JsonObject) -> None:
    subsection(instance, "3.1")["status"] = "populated"


def break_missing_section(instance: JsonObject) -> None:
    del sections_of(instance)["14"]


def break_acronym_too_long(instance: JsonObject) -> None:
    instance["title_page"]["trial_acronym"] = filled_slot(TOO_LONG_ACRONYM)


def break_missing_slot_with_value(instance: JsonObject) -> None:
    instance["title_page"]["full_title"]["value"] = "should be null"


# Cross-field mutations: schema-valid but rejected by validate_protocol.py.


def break_no_sponsor(instance: JsonObject) -> None:
    instance["title_page"]["sponsors"][0]["role"] = "co_sponsor"


def break_unknown_objective_reference(instance: JsonObject) -> None:
    rows = subsection(instance, "1.1.1")["fields"]["summary_rows"]
    rows.append({"objective_id": "primary-9", "objective": missing_slot(), "estimand_summary": missing_slot()})


def break_scope_identifier_with_global(instance: JsonObject) -> None:
    identifier = {"kind": "site", "site": filled_slot("Site 12")}
    instance["title_page"]["amendment_scope_identifiers"] = [identifier]


def break_percent_with_two_decimals(instance: JsonObject) -> None:
    set_enrolled(instance, 12.75, "percent")


def break_no_intervention_identifier(instance: JsonObject) -> None:
    fields = subsection(instance, "1.1.2")["fields"]
    fields["intervention_codes"] = []
    fields["intervention_nonproprietary_names"] = []


def break_objective_numbering(instance: JsonObject) -> None:
    subsection(instance, "3.1")["objectives"][0]["number"] = "3.1.2"


def break_duplicate_estimand_id(instance: JsonObject) -> None:
    objective_node = subsection(instance, "3.1")["objectives"][0]
    objective_node["estimands"].append(copy.deepcopy(objective_node["estimands"][0]))


def break_unknown_v10_section_id(instance: JsonObject) -> None:
    paragraph = verbatim_paragraph()
    paragraph["custody"]["v10_sources"] = [v10_source("v10:title_page.made_up_field")]
    populate_section(instance, [paragraph], v10_section_custody("v10_verbatim", sources_of(paragraph)))


def break_heading_source_partial(instance: JsonObject) -> None:
    sections_of(instance)["4"]["heading_source"] = {**v10_source("v10:9.1"), "portion": "part"}


def break_heading_source_missing(instance: JsonObject) -> None:
    del sections_of(instance)["4"]["heading_source"]


def break_label_with_whole_portion(instance: JsonObject) -> None:
    paragraph = label_paragraph(portion="all")
    populate_section(instance, [paragraph], v10_section_custody("v10_verbatim", sources_of(paragraph)))


def break_label_on_derived_origin(instance: JsonObject) -> None:
    paragraph = label_paragraph(origin="v10_derived")
    paragraph["custody"]["actor"] = copy.deepcopy(TEST_ACTOR)
    populate_section(instance, [paragraph], v10_section_custody("v10_derived", sources_of(paragraph)))


def break_template_text_not_applicable(instance: JsonObject) -> None:
    set_not_applicable(instance, template_custody())


def break_none_origin_not_applicable(instance: JsonObject) -> None:
    set_not_applicable(instance, none_custody())


def break_slot_not_applicable_from_template(instance: JsonObject) -> None:
    slot = {"value": None, "status": "not_applicable", "custody": template_custody()}
    instance["title_page"]["full_title"] = slot


def break_ai_not_applicable_without_review(instance: JsonObject) -> None:
    set_not_applicable(instance, ai_custody(needs_review=False))


def break_template_text_with_llm_actor(instance: JsonObject) -> None:
    custody = {**template_custody(), "actor": copy.deepcopy(LLM_ACTOR)}
    populate_section(instance, [{**ai_paragraph(), "custody": custody}], none_custody())


def break_unreviewed_without_needs_review(instance: JsonObject) -> None:
    instance["title_page"]["full_title"]["custody"]["review"] = {"status": "unreviewed"}


def break_rejected_without_needs_review(instance: JsonObject) -> None:
    instance["title_page"]["full_title"]["custody"]["review"] = reviewed("rejected")


def break_accepted_review_without_reviewer(instance: JsonObject) -> None:
    custody = instance["title_page"]["full_title"]["custody"]
    custody["review"] = {"status": "accepted"}


def break_low_confidence_with_open_review(instance: JsonObject) -> None:
    custody = instance["title_page"]["full_title"]["custody"]
    custody.update({"confidence": "low", "review": {"status": "unreviewed"}})


def break_unsure_slot_without_review(instance: JsonObject) -> None:
    slot = filled_slot("x", {**template_custody(), "needs_review": False})
    slot["status"] = "unsure"
    instance["title_page"]["full_title"] = slot


def break_edit_keeps_ai_origin(instance: JsonObject) -> None:
    custody = ai_custody(review=reviewed("edited"), needs_review=False)
    custody["edited_from"] = {"origin": "ai_generated", "actor": copy.deepcopy(LLM_ACTOR)}
    populate_section(instance, [{**ai_paragraph(), "custody": custody}], ai_custody())


def break_edit_without_previous_value(instance: JsonObject) -> None:
    custody = edited_custody()
    del custody["edited_from"]
    populate_section(
        instance,
        [{**ai_paragraph(), "custody": custody}],
        human_section_custody(custody["v10_sources"]),
    )


def break_edited_from_on_non_human_origin(instance: JsonObject) -> None:
    custody = ai_custody(edited_from={"origin": "ai_generated", "actor": copy.deepcopy(LLM_ACTOR)})
    populate_section(instance, [{**ai_paragraph(), "custody": custody}], ai_custody())


def break_protocol_added_with_numbered_id(instance: JsonObject) -> None:
    instance["unplaced_v10_content"].append(appendix_unplaced_item("v10:12"))


def break_country_scope_with_site(instance: JsonObject) -> None:
    set_not_global_scope(instance, [{"kind": "country", "site": filled_slot("Site 12")}])


def break_site_scope_with_country(instance: JsonObject) -> None:
    identifier = {"kind": "site", "country_or_region": filled_slot(external_code("GENC"))}
    set_not_global_scope(instance, [identifier])


def break_percent_over_100(instance: JsonObject) -> None:
    set_enrolled(instance, 101, "percent")


def break_fractional_number_enrolled(instance: JsonObject) -> None:
    set_enrolled(instance, 12.5, "number")


def break_empty_blinded_roles(instance: JsonObject) -> None:
    subsection(instance, "1.1.2")["fields"]["blinded_roles"] = []


def break_empty_independent_committees(instance: JsonObject) -> None:
    subsection(instance, "1.1.2")["fields"]["independent_committees"] = []


def break_empty_population_condition(instance: JsonObject) -> None:
    subsection(instance, "1.1.2")["fields"]["population_diagnosis_or_condition"] = []


def break_country_region_without_identifier(instance: JsonObject) -> None:
    entry = {"requirements": [], "clarifications": [], "custody": none_custody()}
    subsection(instance, "12.2")["fields"]["country_regions"] = [entry]


def break_verbatim_table_partial(instance: JsonObject) -> None:
    populate_with_block(instance, table_block("v10_verbatim", "part"), "v10_verbatim")


def break_verbatim_heading_partial(instance: JsonObject) -> None:
    heading = {
        "type": "heading",
        "text": "Heading",
        "level": 3,
        "source_index": SAMPLE_SOURCE_INDEX,
        "custody": table_block("v10_verbatim", "part")["custody"],
    }
    populate_with_block(instance, heading, "v10_verbatim")


def break_verbatim_paragraph_partial_without_strip(instance: JsonObject) -> None:
    paragraph = verbatim_paragraph()
    paragraph["custody"]["v10_sources"][0]["portion"] = "part"
    populate_with_block(instance, paragraph, "v10_verbatim")


def break_additional_subsection_matched_to_template(instance: JsonObject) -> None:
    matched = {**appendix_source("v10:12"), "match": "exact"}
    section = sections_of(instance)["12"]
    section["additional_subsections"] = [added_subsection(heading_source=matched)]


def break_additional_subsection_missing_status(instance: JsonObject) -> None:
    sections_of(instance)["12"]["additional_subsections"] = [added_subsection(status="missing")]


def break_section_without_additional_subsections(instance: JsonObject) -> None:
    del sections_of(instance)["4"]["additional_subsections"]


def break_selector_with_unknown_key(instance: JsonObject) -> None:
    block = table_block("v10_derived", "part", {"cells": [0]})
    populate_with_block(instance, block, "v10_derived")


def break_container_without_objectives(instance: JsonObject) -> None:
    section = subsection(instance, "3.2")
    section["status"] = "container"
    section["custody"] = none_custody()


def break_not_applicable_with_objectives(instance: JsonObject) -> None:
    subsection(instance, "3.2")["objectives"] = [objective("secondary", "3.2")]


def break_container_without_analyses(instance: JsonObject) -> None:
    section = subsection(instance, "10.5")
    section["status"] = "container"
    section["custody"] = none_custody()


def break_omitted_parent_with_kept_child(instance: JsonObject) -> None:
    subsection(instance, OPTIONAL_PARENT)["status"] = "omitted"


def break_required_child_omitted_under_kept_parent(instance: JsonObject) -> None:
    parent = subsection(instance, OPTIONAL_PARENT)
    parent["subsections"][OPTIONAL_PARENT_CHILD_KEYS[0]]["status"] = "omitted"


def break_fixed_unnumbered_title_changed(instance: JsonObject) -> None:
    unnumbered_child(instance, "9.2.2", RECORDING_KEY)["title"] = "Something else"


def break_objective_without_category_label(instance: JsonObject) -> None:
    del subsection(instance, "3.1")["objectives"][0]["category_label"]


def break_signatory_without_signature(instance: JsonObject) -> None:
    signatory = {"name": missing_slot(), "title": missing_slot(), "date": missing_slot()}
    instance["title_page"]["sponsor_signatories"] = [signatory]


def break_signature_kind_unknown(instance: JsonObject) -> None:
    signature = {"kind": "stamp", "value": missing_slot()}
    signatory = {"name": missing_slot(), "title": missing_slot(), "date": missing_slot(), "signature": signature}
    instance["title_page"]["sponsor_signatories"] = [signatory]


def break_criterion_number_as_text(instance: JsonObject) -> None:
    criteria = subsection(instance, "5.2")["fields"]["criteria"]
    criteria.append({"number": "1", "text": missing_slot()})


def break_criterion_number_zero(instance: JsonObject) -> None:
    criteria = subsection(instance, "5.2")["fields"]["criteria"]
    criteria.append({"number": 0, "text": missing_slot()})


def break_unknown_external_code_system(instance: JsonObject) -> None:
    fields = subsection(instance, "1.1.2")["fields"]
    fields["control_inns"] = [filled_slot(external_code("Made Up"))]


def break_unsure_without_value_from_ai(instance: JsonObject) -> None:
    slot = unsure_slot("ai_generated", None)
    slot["custody"].update({"actor": copy.deepcopy(LLM_ACTOR)})
    instance["title_page"]["full_title"] = slot


def break_unsure_without_value_derived_without_raw_text(instance: JsonObject) -> None:
    instance["title_page"]["full_title"] = unsure_slot("v10_derived", None)


SCHEMA_NEGATIVES: dict[str, Callable[[JsonObject], None]] = {
    "v10_verbatim without source_index": break_verbatim_without_index,
    "ai_generated with v10_sources": break_ai_with_sources,
    "filled slot with null value": break_filled_slot_null,
    "container section with content": break_container_with_content,
    "unknown code/decode pair": break_unknown_code_pair,
    "ai_generated with a non-llm actor": break_ai_actor_not_llm,
    "low confidence without needs_review": break_low_confidence_without_review,
    "populated status on a heading-only section": break_populated_on_heading_only_section,
    "section 14 missing": break_missing_section,
    "trial acronym over 14 characters": break_acronym_too_long,
    "missing slot with a value": break_missing_slot_with_value,
    "heading_source with portion part": break_heading_source_partial,
    "section without heading_source": break_heading_source_missing,
    "strip_prefix_chars with portion all": break_label_with_whole_portion,
    "strip_prefix_chars on a derived paragraph": break_label_on_derived_origin,
    "not applicable recorded as template_text": break_template_text_not_applicable,
    "not applicable recorded as none": break_none_origin_not_applicable,
    "not applicable slot recorded as template_text": break_slot_not_applicable_from_template,
    "AI-decided not applicable without needs_review": break_ai_not_applicable_without_review,
    "template_text with an llm actor": break_template_text_with_llm_actor,
    "unreviewed review with needs_review false": break_unreviewed_without_needs_review,
    "rejected review with needs_review false": break_rejected_without_needs_review,
    "accepted review without by and at": break_accepted_review_without_reviewer,
    "low confidence with an unreviewed review": break_low_confidence_with_open_review,
    "unsure slot without review and needs_review false": break_unsure_slot_without_review,
    "edited review that keeps the AI origin": break_edit_keeps_ai_origin,
    "edited review without edited_from": break_edit_without_previous_value,
    "edited_from on a non-human origin": break_edited_from_on_non_human_origin,
    "protocol_added with a numbered section_id": break_protocol_added_with_numbered_id,
    "country scope identifier with a site value": break_country_scope_with_site,
    "site scope identifier with a country value": break_site_scope_with_country,
    "percent enrolled over 100": break_percent_over_100,
    "fractional number enrolled": break_fractional_number_enrolled,
    "empty blinded_roles": break_empty_blinded_roles,
    "empty independent_committees": break_empty_independent_committees,
    "empty population_diagnosis_or_condition": break_empty_population_condition,
    "12.2 country/region entry without identifier": break_country_region_without_identifier,
    "verbatim table with portion part": break_verbatim_table_partial,
    "verbatim heading with portion part": break_verbatim_heading_partial,
    "verbatim paragraph with portion part and no strip_prefix_chars": break_verbatim_paragraph_partial_without_strip,
    "additional subsection matched to a template id": break_additional_subsection_matched_to_template,
    "additional subsection with status missing": break_additional_subsection_missing_status,
    "section without additional_subsections": break_section_without_additional_subsections,
    "selector with an unknown key": break_selector_with_unknown_key,
    "3.2 container without objectives": break_container_without_objectives,
    "3.2 not applicable with objectives": break_not_applicable_with_objectives,
    "10.5 container without analyses": break_container_without_analyses,
    "omitted parent with a kept child": break_omitted_parent_with_kept_child,
    "non-optional child omitted under a kept parent": break_required_child_omitted_under_kept_parent,
    "fixed unnumbered title changed": break_fixed_unnumbered_title_changed,
    "objective without category_label": break_objective_without_category_label,
    "signatory without signature": break_signatory_without_signature,
    "signature kind not in the list": break_signature_kind_unknown,
    "criterion number as text": break_criterion_number_as_text,
    "criterion number zero": break_criterion_number_zero,
    "unknown external code system": break_unknown_external_code_system,
    "unsure slot without value from an AI origin": break_unsure_without_value_from_ai,
    "unsure slot without value, derived without raw_text": break_unsure_without_value_derived_without_raw_text,
}
def break_block_index_outside_own_sources(instance: JsonObject) -> None:
    paragraph = verbatim_paragraph()
    paragraph["source_index"] = SAMPLE_SOURCE_INDEX + 1
    populate_with_block(instance, paragraph, "v10_verbatim")


def break_block_sources_outside_section_sources(instance: JsonObject) -> None:
    paragraph = verbatim_paragraph()
    other_source = {**v10_source("v10:2.3.1"), "source_indices": [SAMPLE_SOURCE_INDEX + 1]}
    populate_section(instance, [paragraph], v10_section_custody("v10_verbatim", [other_source]))


def break_section_not_flagged_for_ai_block(instance: JsonObject) -> None:
    populate_section(instance, [ai_paragraph()], ai_custody(needs_review=False, review=reviewed("accepted")))


def break_section_origin_ignores_ai_block(instance: JsonObject) -> None:
    verbatim = verbatim_paragraph()
    content = [verbatim, ai_paragraph()]
    populate_section(instance, content, v10_section_custody("v10_derived", sources_of(verbatim)))


def break_section_origin_all_ai_but_transformed(instance: JsonObject) -> None:
    custody = ai_custody(origin="ai_transformed", v10_sources=[v10_source("v10:2.1.1")])
    populate_section(instance, [ai_paragraph()], custody)


def break_section_origin_two_v10_sections_as_verbatim(instance: JsonObject) -> None:
    first = verbatim_paragraph()
    second = verbatim_paragraph()
    second["source_index"] = SAMPLE_SOURCE_INDEX + 1
    second["custody"]["v10_sources"] = [{**v10_source("v10:2.3.2"), "source_indices": [8]}]
    sources = sources_of(first) + sources_of(second)
    populate_section(instance, [first, second], v10_section_custody("v10_verbatim", sources))


def break_filled_field_in_not_applicable_section(instance: JsonObject) -> None:
    mark_fields_not_applicable(instance)
    fields = subsection(instance, "1.1.2")["fields"]
    fields["number_of_arms"] = filled_slot(2)


def break_original_protocol_with_amendment_identifier(instance: JsonObject) -> None:
    set_original_protocol_yes(instance)
    instance["title_page"]["amendment_identifier"] = filled_slot("Amendment 1")


def break_original_protocol_with_reason(instance: JsonObject) -> None:
    set_original_protocol_yes(instance)
    schema = load_json(SCHEMA_PATH)
    reason = filled_slot(first_code(schema, "ReasonForAmendment"))
    instance["amendment_details"]["primary_reasons"] = [reason]


def break_original_protocol_with_scope_identifier(instance: JsonObject) -> None:
    set_original_protocol_yes(instance)
    instance["title_page"]["amendment_scope_identifiers"] = [{"kind": "site", "site": filled_slot("S1")}]


def break_other_reason_without_other_code(instance: JsonObject) -> None:
    schema = load_json(SCHEMA_PATH)
    details = instance["amendment_details"]
    details["secondary_reasons"] = [filled_slot(first_code(schema, "ReasonForAmendment"))]
    details["secondary_reason_other"] = filled_slot("Sponsor decision")


def break_both_durations_filled(instance: JsonObject) -> None:
    fields = overall_design_fields(instance)
    fields["participation_duration"] = filled_slot(sample_duration(load_json(SCHEMA_PATH)))
    fields["participation_duration_description"] = filled_slot("Until the last visit")


def break_randomisation_type_without_randomisation(instance: JsonObject) -> None:
    fields = overall_design_fields(instance)
    fields["randomisation_type"] = filled_slot("Block randomisation")


def break_other_method_with_randomisation(instance: JsonObject) -> None:
    allow_assignment_method_details(instance)
    fields = overall_design_fields(instance)
    fields["other_assignment_method"] = filled_slot("Minimisation")


CROSS_FIELD_NEGATIVES: dict[str, Callable[[JsonObject], None]] = {
    "no sponsor role": break_no_sponsor,
    "summary row references unknown objective": break_unknown_objective_reference,
    "scope identifier with Global scope": break_scope_identifier_with_global,
    "objective numbering not sequential": break_objective_numbering,
    "duplicate estimand id": break_duplicate_estimand_id,
    "unknown v10 section_id": break_unknown_v10_section_id,
    "percent enrolled with two decimals": break_percent_with_two_decimals,
    "no intervention code or nonproprietary name": break_no_intervention_identifier,
    "block source_index outside its own sources": break_block_index_outside_own_sources,
    "block sources missing from the section sources": break_block_sources_outside_section_sources,
    "section not flagged although an AI block needs review": break_section_not_flagged_for_ai_block,
    "section origin ignores an AI block": break_section_origin_ignores_ai_block,
    "section of one AI block recorded as ai_transformed": break_section_origin_all_ai_but_transformed,
    "two v10 sections recorded as v10_verbatim": break_section_origin_two_v10_sections_as_verbatim,
    "filled field in a not applicable section": break_filled_field_in_not_applicable_section,
    "original protocol with an amendment identifier": break_original_protocol_with_amendment_identifier,
    "original protocol with a reason for amendment": break_original_protocol_with_reason,
    "original protocol with a scope identifier": break_original_protocol_with_scope_identifier,
    "other reason description without an Other reason": break_other_reason_without_other_code,
    "both slots of a duration pair filled": break_both_durations_filled,
    "randomisation type without Randomisation": break_randomisation_type_without_randomisation,
    "other assignment method with Randomisation": break_other_method_with_randomisation,
}


def two_blocks_sharing_an_index(first_selector: JsonObject, second_selector: JsonObject) -> JsonObject:
    schema = load_json(SCHEMA_PATH)
    instance = minimal_instance(schema)
    blocks = [
        table_block("v10_derived", "part", first_selector),
        table_block("v10_derived", "part", second_selector),
    ]
    sources = sources_of(blocks[0]) + sources_of(blocks[1])
    populate_section(instance, blocks, v10_section_custody("v10_derived", sources))
    return instance


def count_warnings(instance: JsonObject) -> int:
    errors, warnings = validate_document(instance)
    reused = [warning for warning in warnings if warning.startswith("source_index")]
    return len(reused) if not errors else -1


def default_source_ids(node: JsonObject) -> list[str]:
    return [entry["section_id"] for entry in node.get("x-v10-default-sources", [])]


def annotation_checks(record: Callable[[str, bool, str], None]) -> None:
    defs = load_json(ANNOTATED_SCHEMA_PATH)["$defs"]
    title_page = defs["TitlePage"]["properties"]
    details = defs["AmendmentDetails"]["properties"]
    expected_sources = {
        "original_protocol default source": (title_page["original_protocol"], ["v10:title_page.amendment_number"]),
        "version_number default source": (title_page["version_number"], ["v10:title_page.protocol_version"]),
        "product names default sources": (
            title_page["investigational_product_names"],
            ["v10:title_page.compound", "v10:6.1"],
        ),
        "amendment summary default source": (details["summary"], ["v10:front.amendment_summary.overall_rationale"]),
        "primary reasons default source": (details["primary_reasons"], ["v10:front.amendment_summary.overall_rationale"]),
        "substantial impact default sources": (
            details["substantial_impact_on_data_reliability"],
            ["v10:front.amendment_summary.amendment_heading", "v10:front.amendment_summary.substantial_statement"],
        ),
        "prior amendments default source": (
            defs["Fields_12_3"]["properties"]["prior_amendments"],
            ["v10:front.amendment_summary"],
        ),
    }
    for label, (node, expected) in expected_sources.items():
        found = default_source_ids(node)
        record(f"annotation: {label}", found == expected, str(found))
    address = title_page["sponsors"]["items"]["properties"]["legal_address"]["x-m11"]["ncit_code"]
    record("annotation: sponsor legal address code", address == SPONSOR_ADDRESS_CODE, address)
    section_defs = [node for name, node in defs.items() if name.startswith("Section_")]
    has_required = all("required" in node["x-v11"] for node in section_defs)
    record("annotation: every Section x-v11 has a required key", has_required)
    has_parts = all(isinstance(node.get("x-parts"), list) for node in section_defs)
    record("annotation: every Section has x-parts", has_parts)
    parts = defs["Section_9_2_2"]["x-parts"]
    record("annotation: 9.2.2 x-parts lists its M11 elements", len(parts) > 1, f"{len(parts)} parts")
    template_title = defs["Section_9_2_3__sade_reporting_to_x"]["properties"]["title"]
    record("annotation: placeholder title has x-title-template", "x-title-template" in template_title)


def run_checks(write_dir: Path | None) -> int:
    schema = load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    print("PASS schema passes Draft202012Validator.check_schema")
    failures = 0

    def record(label: str, passed: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if passed else 1
        print(f"{'PASS' if passed else 'FAIL'} {label}{' - ' + detail if detail else ''}")

    def save(name: str, instance: JsonObject) -> None:
        if write_dir is not None:
            write_dir.mkdir(parents=True, exist_ok=True)
            with open(write_dir / f"{name}.json", "w", encoding="utf-8") as handle:
                json.dump(instance, handle, indent=2, ensure_ascii=False)

    for name, build in (("minimal", minimal_instance), ("populated", populated_instance)):
        instance = build(schema)
        save(name, instance)
        errors, _warnings = validate_document(instance)
        record(f"positive: {name} instance validates", not errors, "; ".join(errors[:3]))

    for label, mutate in POSITIVES.items():
        instance = minimal_instance(schema)
        mutate(instance)
        save("positive_" + label.replace(" ", "_").replace("/", "_"), instance)
        errors, _warnings = validate_document(instance)
        record(f"positive: {label} validates", not errors, "; ".join(errors[:3]))

    expectations = [(SCHEMA_NEGATIVES, "schema"), (CROSS_FIELD_NEGATIVES, "cross-field")]
    for negatives, layer in expectations:
        for label, mutate in negatives.items():
            instance = minimal_instance(schema)
            mutate(instance)
            save("negative_" + label.replace(" ", "_").replace("/", "_"), instance)
            errors, _warnings = validate_document(instance)
            record(f"negative ({layer}): {label} is rejected", bool(errors), errors[0][:140] if errors else "no error raised")
    same = count_warnings(two_blocks_sharing_an_index({"rows": [0]}, {"rows": [0]}))
    record("warning: a source_index reused with the same selector", same == 1, f"{same} warning(s)")
    different = count_warnings(two_blocks_sharing_an_index({"rows": [0]}, {"rows": [1]}))
    record("no warning: a source_index reused with different selectors", different == 0, f"{different} warning(s)")
    annotation_checks(record)
    print(f"{failures} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    write_dir = Path(argv[argv.index("--write-dir") + 1]) if "--write-dir" in argv else None
    return run_checks(write_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
