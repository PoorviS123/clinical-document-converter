"""Map the v010 title page values into the protocol JSON `title_page` slots, and back for rendering."""

import copy
import re
from datetime import datetime
from pathlib import Path

import pymupdf

from pdf_to_cpt_docx import (
    COVER_HEADING, ELECTRONIC_SIGNATURE_VALUE, Heading, Paragraph, Table, canonical_label, collect_title_values,
    join_sponsor, registry_rows,
)

type JsonObject = dict[str, object]

TITLE_STOP = re.compile(r"^(Confidential:|Confidential$|Any and all information|This protocol was approved|Based on TMF|©)", re.IGNORECASE)
RUNNING_HEADER_VERSION = re.compile(r"^Version number:\s*(\S+)$", re.IGNORECASE)
TITLE_EMPTY_LABELS = re.compile(r"^(Clinical Scientist|Medical\s*/?Study Director|Medical/Study Director|Study Medical Director/Medical Monitor|Monitoring team|Other:|Pediatric investigational plan|Sponsor Signatory \().*", re.IGNORECASE)
PDF_SUFFIX = ".pdf"
MAX_ACRONYM_CHARS = 14
MAX_SHORT_TITLE_CHARS = 300
TITLE_PAGE_UNMAPPED_ID = "v10:title_page.unmapped"
DATE_FORMATS = ["%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%m/%d/%Y"]
ROMAN_PHASES = {"I": "1", "II": "2", "III": "3", "IV": "4"}
PHASE_NUMBERS = re.compile(r"\b(IV|III|II|I|[1-4])\b")
REGISTRY_TYPES = {
    "IND": "fda_ind_number", "NCT": "nct_number", "WHO": "who_utn_number",
    "EU CT": "eu_ct_number", "EU TRIAL NUMBER": "eu_ct_number", "CTIS": "eu_ct_number",
}
V10_FIELD_IDS = {
    "title": "protocol_title", "short_title": "brief_title", "acronym": "acronym",
    "protocol_number": "protocol_number", "amendment_number": "amendment_number", "compound": "compound",
    "phase": "study_phase", "sponsor": "sponsor_name", "manufacturer": "manufacturer",
    "regulatory_ids": "regulatory_agency_identifier_numbers", "date": "approval_date",
    "signatory": "sponsor_signatory", "medical_monitor": "medical_monitor_name_and_contact_information",
}


def parse_date(text: str) -> str | None:
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), date_format).date().isoformat()
        except ValueError:
            continue
    return None


def phase_decode(text: str) -> str | None:
    """'Phase III' -> 'Phase 3', '2/3' -> 'Phase 2/Phase 3'; None when no phase number is found."""
    numbers = [ROMAN_PHASES.get(match, match) for match in PHASE_NUMBERS.findall(text.upper())]
    return "/".join(f"Phase {number}" for number in numbers) if numbers else None


def first_two_pages(path: Path) -> tuple[list[str], list[str]]:
    """Non-empty page-1 lines and page-2 lines of a PDF."""
    with pymupdf.open(path) as pdf:
        lines = [line.strip() for line in pdf[0].get_text().splitlines() if line.strip()]
        second_page_lines = [line.strip() for line in pdf[1].get_text().splitlines()] if len(pdf) > 1 else []
    return lines, second_page_lines


def add_amendment_from_version(values: dict[str, str]) -> None:
    amendment = re.search(r"\bAmendment\s+\d+(?:\s+EU-\d+)?", values.get("version", ""), re.IGNORECASE)
    if amendment and not values.get("amendment_number"):
        values["amendment_number"] = amendment.group()


COMPANY_LINE = re.compile(r"[A-Z][\w&.,' -]*?(?:Pharmaceuticals|Therapeutics|Pharma|Biosciences)[\w., -]*"
                          r"(?:Inc\.|Ltd\.?|LLC|GmbH|Corp\.|Corporation|AG|plc|S\.A\.)")


