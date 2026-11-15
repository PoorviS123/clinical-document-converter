# Protocol JSON schema: design spec

All paths are relative to the repository root.
Inputs (verified research, read-only): `resources/research/schema-inputs/`
- `v11_inventory.json` — 207 nodes, every v11 heading/title-page field in document order (keys: id, group, number, level, title, title_exact, style, optional, optional_basis, required, text_expected{value,citation}, not_applicable_rule, value_slots, repeating_structure, key_instructions, heading_policy, m11, parent_number)
- `v10_section_ids.json` — canonical v10 ids (`ids` list, `id_patterns`, `reserved_ids`)
- `crosswalk_verified.json` — `rows` (v10_number, v10_title, v11_targets[{number,title,portion}], change_type, confidence, evidence), `v11_without_v10_source`, `multi_landing`
- `m11_elements.json`, `m11_codelists.json` — M11 Step 4 data elements + codelists with NCIt codes
- notes: `v11_inventory_notes.md`, `v10_notes.md`, `crosswalk_findings.md`, `m11_notes.md`
Existing example of the per-section JSON this replaces: `tests/test1_v10_section1.json` (made by `tests/extract_v10_section1.py`, consumed by `tests/insert_v11_section1.py`).

## Purpose

ONE JSON document per protocol. A v10 protocol is extracted into it; a v11 docx is rendered from it. It is shaped like the **v11** template. Every value and every content block records its **chain of custody**: which v10 section it came from (template section id + what was actually found in the file), or that AI wrote it, or that it is v11 template text, or that a human entered it.

## Deliverables

1. `schema/build_protocol_schema.py` — generator. Reads the inputs above, writes `schema/protocol.schema.json` (annotations stripped) and `schema/protocol.schema.annotated.json` (with `x-` keys, `description` and `default`). Deterministic output (sorted where order is not meaningful, `indent=2`, `ensure_ascii=False`). Follow the style rules in `~/.claude/CLAUDE.md`: readable code, no magic numbers, short descriptive names, no `Any` in new code, comments only for non-obvious why. Type hints. Python 3.12+, run with `.venv/bin/python`. Stdlib + jsonschema only.
2. `schema/protocol.schema.json` — JSON Schema draft 2020-12, `$id` "https://medconvert.local/schema/protocol.schema.json", title "CPT v10 to v11 protocol".
3. `schema/validate_protocol.py` — `python validate_protocol.py <protocol.json>`: validates with `jsonschema.Draft202012Validator` (format checking on), prints every error with its JSON path, and runs the cross-field checks listed under "Checks the schema cannot express". Exit 1 on any error.

## Top level

```
{
  "schema_version": "1.0.0",                       // const for this schema
  "source": { "path": str, "file_name": str, "sha256": str (64 hex), "template": const "CPT v010", "extracted_at": date-time },
  "target": { "template": const "CPT v011", "template_file": str },
  "title_page": TitlePage,
  "amendment_details": AmendmentDetails,
  "sections": { "1": Section_1, ..., "14": Section_14 },   // all 14 required, additionalProperties false
  "unplaced_v10_content": [UnplacedItem],          // v10 content with no v11 home. Nothing may vanish silently.
  "processing_log": [ProcessingStep]               // every tool/AI/human pass over this document, in order
}
```
All objects `additionalProperties: false` unless stated.

## Chain of custody (`$defs/Custody`) — the core of the schema

