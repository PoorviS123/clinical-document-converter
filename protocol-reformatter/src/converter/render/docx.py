"""Render protocol JSON (schema/protocol.schema.json) to a v011 docx on the CPT template."""

import re
from pathlib import Path

from docx.enum.text import WD_COLOR_INDEX
from docx.shared import RGBColor

from block_xhtml import xhtml_to_blocks
from converter.title_page import TITLE_PAGE_UNMAPPED_ID, render_values
from pdf_to_cpt_docx import Figure, PageMark, Table, Writer, write_block, write_title_page

type JsonObject = dict[str, object]

TEMPLATE_VERSION = "v011"
MISSING_TEXT = "[NOT EXTRACTED: no content found in the source protocol]"
NOT_APPLICABLE_TEXT = "Not applicable"
AI_NOTE = "[AI-drafted from the source protocol. Review required.]"
MISSING_COLOR = RGBColor(0xC0, 0x00, 0x00)
NUMBER_PART = re.compile(r"^[\d.]+")
UNSPLIT_TEXT = "[Not separated in the source protocol; see the section text below]"

# Fixed M11 wording the validator checks; the source's own lead-in sentence is replaced, not repeated
LEAD_INS = {
    "5.2": "To be eligible to participate in this trial, an individual must meet all the following criteria:",
    "5.3": "An individual who meets any of the following criteria will be excluded from participation in this trial:",
}
LABEL_PARAGRAPHS = {
    "9.2.2": ["Identification", "Severity", "Causality", "Recording", "Follow-up"],
    "1.1.2": [
        "Intervention:", "Intervention Model:", "Control Type:", "Control Description:",
        "Intervention Assignment Method:", "Stratification:", "Population Type:",
        "Population Diagnosis or Condition:", "Population Age:", "Minimum:", "Maximum:",
        "Site Distribution and Geographic Scope:", "Master Protocol:",
        "Drug/Device Combination Product Indicator:", "Adaptive Trial Design:", "Number of Arms:",
        "Trial Blind Schema:",
        "Blinded Roles: The following roles indicated will not be made aware of the treatment group assignment during the trial:",
        "Number of Participants:", "Duration:",
        "Total planned duration of trial intervention for each participant:",
        "Total planned duration of trial participation for each participant:",
        "Committees:", "Independent Committees:", "Other Committees:",
    ],
}
# 1.1.2 label -> the slots whose filled values are written after it
LABEL_SLOTS = {
    "Intervention Model:": ["intervention_model"],
    "Control Type:": ["control_type"],
    "Control Description:": ["control_description"],
    "Intervention Assignment Method:": ["intervention_assignment_method", "randomisation_type", "other_assignment_method"],
    "Stratification:": ["stratification"],
    "Population Type:": ["population_type"],
    "Minimum:": ["population_age.minimum", "population_age.minimum_unit"],
    "Maximum:": ["population_age.maximum", "population_age.maximum_unit"],
    "Site Distribution and Geographic Scope:": ["site_distribution", "site_geographic_scope"],
    "Master Protocol:": ["master_protocol"],
    "Drug/Device Combination Product Indicator:": ["drug_device_combination_product"],
    "Adaptive Trial Design:": ["adaptive_trial_design"],
    "Number of Arms:": ["number_of_arms"],
    "Trial Blind Schema:": ["trial_blind_schema"],
    "Blinded Roles: The following roles indicated will not be made aware of the treatment group assignment during the trial:": ["blinded_roles"],
    "Independent Committees:": ["independent_committees"],
    "Other Committees:": ["other_committees"],
    "Number of Participants:": ["participant_number_qualifier", "number_of_participants", "participant_number_basis"],
    "Total planned duration of trial intervention for each participant:": ["intervention_duration", "intervention_duration_description"],
    "Total planned duration of trial participation for each participant:": ["participation_duration", "participation_duration_description"],
}
FIELD_TABLES = [
    ("6", "interventions", [
        ("Arm Name", "arm_name"), ("Arm Type", "arm_type"), ("Intervention Name", "intervention_name"),
        ("Intervention Type", "intervention_type"), ("Pharmaceutical Dose Form", "dose_form"),
        ("Dosage Strength(s)", "dosage_strengths"), ("Dosage Level(s)", "dosage_levels"),
        ("Route of Administration", "route"), ("Regimen/Treatment Period", "regimen"), ("Use", "use"),
        ("IMP/NIMP", "imp_or_nimp"), ("Sourcing", "sourcing")]),
    ("9.2", "safety_event_reporting", [
        ("Event Type", "event_type"), ("Other Event Type", "other_event_type"), ("Situational Scope", "situational_scope"),
        ("Reportable Period Start", "reportable_period_start"), ("Reportable Period End", "reportable_period_end"),
        ("Timing for Reporting to Sponsor", "timing_for_reporting"), ("Method for Reporting", "reporting_method"),
        ("Back-up Method for Reporting", "backup_reporting_method")]),
    ("12.3", "prior_amendments", [
        ("Amendment Identifier", "amendment_identifier"), ("Sponsor Approval Date", "sponsor_approval_date"),
        ("Approximate Enrollment", "approximate_enrollment"), ("Enrollment Scope", "enrollment_scope")]),
]
ESTIMAND_COLUMNS = ["population", "treatment", "endpoint", "population_level_summary"]
ESTIMAND_HEADINGS = ["Population", "Treatment", "Endpoint", "Population-Level Summary"]
OPTIONAL_LABELS = {"Other Committees:"}  # M11 Optional: written only when the source states them
OVERALL_DESIGN_INTRO = "Key aspects of the trial design are summarised below."