def enrich_title(path, values, others, notes):
    """Supplement title fields from PDF page text. Other inputs have no page layout, so only the title table is used."""
    if Path(path).suffix.lower() != PDF_SUFFIX:
        add_amendment_from_version(values)
        return
    lines, second_page_lines = first_two_pages(path)
    running_version = next((match.group(1) for match in map(RUNNING_HEADER_VERSION.match, second_page_lines) if match), None)
    if running_version:
        values.setdefault("version", running_version)
        notes.append("Version Number taken from the 'Version number:' running header on page 2")
    first_label = next((index for index, line in enumerate(lines)
                        if re.fullmatch(r"(?:AMENDED )?CLINICAL (?:TRIAL|STUDY) PROTOCOL(?: \d+)?", line, re.IGNORECASE)), 0)
    collected = {}
    current = None
    registry = registry_rows(" ".join(lines))
    date = next((line.partition(":")[2].strip() for line in lines[:first_label]
                 if line.startswith("Date:")), None)
    if date:
        values["date"] = date
    for line in lines[:first_label]:
        version = re.fullmatch(r"Protocol Version (\S+) Final", line, re.IGNORECASE)
        amendment = re.fullmatch(r"Protocol Amendment (\S+) Final", line, re.IGNORECASE)
        if version:
            values.setdefault("version", version.group(1))
        if amendment:
            values.setdefault("amendment_number", amendment.group(1))
    index = first_label
    while index < len(lines):
        line = lines[index]
        index += 1
        if TITLE_STOP.match(line):
            break
        label, separator, remainder = line.partition(":")
        key = canonical_label(label)
        if not key and index < len(lines):
            combined = line + " " + lines[index]
            combined_label, combined_separator, combined_remainder = combined.partition(":")
            key = canonical_label(combined_label)
            if key:
                label, separator, remainder = combined_label, combined_separator, combined_remainder
                index += 1
        if key:
            current = key
            collected.setdefault(key, [])
            if separator and remainder:
                collected[key].append(remainder.strip())
            continue
        if TITLE_EMPTY_LABELS.match(line) or (line.endswith(":") and not canonical_label(line)):
            current = None
        elif current and line:
            collected[current].append(line)
    for key, pieces in collected.items():
        value = " ".join(pieces).strip()
        if key not in ("registry_id", "regulatory_ids", "sponsor_address", "medical_monitor", "signatory",
                       "primary_intervention") \
                and value and not ELECTRONIC_SIGNATURE_VALUE.match(value):
            values[key] = value
    if collected.get("sponsor_address"):
        sponsor = ""
        for piece in collected.get("sponsor", []) + collected["sponsor_address"]:
            sponsor = join_sponsor(sponsor, piece)
        values["sponsor"] = sponsor
    if registry:
        values["regulatory_ids"] = "; ".join(f"{label}: {identifier}" for label, identifier in dict.fromkeys(registry))
    if not values.get("sponsor"):
        company = next((line for line in lines if COMPANY_LINE.fullmatch(line)), None)
        if company:
            values["sponsor"] = company
            notes.append(f"Sponsor recovered from the company line in the {company} title-page layout")
    if not values.get("title"):
        compound_index = next((index for index, line in enumerate(lines) if canonical_label(line) == "compound"), None)
        if compound_index is not None:
            title_start = next((index + 1 for index in range(compound_index - 1, -1, -1)
                                if lines[index].lower() == "clinical study protocol"), None)
            title_lines = lines[title_start:compound_index] if title_start is not None else []
            if title_lines and all(line.upper() == line for line in title_lines):
                values["title"] = " ".join(title_lines)
                notes.append("Full Title recovered from the uppercase title between Clinical Study Protocol and Compound")
    add_amendment_from_version(values)
    notes.append("Title fields supplemented from explicit first-page labels; unrecognized title fields retained verbatim")


def paragraphs(blocks):
    for block in blocks:
        if isinstance(block, Paragraph):
            yield block
        elif isinstance(block, Table):
            for row in block.grid:
                for cell in row:
                    if cell:
                        yield from cell["paragraphs"]


def synopsis_title(document, values, notes):
    if values.get("title"):
        return
    in_synopsis = False
    pending = False
    for block in document.body:
        if isinstance(block, Heading):
            in_synopsis = block.number == "1.1"
        if not in_synopsis:
            continue
        for paragraph in paragraphs([block]):
            text = paragraph.text.strip()
            if pending and text:
                values["title"] = text
                notes.append("Full Title recovered from synopsis Protocol Title before synopsis filtering")
                return
            label, separator, remainder = text.partition(":")
            if canonical_label(label) == "title":
                if separator and remainder.strip():
                    values["title"] = remainder.strip()
                    notes.append("Full Title recovered from synopsis Protocol Title before synopsis filtering")
                    return
                pending = True


def finish_title(values: dict[str, str], others: list[tuple[str | None, str]], notes: list[str] | None = None) -> None:
    """Fold fallback fields into their slots and drop extras that repeat a mapped value."""
    primary = values.pop("primary_intervention", None)
    if primary and not values.get("compound"):
        values["compound"] = primary
        if notes is not None:
            notes.append("Investigational Product Code(s) taken from 'Primary study intervention(s)'")
    mapped_text = " ".join(values.values())
    others[:] = [(label, value) for label, value in others
                 if label or not (COVER_HEADING.match(value) or value in mapped_text)]