```
Custody {
  origin: enum
    "v10_verbatim"   // copied unchanged from the v10 file (renderer copies the XML via source_index)
    "v10_derived"    // taken from v10 by deterministic code only (no model) and reshaped (eg "90 weeks" parsed out of a bullet, a table cell split into fields)
    "ai_transformed" // an AI model rewrote, condensed, split or classified v10 content (eg mapped free text to an M11 code)
    "ai_generated"   // an AI model wrote it with no v10 source
    "template_text"  // headings and fixed sentences copied from the v11 template; never a decision such as "Not applicable"
    "human_entered"  // typed or edited by a person
    "none"           // nothing was written (missing / container / omitted)
  v10_sources: [V10Source]
  actor: Actor
  confidence: enum high | medium | low
  needs_review: bool
  review: Review            // optional
  edited_from: { origin, actor }   // optional; only when origin is human_entered
  note: str                 // optional
}
```
Rules (encode with if/then in the schema):
- origin in {v10_verbatim, v10_derived, ai_transformed} → v10_sources minItems 1.
- origin in {ai_generated, template_text, none} → v10_sources maxItems 0.
- origin in {ai_transformed, ai_generated} → actor.kind const "llm" and actor.model required.
- origin = v10_derived → deterministic code only → actor.kind const "script". A model that reshapes v10 text uses `ai_transformed`.
- origin = human_entered → actor.kind const "human".
- origin = template_text → actor.kind const "script". `actor` is required for every origin; for `none` and `template_text` it is the tool that built the document.
- origin in {ai_transformed, ai_generated} → needs_review const true unless review.status is "accepted" or "edited". (Simplest encoding: if origin is ai_* and review absent → needs_review const true.)
- confidence "low" → needs_review const true unless review.status is "accepted" or "edited". The same holds for a slot with status `unsure`.
- review.status "unreviewed" or "rejected" → needs_review const true, for every origin.
- review.by and review.at are required when review.status is "accepted", "edited" or "rejected".
- A reviewer edit that changes a value or text sets `origin: human_entered`, `actor: {kind: human, name: <reviewer>}`, keeps the original `v10_sources`, sets `review: {status: edited, by, at}` and `needs_review: false`. The previous origin and actor go in `edited_from: {origin, actor}`, and the pass is also logged as a `processing_log` step. review.status "edited" requires origin human_entered and `edited_from`; `edited_from` is allowed only when origin is human_entered. An `accepted` review without a text change keeps the AI origin.
- `not_applicable` status (sections and slots): if the v10 file says so (a heading with "Not applicable" or equivalent text, or the extractor sets it by rule from v10 content), custody is `v10_derived` with the v10 source and `actor.kind: script`. If a model decided it, custody is `ai_generated` (no v10 source) or `ai_transformed` (v10 sources that led to the decision), `actor.kind: llm`, `needs_review: true`. `template_text` and `none` are rejected for a `not_applicable` status, because the words are template text but the decision is not.
- Section custody is derived, not chosen. A node with `status`, `content` and `custody` (every Section, AdditionalSubsection, ObjectiveAnalysis and analysis subsection) takes its origin from its items: the content blocks plus every slot under `fields`. Items with origin `template_text` or `none` are ignored; when nothing is left the origin is not checked. In order: (1) any AI item: `ai_generated` if all items are `ai_generated`, else `ai_transformed`; (2) all items `v10_verbatim` and all their sources share one `section_id`: `v10_verbatim`; (3) all items `v10_verbatim` or `v10_derived`: `v10_derived`; (4) anything else (for example `human_entered` mixed in with no AI): `human_entered`. Rule 4 is an assumption; confirm it. The section's `v10_sources` must contain every source index of its blocks, and its `needs_review` is true if any block's is (validator checks 10-13).
- `unsure` slot with a null value: origin is `none`, or `v10_derived` with `raw_text` (the text that could not be resolved).
Policy (put in `description`): never record a v10 origin without a resolvable source_index. If you cannot tell whether text came from v10, it is ai_generated (or ai_transformed with the v10 sources you do know) — when in doubt, attribute to AI.

```
V10Source {
  section_id: str, pattern from v10_section_ids.json id_patterns combined:
     ^v10:(title_page\.[a-z0-9_]+|front\.[a-z0-9_.]+|1\.1\.synopsis\.[a-z0-9_]+|\d+(\.\d+){0,4}|(\d+(\.\d+){0,4}|x)\.(unnumbered|label)\.[a-z0-9_]+|(\d+(\.\d+){0,4}|x|body)\.unmapped)$
     // the TEMPLATE section this content corresponds to. Real protocols renumber (test1: 27 headings renumbered, Section 9 restructured), so this is the matched template id, not the number printed in the file.
  found_number: str | null    // number as it appears in the protocol file
  found_heading: str | null   // heading text as it appears in the protocol file
  match: enum exact | renamed | renumbered | renamed_and_renumbered | protocol_added | inferred
     // protocol_added: heading not in the v10 template. section_id is always "<nearest preceding template parent>.unmapped" (v10:body.unmapped for a new H1 after the last section or before Section 1), never the number printed in the file; the printed number goes in found_number and the heading in found_heading. The schema requires the ".unmapped" suffix when match is protocol_added.
  source_indices: [int >= 0] minItems 1   // positions in document.element.body children of the v10 file
  portion: enum all | part
  selector: { rows?: [int], columns?: [int], row_labels?: [str] }   // optional; for a partial table source, the 0-based rows and columns of the v10 table that were used
}
`V10Source.source_indices` lists every v10 body index the content came from. `Block.source_index` is the single index the renderer copies, and it must be in the block's own `custody.v10_sources[].source_indices` (validator check 10).
Actor { kind: enum script | llm | human, name: str, version: str (optional), model: str (optional) }
Review { status: enum unreviewed | accepted | edited | rejected, by: str, at: date-time, note: str }  // by/at/note optional
```