class DocxRenderer:
    def __init__(self, template: Path, layout: dict[str, str], assets_dir: Path, protocol_label: str) -> None:
        self.writer = Writer(str(template), TEMPLATE_VERSION)
        self.layout = layout
        self.assets_dir = assets_dir
        self.protocol_label = protocol_label
        self.toc_entries: list[tuple[int, str, str]] = []
        self.protocol_number = ""

    def render(self, protocol: JsonObject, out_path: Path) -> None:
        writer = self.writer
        self.title_page(protocol)
        self.unplaced_front(protocol)
        writer.toc()
        writer.page_break()
        sections = protocol["sections"]
        for key in sorted(sections, key=int):
            self.section(sections[key], level=1)
        writer.finish_sections()
        writer.fill_toc(self.toc_entries)
        writer.footers(self.protocol_label or self.protocol_number)
        writer.save(str(out_path))

    def title_page(self, protocol: JsonObject) -> None:
        others = [
            (item["v10_source"]["found_heading"], item["blocks"][0]["text"])
            for item in protocol.get("unplaced_v10_content", []) if is_title_field(item)
        ]
        values = render_values(protocol["title_page"])
        self.protocol_number = write_title_page(self.writer, values, others, []) or ""
        self.writer.page_break()

    def unplaced_front(self, protocol: JsonObject) -> None:
        """Front matter has no v011 slot yet; keep it ahead of the TOC as the old converter did."""
        items = [item for item in protocol.get("unplaced_v10_content", []) if not is_title_field(item)]
        for item in items:
            for block in item["blocks"]:
                self.block(block)
        if items:
            self.writer.page_break()

    def section(self, section: JsonObject, level: int) -> None:
        status = section["status"]
        if status == "omitted":
            return
        number = section.get("number") or ""
        content = section.get("content", [])
        landscape = self.landscape_blocks(content)
        self.writer.set_orientation(bool(content) and landscape[id(content[0])])
        self.heading(str(number), str(section["title"]), level)
        content = self.fixed_text(number, content, section.get("fields", {}))
        self.status_text(status, content)
        for block in content:
            self.block(block, landscape[id(block)])
        for criterion in section.get("fields", {}).get("criteria", []):
            self.criterion(criterion)
        self.field_tables(number, section.get("fields", {}))
        self.objectives(section.get("objectives", []))
        children = section.get("subsections", {})
        for key in sorted(children, key=natural_key):
            self.section(children[key], level + 1)
        for extra in section.get("additional_subsections", []):
            self.additional(extra, level + 1)

    def fixed_text(self, number: str, content: list[JsonObject], fields: JsonObject) -> list[JsonObject]:
        """Write the M11 text fixed for this section; return the source blocks still to write."""
        body = self.writer.styles["body"]
        if number in LEAD_INS:
            self.writer.paragraph(body, text=LEAD_INS[number])
            return content[1:] if content and is_lead_in(content[0]) else content
        if number == "1.1.2":
            self.writer.paragraph(body, text=OVERALL_DESIGN_INTRO)
        for label in LABEL_PARAGRAPHS.get(number, []):
            filled = filled_text(fields, LABEL_SLOTS.get(label, []))
            if label in OPTIONAL_LABELS and not filled:
                continue
            self.writer.paragraph(self.writer.styles["field_label"], text=label)
            if filled:
                self.highlighted(filled)
            else:
                self.writer.paragraph(body, text=UNSPLIT_TEXT)
        return content

    def field_tables(self, number: str, fields: JsonObject) -> None:
        """Tables M11 builds from record fields: interventions (6), safety event reporting (9.2), amendments (12.3)."""
        for table_number, key, columns in FIELD_TABLES:
            rows = [[cell_text(record[name]) for _, name in columns] for record in fields.get(key, [])]
            rows = [row for row in rows if any(row)]
            if table_number == number and rows:
                self.highlighted(AI_NOTE)
                self.writer.simple_table([[heading for heading, _ in columns], *rows])
        for region in fields.get("country_regions", []):
            identifier = region["identifier"]["value"]
            self.writer.paragraph(self.writer.styles["field_label"], text=f"{identifier['decode']} ({identifier['code']})")
            for block in [*region["requirements"], *region["clarifications"]]:
                self.block(block)
        if number == "12.3":
            for name in ("amendment_statement", "overview_location", "original_protocol_approval_date"):
                text = cell_text(fields.get(name, {}))
                if text:
                    self.highlighted(text)

    def objectives(self, objectives: list[JsonObject]) -> None:
        """Each objective with its estimands as one table of the ICH E9(R1) attributes."""
        for objective in objectives:
            statement = cell_text(objective["statement"])
            if not statement:
                continue
            category = cell_text(objective["category_label"])
            self.writer.paragraph(self.writer.styles["field_label"],
                                  text=f"{objective['title']}{': ' + category if category else ''}")
            self.highlighted(statement)
            rows = [[cell_text(estimand[name]) for name in ESTIMAND_COLUMNS] + [intercurrent_text(estimand)]
                    for estimand in objective["estimands"]]
            rows = [row for row in rows if any(row)]
            if rows:
                self.writer.simple_table([[*ESTIMAND_HEADINGS, "Intercurrent Events and Strategies"], *rows])

    def criterion(self, criterion: JsonObject) -> None:
        """One numbered paragraph; the criterion's further lines (sub-items) follow as bullets."""
        first, *rest = str(criterion["text"]["value"]).split("\n")
        self.writer.paragraph(self.writer.styles["body"], text=f"{criterion['number']}. {first}")
        for line in rest:
            self.writer.paragraph(self.writer.styles["bullet"], text=line)

    def additional(self, node: JsonObject, level: int) -> None:
        self.heading("", str(node["title"]), level)
        landscape = self.landscape_blocks(node["content"])
        for block in node["content"]:
            self.block(block, landscape[id(block)])
        for child in node["subsections"]:
            self.additional(child, level + 1)

    def heading(self, number: str, title: str, level: int) -> None:
        self.toc_entries.append((level, number, title))
        self.writer.numbered_heading(title, level)

    def status_text(self, status: str, content: list[JsonObject]) -> None:
        if status == "not_applicable":
            self.writer.paragraph(self.writer.styles["body"], text=NOT_APPLICABLE_TEXT)
        elif status == "missing" or (content and not has_text(content)):
            self.missing_text()
        elif any(is_ai(block) for block in content):
            self.highlighted(AI_NOTE)

    def missing_text(self) -> None:
        paragraph = self.writer.paragraph(self.writer.styles["body"], text=MISSING_TEXT)
        for run in paragraph.runs:
            run.font.color.rgb = MISSING_COLOR

    def highlighted(self, text: str) -> None:
        paragraph = self.writer.paragraph(self.writer.styles["body"], text=text)
        for run in paragraph.runs:
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW

    def block(self, block: JsonObject, landscape: bool = False) -> None:
        if is_ai(block):
            self.ai_block(block)
            return
        rich_blocks = self.rich_blocks(block)
        if rich_blocks is None:
            self.plain_block(block)
            return
        self.writer.set_orientation(landscape)
        for rich in rich_blocks:
            write_block(self.writer, rich)

    def rich_blocks(self, block: JsonObject) -> list | None:
        xhtml = self.layout.get(str(block.get("source_index")))
        return None if xhtml is None else xhtml_to_blocks(xhtml, self.assets_dir)

    def landscape_blocks(self, content: list[JsonObject]) -> dict[int, bool]:
        """Which blocks go on landscape pages, keyed by id(block).

        A table or figure that sat on a landscape source page does, and so does the caption or heading
        right before it (a chain of marked blocks ending in one). Text after a table (footnotes) goes back to portrait.
        """
        rich = [None if is_ai(block) else self.rich_blocks(block) for block in content]
        marked = [blocks is not None and is_landscape(blocks) for blocks in rich]
        visual = [blocks is not None and any(isinstance(item, (Table, Figure)) for item in blocks) for blocks in rich]
        flags = {}
        leads_to_visual = False
        for index in reversed(range(len(content))):
            leads_to_visual = marked[index] and (visual[index] or leads_to_visual)
            flags[id(content[index])] = leads_to_visual
        return flags

    def plain_block(self, block: JsonObject) -> None:
        if block["type"] == "table":
            self.writer.simple_table(block["rows"])
        elif block["type"] == "heading":
            self.writer.paragraph(self.writer.styles["field_label"], text=block["text"])
        else:
            self.writer.paragraph(self.writer.styles["body"], text=block["text"])

    def ai_block(self, block: JsonObject) -> None:
        self.plain_block(block)
        paragraph = self.writer.doc.paragraphs[-1] if block["type"] != "table" else None
        for run in paragraph.runs if paragraph is not None else []:
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW


def slot_text(value: object) -> str:
    if isinstance(value, dict) and "decode" in value:
        return str(value["decode"])
    if isinstance(value, dict):
        return f"{value['quantity']} {slot_text(value['unit'])}"
    return str(value)


def slot_displays(slot: object) -> list[str]:
    """Display text of a slot or list of slots: filled values, or the source term of an `unsure` coded slot."""
    if isinstance(slot, list):
        return [text for item in slot for text in slot_displays(item)]
    if not isinstance(slot, dict):
        return []
    if slot.get("status") == "filled":
        return [slot_text(slot["value"])]
    if slot.get("status") == "unsure" and slot.get("raw_text"):
        return [str(slot["raw_text"])]
    return []


def intercurrent_text(estimand: JsonObject) -> str:
    return "\n".join(f"{cell_text(event['description'])}: {cell_text(event['strategy'])}"
                     for event in estimand["intercurrent_events"])


def cell_text(slot: object) -> str:
    return ", ".join(slot_displays(slot))


def filled_text(fields: JsonObject, paths: list[str]) -> str:
    """The filled slots at `paths` joined into one line; empty when none is filled."""
    values = []
    for path in paths:
        slot = fields
        for part in path.split("."):
            slot = slot.get(part, {}) if isinstance(slot, dict) else {}
        values.extend(slot_displays(slot))
    return " ".join(values)


def is_landscape(rich_blocks: list) -> bool:
    return any(isinstance(block, PageMark) and block.landscape for block in rich_blocks)


def has_text(content: list[JsonObject]) -> bool:
    """False for a section holding only figures, which M11 validators read as empty."""
    return any(not block.get("has_image") for block in content)


def is_lead_in(block: JsonObject) -> bool:
    return block["type"] == "paragraph" and block["text"].rstrip().endswith(":")


def is_title_field(item: JsonObject) -> bool:
    return item["v10_source"]["section_id"] == TITLE_PAGE_UNMAPPED_ID


def is_ai(block: JsonObject) -> bool:
    return block["custody"]["origin"] in ("ai_generated", "ai_transformed")


def natural_key(key: str) -> tuple[int, ...]:
    match = NUMBER_PART.match(key)
    return tuple(int(part) for part in match.group(0).split(".") if part) if match else ()


def render_docx(protocol: JsonObject, layout: dict[str, str], assets_dir: Path,
                template: Path, out_path: Path, protocol_label: str = "") -> None:
    DocxRenderer(template, layout, assets_dir, protocol_label).render(protocol, out_path)