class TitlePageFiller:
    def __init__(self, protocol: JsonObject, schema: JsonObject, take_index, script_actor: JsonObject) -> None:
        self.page = protocol["title_page"]
        self.schema = schema
        self.take_index = take_index
        self.actor = script_actor

    def source(self, key: str, label: str | None) -> JsonObject:
        known = key in V10_FIELD_IDS
        return {
            "section_id": f"v10:title_page.{V10_FIELD_IDS[key]}" if known else TITLE_PAGE_UNMAPPED_ID,
            "found_number": None, "found_heading": label,
            "match": "exact" if known else "protocol_added",
            "source_indices": [self.take_index()], "portion": "all",
        }

    def custody(self, key: str, needs_review: bool = False) -> JsonObject:
        return {
            "origin": "v10_derived", "v10_sources": [self.source(key, None)],
            "actor": copy.deepcopy(self.actor), "confidence": "low" if needs_review else "high",
            "needs_review": needs_review,
        }

    def slot(self, key: str, text: str, value: object | None = None, usable: bool = True) -> JsonObject:
        """A filled slot, or an unsure one (null value, text kept in raw_text) when the text can't be used."""
        chosen = text if value is None else value
        if usable and chosen is not None:
            return {"value": chosen, "status": "filled", "custody": self.custody(key), "raw_text": text}
        return {"value": None, "status": "unsure", "custody": self.custody(key, needs_review=True), "raw_text": text}

    def phase_slot(self, text: str) -> JsonObject:
        decode = phase_decode(text)
        for term in self.schema["$defs"]["Code_TrialPhase"]["oneOf"]:
            properties = term["properties"]
            if properties["decode"]["const"] == decode:
                coded = {"code": properties["code"]["const"], "decode": decode}
                return self.slot("phase", text, coded)
        return self.slot("phase", text, usable=False)

    def fill(self, values: dict[str, str]) -> None:
        page = self.page
        text_fields = {
            "title": "full_title", "protocol_number": "sponsor_protocol_identifier",
            "amendment_number": "amendment_identifier", "version": "version_number",
            "medical_monitor": "medical_expert_contact",
        }
        for key, field in text_fields.items():
            if values.get(key):
                page[field] = self.slot(key, values[key])
        if values.get("acronym"):
            page["trial_acronym"] = self.slot("acronym", values["acronym"], usable=len(values["acronym"]) <= MAX_ACRONYM_CHARS)
        if values.get("short_title"):
            page["short_title"] = self.slot("short_title", values["short_title"], usable=len(values["short_title"]) <= MAX_SHORT_TITLE_CHARS)
        if values.get("date"):
            iso = parse_date(values["date"])
            page["version_date"] = self.slot("date", values["date"], iso, usable=iso is not None)
        if values.get("phase"):
            page["trial_phase"] = self.phase_slot(values["phase"])
        if values.get("compound"):
            page["investigational_product_codes"] = [self.slot("compound", values["compound"])]
        if values.get("sponsor"):
            page["sponsors"] = [{"role": "sponsor", "name": self.slot("sponsor", values["sponsor"]),
                                 "legal_address": page["sponsors"][0]["legal_address"]}]
        if values.get("manufacturer"):
            page["device_manufacturers"] = [{"name": self.slot("manufacturer", values["manufacturer"]),
                                             "legal_address": copy.deepcopy(page["sponsors"][0]["legal_address"])}]
        if values.get("regulatory_ids"):
            page["regulatory_identifiers"] = [
                {"type": REGISTRY_TYPES.get(label, "other"), "label": label, "id": self.slot("regulatory_ids", identifier)}
                for label, identifier in registry_rows(values["regulatory_ids"])
            ]
        if values.get("signatory"):
            empty = page["sponsors"][0]["legal_address"]
            page["sponsor_signatories"] = [{
                "name": self.slot("signatory", values["signatory"]), "title": copy.deepcopy(empty),
                "date": copy.deepcopy(empty), "signature": {"kind": "text", "value": copy.deepcopy(empty)},
            }]


def slot_text(slot: JsonObject) -> str:
    raw = slot.get("raw_text")
    if isinstance(raw, str):
        return raw
    value = slot.get("value")
    return value if isinstance(value, str) else ""


def render_values(title_page: JsonObject) -> dict[str, str]:
    """Title page slots back to the {key: text} dict that write_title_page expects."""
    values: dict[str, str] = {}
    single = {
        "title": "full_title", "protocol_number": "sponsor_protocol_identifier",
        "amendment_number": "amendment_identifier", "version": "version_number", "acronym": "trial_acronym",
        "short_title": "short_title", "date": "version_date", "phase": "trial_phase",
        "medical_monitor": "medical_expert_contact",
    }
    for key, field in single.items():
        text = slot_text(title_page.get(field, {}))
        if text:
            values[key] = text
    codes = [slot_text(slot) for slot in title_page.get("investigational_product_codes", [])]
    if any(codes):
        values["compound"] = "; ".join(code for code in codes if code)
    for key, field in (("sponsor", "sponsors"), ("manufacturer", "device_manufacturers")):
        names = [slot_text(item["name"]) for item in title_page.get(field, [])]
        if any(names):
            values[key] = "; ".join(name for name in names if name)
    ids = [f"{item['label']}: {slot_text(item['id'])}" for item in title_page.get("regulatory_identifiers", [])
           if slot_text(item["id"])]
    if ids:
        values["regulatory_ids"] = "; ".join(ids)
    signatories = [slot_text(item["name"]) for item in title_page.get("sponsor_signatories", [])]
    if any(signatories):
        values["signatory"] = "; ".join(name for name in signatories if name)
    return values


def collect(document, source_path) -> tuple[dict[str, str], list[tuple[str | None, str]]]:
    notes: list[str] = []
    values, others = collect_title_values(document, notes)
    synopsis_title(document, values, notes)
    enrich_title(source_path, values, others, notes)
    finish_title(values, others)
    return values, others