## Slots (typed leaf values)

Generic shape, one `$defs` entry per value type (TextSlot, IntegerSlot, NumberSlot, DateSlot, DateOrTextSlot, plus one `Coded_<Name>Slot` per codelist used, ExternalCodeSlot, DurationSlot):
```
{ value: <type> | null, status: SlotStatus, custody: Custody, raw_text: str (optional, the v10 text the value was read from), note: str (optional) }
SlotStatus: enum filled | unsure | missing | not_applicable
```
Rules: filled → value not null. missing / not_applicable → value null. missing → custody.origin "none". unsure → value may be a candidate or null; custody.needs_review const true unless the review accepted or edited it. unsure with a null value → origin `none`, or `v10_derived` with `raw_text` required.
- NumberSlot value: number >= 0 (used by approximate_enrolled.quantity).
- TextSlot value: string minLength 1. Optional `maxLength` passed per use (Trial Acronym 14, Short Title 300).
- DateSlot value: string format "date" (ISO). The original wording goes in raw_text.
- DateOrTextSlot value: { date: date } or { text: str } (Sponsor Approval).
- CodedValue: { code: NCIt code `^C\d+$`, decode: str }. For each codelist in m11_codelists.json that is used as a value list (not the "ICH M11 Section N Terminology" lists, which are heading lists), generate `$defs/Code_<PascalName>` = object with oneOf over the terms, each `{properties:{code:{const}, decode:{const}}}`. Use the term's `submission_value` as decode. All codelists are non-extensible (checked). Add `x-codelist: {ncit_code, name}`.
- ExternalCode: { system: enum MedDRA | ISO 3166-1 | ISO 3166-2 | GENC | EDQM | IDMP | SNOMED CT | UCUM | WHO INN | CDISC SDTM | other, code: str, decode: str } — for Population Diagnosis or Condition (MedDRA), Country/Region identifiers, dose form, route.
- Duration: { quantity: integer >= 0 (number allowed? M11 says integer), unit: Code_UnitOfMeasure }.
Every slot property in the schema gets `x-m11: {element, ncit_code, conformance, cardinality}` from m11_elements.json where one exists, and `description` with the M11 definition (short).

## Content blocks (`$defs/Block`, oneOf by `type`)

```
Paragraph { type: const "paragraph", text: str, style: str (Word style name as found; for AI text use the v11 body style "Normal"), source_index: int (optional), has_image: bool (optional), part: str (optional), custody: Custody }
Table     { type: const "table", rows: [[str]], source_index: int (optional), part: str (optional), custody: Custody }
Heading   { type: const "heading", text: str, level: int 1..9, source_index: int (optional), part: str (optional), custody: Custody }
           // a v10 sub-heading that has no v11 heading of its own and is carried inside the v11 section's content
```
`part` names the M11 element or v11 sub-label the block belongs to (for example "Severity" in 9.2.2); the section's `x-parts` annotation lists the M11 elements of that section (element name, code, conformance). A sub-label printed as a heading is a Heading block with `origin: template_text`.

Rule: a Table or Heading with origin v10_verbatim has every source at `portion: all`; a Paragraph does too unless `strip_prefix_chars > 0`. A partial table source is `v10_derived` with a `selector`. Check 4 skips repeated indices whose selectors differ.

Rule: custody.origin v10_verbatim → source_index required (the renderer copies that body element). Text is kept for checking the file has not changed (insert_v11_section1.py already compares it).

Paragraph also has `strip_prefix_chars` (optional, integer >= 0): the number of leading characters of the v10 paragraph (after accepting tracked changes) that are a label and must be removed when copying. `text` is the paragraph text after removal. Renderer check: `visible_text(source).strip()[strip_prefix_chars:].strip() == text`. A paragraph with `strip_prefix_chars > 0` is `v10_verbatim` and its custody has a `v10_sources` entry with `portion: part`. A bare-number inline value (`inline_value.isdigit()`) is put in its slot and not copied as a Paragraph, as the current script does.

## Section nodes

One `$defs/Section_<number with dots as underscores>` per v11 heading, generated from v11_inventory.json group "body". Shape:
```
{
  number: const "<v11 number>",
  title: str  (default = template title; bracketed placeholder titles like "Criteria for Temporarily Delaying [Enrollment/Randomization/...]" are resolved by the populator),
  status: SectionStatus (per-section enum, see below),
  heading_source: V10Source | null,   // the v10 heading this section was matched to; null when none matched
  custody: Custody,          // how the section as a whole was populated (the mapping used)
  content: [Block],
  subsections: { "<child number>": Section_<child>, ... }   // only if it has children; all children required
  additional_subsections: [AdditionalSubsection]           // required key on every Section; may be empty
  fields: {...}              // only for sections with structured fields (below)
}
```
An `AdditionalSubsection` is a heading the protocol added that has no v11 heading: level 3 and deeper headings in any section, and additional appendices in section 12 (numbered 12.5 and up, M11 12.X Additional Appendices, C220640):
```
AdditionalSubsection { number: str | null (as printed), title: str, level: int 1..9, status: populated | container,
  heading_source: V10Source (match const protocol_added), custody, content: [Block], subsections: [AdditionalSubsection] }
```
It uses `SectionStatusRules`. A v10 heading with no v11 heading that the extractor keeps inside a v11 section as text is a Heading block instead. `unplaced_v10_content` stays for content with no slot at all.
`heading_source` records the v10 heading that matched this section, even for container, missing and not_applicable sections. It is independent of `custody`, which describes the content only. `heading_source.portion` is always `all`. The key is required on every Section, RepeatedSubsection, ObjectiveAnalysis and Objective (value null when no v10 heading matched).

SectionStatus values: "populated" | "not_applicable" (renderer writes "Not applicable") | "omitted" (heading removed from output) | "missing" (expected, nothing found; renderer writes a red NOT EXTRACTED marker) | "container" (heading only, no text by design).
Per section:
- Body nodes have `text_expected.value` `none`, `none_unless_not_applicable` or `text`. (`fields_only` occurs only on the Title Page node and `generated` only on the table of contents; the template-instructions group produces no node.) "none" → status enum ["container"] and content maxItems 0; "none_unless_not_applicable" (3.2, 3.3, 10.5) → enum ["container", "not_applicable"], content maxItems 0; "text" → enum ["populated", "not_applicable", "missing"].
- "omitted" is added to the enum when the node or any ancestor has inventory `optional` true. If a Section's status is omitted, every child's status is const omitted; if it is not omitted, a child that is not itself optional may not be omitted (the case is 9.1.3.1 and its children n135-n137).
- status in {not_applicable, omitted, missing, container} → content maxItems 0 (encode in the generic `$defs/SectionStatusRules`).
- 3.2, 3.3 and 10.5: status `container` requires at least one objective (or objective analysis); `not_applicable` requires none. 3.1 and 10.4 always have at least one.
- In a section whose status is not_applicable or missing, every slot under `fields` has that same status (validator check 18).
- Annotations on every Section def (non-validating `x-` keywords):
  `x-v11`: {style, optional, optional_basis, required, required_basis, text_expected, not_applicable_rule, heading_policy}. `required` and `required_basis`, `title_exact` and `heading_policy` are optional keys in the inventory (read them with a default).
  `x-parts`: the M11 elements whose `m11_section` equals this section's number: [{element, ncit_code, conformance}]. Blocks name one with `part`.
  `x-guidance`: key_instructions (list of strings, verbatim from inventory)
  `x-m11`: inventory `m11` value
  `x-v10-default-sources`: from crosswalk_verified.json rows whose v11_targets resolve to this section: [{section_id, v10_title, portion, part (when the target has one), change_type, confidence, evidence}]. `part` is the crosswalk target's own label for which part of the target section the v10 content belongs in (e.g. "Severity"); `portion` is the target's all/part.
  Crosswalk `v10_number` to v10 id: plain numbers → `v10:<number>`; TP.1-TP.17 and SYN.1-SYN.11 by order as listed; TP.6b → null; TP.18/TP.18a/TP.18d → v10:front.amendment_summary; TP.18b → v10:front.amendment_summary.amendment_heading and v10:front.amendment_summary.substantial_statement; TP.18c → v10:front.amendment_summary.overall_rationale; TP.19 → v10:front.table_of_contents; TP.20 → v10:front.list_of_abbreviations; FM has no targets and no id. Suffixed rows: 3.u1 → v10:3.unnumbered.estimands_for_primary_objectives; 3.u2 → v10:3.label.supplementary_estimands; 3.u3 → v10:3.unnumbered.estimands_for_secondary_objectives; 3.u4 → v10:3.label.supplementary_estimands_2; 3.u5 → v10:3.unnumbered.estimands_for_tertiary_exploratory_other_objecti; 3.u6 → v10:3.label.tertiary_exploratory_other_estimands_label and v10:3.label.supplementary_estimands_3 (3.3.1 gets both); 10.7.1a → v10:10.7.1.label.medical_device_ae_and_ade_definition; 10.7.2a → v10:10.7.2.label.sade_definition and v10:10.7.2.label.unanticipated_sade_usade_definition; 10.7.6a → v10:10.7.6.label.sade_reporting_to_x; 10.9a → v10:10.9.label.amendment_amendment_number_date. Verify each mapping by title and report a mismatch.
  Resolve a crosswalk target to an inventory node by number when the number exists in the inventory, else by normalised title among body nodes: `9.1.4` → 9.1.3.1, `9.2.1.1` → the node 9.2.1#time_period_for_detecting_medical_device_deficiencies; `title-page` targets belong to the TitlePage property named by the target title. Fail loudly when a target resolves to nothing. An unnumbered child also gets the entries whose target resolves to its parent and whose `part` equals the child's title (e.g. part "Recording" → 9.2.2#recording); the parent keeps all of them.
  `x-no-v10-source`: a node listed in v11_without_v10_source that also has default sources keeps both annotations; `x-no-v10-source.status` says what the entry means (container_with_library_text: sources come from children). Also add `x-no-v10-source` with the note when the number is in `v11_without_v10_source`.
- Unnumbered inventory nodes (number null, parent_number set: 9.1.3.1 children, 9.2.1/9.2.2/9.2.3 children) become subsections keyed "<parent_number>#<snake_case_title>", with `number: null` allowed (const null) and `title` const of the template title. A title containing a `[...]` placeholder (n145 "SADE Reporting to [X]") is a free string with `x-title-template` instead of a const.
- 12.3's two unnumbered nodes are NOT subsections; they become 12.3 fields (below).

### Repeating structures (replace inventory nodes 3.1.1, 3.2.1, 3.3.1, 10.4.1 + 10.4.1.1-5, 10.5.1 + 10.5.1.1-5)

Section 3.1 gets `objectives: [PrimaryObjective] minItems 1`; 3.2 `objectives: [SecondaryObjective]`; 3.3 `objectives: [ExploratoryObjective]`. These replace `subsections` for those nodes.
```
Objective {
  id: str pattern ^(primary|secondary|exploratory)-\d+$     // stable link target
  number: str pattern ^3\.[123]\.\d+$
  title: str                      // "Primary Objective 1"
  heading_source: V10Source | null
  category_label: TextSlot        // the v10 category as written (eg "Key secondary")
  statement: TextSlot             // M11 C85826 / C85827 / C163559
  estimands: [Estimand]           // primary: minItems 1 (M11: endpoint required); secondary/exploratory: may be empty
                                  // an unextractable primary objective is one Objective whose statement.status is missing
  additional_attributes: [{ label: str, value: TextSlot }]   // v10 columns with no M11 home, eg test1 "Assessments". Keeps data instead of dropping it.
  content: [Block]                // any other narrative under the objective heading
  custody: Custody
}
Estimand {
  id: str pattern ^(primary|secondary|exploratory)-\d+-estimand-\d+$
  population: TextSlot, treatment: TextSlot, endpoint: TextSlot, population_level_summary: TextSlot,
  intercurrent_events: [{ description: TextSlot, strategy: TextSlot }],
  custody: Custody
}
```
When both the v10 synopsis (SYN.6) and v10 section 3 give objectives, the section 3 Objective is built from v10 section 3 and the synopsis row links to it through `objective_id`. If only the synopsis exists, the Objective is `v10_derived` with `v10_sources` listing `v10:1.1.synopsis.objectives_endpoints_estimands`.

Section 10.4 gets `objective_analyses: [ObjectiveAnalysis] minItems 1`, 10.5 `objective_analyses: [ObjectiveAnalysis]`. ObjectiveAnalysis `content` is allowed for introductory text under the objective heading, although the inventory says `none` for 10.4.1 and 10.5.1 (v10 9.3.1 and 9.4.1 carry text):
```
ObjectiveAnalysis {
  objective_id: str (primary-N for 10.4, secondary-N for 10.5),
  number: pattern ^10\.4\.\d+$ (or 10.5),
  title: str, status: SectionStatus (populated|not_applicable|missing), heading_source, custody, content: [Block],
  subsections: { statistical_analysis_method, handling_of_data, handling_of_missing_data, sensitivity_analysis, supplementary_analysis }
     // each a RepeatedSubsection { number: pattern ^10\.[45]\.\d+\.[1-5]$, title, status (sensitivity/supplementary also "omitted"), heading_source, custody, content }
     // titles from the template: Statistical Analysis Method / Handling of Data in Relation to Primary|Secondary Estimand(s) / Handling of Missing Data in Relation to ... / Sensitivity Analysis / Supplementary Analysis
}
```

### Section fields (hand-designed; put each in `fields`, all keys required)

Arrays may be empty unless M11 marks the element Required: those have `minItems 1`, and when nothing was found the single item is a slot with `status: missing`. Conditional and Optional elements may be empty.

1.1.1 `fields.summary_rows: [{ objective_id: str (must reference a section 3 objective), objective: TextSlot, estimand_summary: TextSlot }]` — v11 synopsis table "Objective | Estimand (treatment effect)", primary and secondary only.

1.1.2 `fields` (M11 1.1.2 elements; use the exact codelists):
- intervention_codes: [TextSlot] (C218675), intervention_nonproprietary_names: [TextSlot] (C97054). Required as an either/or pair, so neither has `minItems`; validator check 8 covers it.
- intervention_model: Coded IntervModel slot (C98746, Intervention Model Response Terminology)
- population_type: coded (Population Type)
- population_diagnosis_or_condition: [ExternalCodeSlot] (MedDRA, C112038), minItems 1
- control_type: coded (Control Type); control_description: TextSlot (M11 id 106, the free-text composite); control_nonproprietary_names: [ExternalCodeSlot] (C97054, id 107); control_inns: [ExternalCodeSlot] (C142585, id 108); control_description_not_applicable: TextSlot (id 109, "Not applicable" when there is no name and no INN)
- population_age: { minimum: IntegerSlot, minimum_unit: coded Unit of Measure, maximum: IntegerSlot, maximum_unit: coded Unit of Measure }
- intervention_assignment_method: coded; randomisation_type: TextSlot (C223137); other_assignment_method: TextSlot (C223138)
- stratification: coded No Yes (C223136)
- site_distribution: coded; site_geographic_scope: coded
- master_protocol: coded No Yes; drug_device_combination_product: coded No Yes; adaptive_trial_design: coded No Yes
- number_of_arms: IntegerSlot (C98771)
- trial_blind_schema: coded (Trial Blinding Schema); blinded_roles: [coded Trial Blinding Role slot], minItems 1
- participant_number_qualifier: slot with value enum ["Target","Maximum"] (C218710, inline pick-list, no NCIt codes)
- number_of_participants: IntegerSlot (C49692)
- participant_number_basis: slot value enum ["randomly assigned to trial intervention","enrolled"] (C218711)
- intervention_duration: DurationSlot (C218712); intervention_duration_description: TextSlot (C218714)
- participation_duration: DurationSlot (C218715); participation_duration_description: TextSlot (C218717)
- additional_duration_description: TextSlot (C218838)
- independent_committees: [coded Independent Committee Name slot], minItems 1; other_committees: TextSlot (C218719)
- Conditional rules the validator checks: randomisation_type only when intervention_assignment_method is Randomisation; other_assignment_method only when it is Other; of each duration/description pair (C218712/C218714, C218715/C218717) at most one is filled.

5.2 / 5.3 `fields.criteria: [{ number: int >= 1 | null, text: TextSlot }]` (C25532 / C25370). Content keeps intro text.

6 `fields`: overview: TextSlot (C218747); interventions: [{ arm_name: TextSlot, arm_type: coded Trial Arm Type, intervention_name: TextSlot, intervention_type: coded Intervention Type, dose_form: ExternalCodeSlot, dosage_strengths: TextSlot, dosage_levels: TextSlot, route: ExternalCodeSlot, regimen: TextSlot, use: coded Intervention Use, imp_or_nimp: coded IMP indicator, sourcing: [coded Sourcing slot] }] (v011 table omits Sourcing but its instructions list it; keep it).

9.2 `fields.safety_event_reporting: [{ event_type: coded Trial-Related Safety Event Type, other_event_type: TextSlot, situational_scope: TextSlot, reportable_period_start: TextSlot, reportable_period_end: TextSlot, timing_for_reporting: TextSlot, reporting_method: TextSlot, backup_reporting_method: TextSlot }]`.

12.2 `fields.country_regions: [{ identifier: ExternalCodeSlot (C218832), requirements: [Block] (C218833), clarifications: [Block] (C218834), custody: Custody }]`, one entry per country or region. The list is empty when 12.2 is not_applicable or missing.

12.3 `fields`: amendment_statement: coded Amendment Details Statement slot (C218835); overview_location: TextSlot (C218836 "<specify alternative location>"); prior_amendments: [{ amendment_identifier: TextSlot, sponsor_approval_date: DateSlot, approximate_enrollment: TextSlot, enrollment_scope: coded Amendment Scope Enrollment Description, changes: [AmendmentChange] }]; original_protocol_approval_date: DateSlot.

## TitlePage (`$defs/TitlePage`, hand-designed, M11 Title Page elements)

- sponsor_confidentiality_statement: TextSlot
- full_title: TextSlot
- full_title_components: { intervention_model, primary_purpose, trial_phase, blinding, number_of_arms (IntegerSlot), health_measurement_or_outcome, investigational_intervention, intervention_form, participant_sex, participant_age_range, condition_or_disease } — all TextSlot except number_of_arms. These are the v11 suggested-title helper placeholders, not M11 elements.
- trial_acronym: TextSlot maxLength 14
- sponsor_protocol_identifier: TextSlot
- original_protocol: coded No Yes (C218672)
- version_number: TextSlot; version_date: DateSlot
- amendment_identifier: TextSlot; amendment_scope: coded Amendment Scope
- amendment_scope_identifiers: [{ kind: enum country | region | site, country_or_region: ExternalCodeSlot (kind country or region only), site: TextSlot (kind site only) }]  (C20108; empty unless scope = Not Global)
- investigational_product_codes: [TextSlot]
- investigational_product_names: [{ kind: enum nonproprietary | proprietary, name: TextSlot }]
- trial_phase: coded Trial Phase
- short_title: TextSlot maxLength 300
- sponsors: [{ role: enum sponsor | co_sponsor | local_sponsor, name: TextSlot, legal_address: TextSlot }] minItems 1 (exactly one "sponsor" role: put in validator). `x-m11.ncit_code` of the sponsor legal address is C218677; the M11 PDF repeats Sponsor Name's C222495 there and the terminology wins.
- device_manufacturers: [{ name: TextSlot, legal_address: TextSlot }]
- regulatory_identifiers: [{ type: enum eu_ct_number | fda_ind_number | ide_number | jrct_number | nct_number | nmpa_ind_number | who_utn_number | pediatric_investigational_plan_number | other, label: str (as printed in the "Label" column), id: TextSlot }]  // x-m11 on each enum value via description listing codes C218684..C218690, C172240
- sponsor_approval: DateOrTextSlot (C132352). The M11 PDF marks it repeatable; it is kept single.
- sponsor_signatories: [{ name: TextSlot, title: TextSlot, date: DateSlot, signature: { kind: enum image | text | location, value: TextSlot } }]
- medical_expert_contact: TextSlot (C218693)
Each property also gets `x-v10-default-sources` from the TP.* crosswalk rows (e.g. sponsor_protocol_identifier ← v10:title_page.protocol_number, trial_phase ← study_phase, investigational_product_codes ← compound, short_title ← brief_title, trial_acronym ← acronym, sponsors ← sponsor_name + legal_registered_address, device_manufacturers ← manufacturer, regulatory_identifiers ← regulatory_agency_identifier_numbers + pediatric_investigational_plan_number, sponsor_approval ← approval_date, sponsor_signatories ← sponsor_signatory, medical_expert_contact ← medical_monitor_name_and_contact_information, amendment_identifier ← amendment_number, amendment_scope + amendment_scope_identifiers ← amendment_scope + country_region_identifier, full_title ← protocol_title). Check these against crosswalk rows; report any disagreement instead of silently choosing.

Default sources beyond the TP.* rows: `investigational_product_names` gets `v10:title_page.compound` (row TP.6, whose evidence sends a v10 name value there) and `v10:6.1` (row TP.6b, "found only in the intervention section"; the row's title does not match `v10:title_page.compound`, so it is not mapped there). `original_protocol` gets a hand-built `v10:title_page.amendment_number` entry (`change_type: derived`: an empty amendment number means original). `version_number` gets a hand-built `v10:title_page.protocol_version` entry with `change_type: protocol_added` (a field some protocols add; not in `v10_section_ids.json`; as a `V10Source` match it must use `v10:title_page.unmapped`).

## AmendmentDetails (`$defs/AmendmentDetails`, M11 Amendment Details elements)

- statement: coded Amendment Details Statement slot (C218694)
- approximate_enrolled: { quantity: NumberSlot, unit: enum number | percent } (C218478). Unit number: integer. Unit percent: at most 100 and one decimal (the decimal is a validator check, because `multipleOf: 0.1` rejects valid floats such as 0.3 and 12.7). 12.3 `approximate_enrollment` stays a TextSlot.
- enrollment_scope: coded Amendment Scope Enrollment Description (C218695)
- primary_reasons: [coded Reason for Amendment] (C218696, multiple values allowed); primary_reason_other: TextSlot (C17649, only when a reason has code C17649)
- secondary_reasons: [coded Reason for Amendment] (C218697); secondary_reason_other: TextSlot
- summary: TextSlot (C42581)
- substantial_impact_on_safety_or_rights: coded No Yes (C218698); safety_or_rights_explanation: TextSlot (C218699)
- substantial_impact_on_data_reliability: coded No Yes (C218700); data_reliability_explanation: TextSlot (C218701)
- changes: [AmendmentChange]
Default sources per property: `summary` and `primary_reasons` from TP.18c; `substantial_impact_*` from TP.18b; `changes` from TP.18d; 12.3 `prior_amendments` from TP.18a.
AmendmentChange { description: TextSlot (C218483), rationale: TextSlot (C181233), section: coded Protocol Section Name and Number (C218479) slot }

## UnplacedItem / ProcessingStep

```
UnplacedItem { v10_source: V10Source, reason: enum no_v11_slot | dropped_per_crosswalk | unrecognized | duplicate_of_placed_content | v10_instruction_text, blocks: [Block], note: str (optional) }
ProcessingStep { step: str, actor: Actor, at: date-time, summary: str }
```

## Checks the schema cannot express (implement in validate_protocol.py)

1. Every `objective_id` (1.1.1 summary rows, 10.4/10.5 analyses) resolves to an objective in section 3 of the right kind.
2. Objective ids and estimand ids are unique; objective numbers are sequential within 3.1/3.2/3.3.
3. Exactly one sponsor with role "sponsor".
4. Every source_index used anywhere is unique per block (a v10 body element is not copied twice) — warn, not error, because a split section can legitimately reuse a table; print as WARNING. A repeat whose `selector` differs is not warned.
5. Every `v10_sources[].section_id` exists in v10_section_ids.json, matches the numbered-heading pattern, or is a `.unmapped` id. `match: protocol_added` gets no exemption: its id must end in `.unmapped`.
6. If `source.path` exists on disk, its sha256 matches `source.sha256` (warn if the file is absent).
7. `amendment_scope_identifiers` is empty unless amendment_scope is "Not Global".
8. At least one of `intervention_codes` and `intervention_nonproprietary_names` has a slot that is not missing, unless both are one missing slot.
9. A percent `approximate_enrolled.quantity` has at most one decimal.
10. A block's `source_index` is in its own `custody.v10_sources[].source_indices`.
11. Every source index of a block is in the section's `custody.v10_sources`.
12. A section's `custody.needs_review` is true if any block's is.
13. A section's `custody.origin` equals the origin derived from its blocks and field slots (see Chain of custody).
14. If `original_protocol` is Yes: the amendment identifier, amendment scope and the Amendment Details conditional slots have no value, and `amendment_scope_identifiers`, both reason arrays and `changes` are empty.
15. A `*_reason_other` slot has a value only if a reason in its array has code C17649.
16. In each duration pair (C218712/C218714, C218715/C218717) at most one slot has a value. Neither having a value is allowed: the `missing` status is the signal.
17. `randomisation_type` has a value only when the assignment method is Randomisation; `other_assignment_method` only when it is Other.
18. In a section whose status is not_applicable or missing, every slot under `fields` has that status.

## Self-test you must do before reporting

- Generate the schema; validate the schema itself with `Draft202012Validator.check_schema`.
- `schema/test_protocol_schema.py` builds a minimal instance from the schema itself (it builds one item for any array with `minItems`), then runs the positive, schema-negative and cross-field-negative cases and the annotation checks. It must end with `0 failure(s)`. `--write-dir DIR` saves every instance for inspection; use the session scratchpad directory, not the repository. Every new schema rule gets a positive and a negative case; every new validator check gets a cross-field negative.
- Report: schema size, number of Section defs, any inventory/crosswalk/M11 data you had to interpret or found inconsistent.
