"""Rewrite "Section N" and "Appendix N" references in body text to restructured section numbers."""

import re
from dataclasses import dataclass, field

from pdf_to_cpt_docx import Paragraph, Span, Table

APPENDIX_CHAPTER = "10"  # v010 numbers its appendices 10.1, 10.2, ...; "Appendix 3" is section 10.3
SNIPPET_CONTEXT = 40  # characters of text shown on each side of a reported reference

NUMBER = r"\d{1,2}(?:\.\d{1,2}){0,5}(?!\.?\d)"
SEPARATOR = r"(?:\s*,\s*(?:and|or)\s+|\s*,\s*|\s+(?:and|or|to|through)\s+|\s*[-–]\s*)"
NUMBER_LIST = rf"{NUMBER}(?:{SEPARATOR}{NUMBER})*"
SECTION_WORD = r"(?i:sections?)"
APPENDIX_WORD = r"(?i:appendix|appendices)"

# Alternatives are tried left to right at each position, so the paired forms ("Appendix 3
# (Section 10.3)", "Section 10.3 [Appendix 3]") win over the plain lists that would match their parts.
REFERENCE = re.compile(
    rf"\b(?:"
    rf"(?P<appendix_first>{APPENDIX_WORD}(?:\s+{NUMBER})?\s*:?\s*"
    rf"(?:\(\s*{SECTION_WORD}\s+(?P<paren_number>{NUMBER})\s*\)"
    rf"|\[\s*{SECTION_WORD}\s+(?P<bracket_number>{NUMBER})\s*\]"
    rf"|,?\s*{SECTION_WORD}\s+(?P<plain_number>{NUMBER})))"
    rf"|{SECTION_WORD}\s+(?P<section_first>{NUMBER})\s*"
    rf"(?:\(\s*{APPENDIX_WORD}\s+{NUMBER}\s*\)|\[\s*{APPENDIX_WORD}\s+{NUMBER}\s*\]|,\s*{APPENDIX_WORD}\s+{NUMBER})"
    rf"|(?P<section_word>{SECTION_WORD})\s+(?P<section_list>{NUMBER_LIST})"
    rf"|(?P<appendix_word>{APPENDIX_WORD})\s+(?P<appendix_list>{NUMBER_LIST})"
    rf")"
)
LIST_NUMBER = re.compile(NUMBER)
# "Section 5 of the IB" points into another document; "Section 5 of this protocol" does not.
OTHER_DOCUMENT = re.compile(r"\s+of\s+(?!(?:this|the)\s+(?:\w+\s+)?protocol\b)", re.IGNORECASE)


@dataclass
class CrossReferenceReport:
    rewritten: int = 0
    review: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)


@dataclass
class Replacement:
    start: int
    end: int
    source_numbers: list
    template: str  # text replacing start:end, with one "{}" per new number


def rewrite_cross_references(blocks, number_map, also_sources):
    report = CrossReferenceReport()
    for paragraph in block_paragraphs(blocks):
        rewrite_paragraph(paragraph, number_map, also_sources, report)
    return report


def block_paragraphs(blocks):
    for block in blocks:
        if isinstance(block, Paragraph):
            yield block
        elif isinstance(block, Table):
            for row in block.grid:
                for cell in row:
                    if cell:
                        yield from cell["paragraphs"]


def containing_section(number, number_map):
    """The nearest ancestor of a section number the source has no heading for, eg 8.3.1.1 for 8.3.1.1.1."""
    parts = number.split(".")
    while len(parts) > 1:
        parts.pop()
        ancestor = ".".join(parts)
        if ancestor in number_map:
            return ancestor
    return None


def rewrite_text(text, number_map, also_sources, report, where):
    """Rewrite references in text written outside the section blocks (title page, heading titles)."""
    paragraph = Paragraph([Span(text, False, False, False, 0)])
    rewrite_paragraph(paragraph, number_map, also_sources, report, where)
    return paragraph.text


def rewrite_title_values(values, number_map, also_sources, report):
    return [(label, rewrite_text(text, number_map, also_sources, report, "title page") if text else text)
            for label, text in values]


def rewrite_heading_titles(nodes, number_map, also_sources, report):
    for node in nodes:
        node.title = rewrite_text(node.title, number_map, also_sources, report, f"heading {node.number}")


def rewrite_paragraph(paragraph, number_map, also_sources, report, where=None):
    where = where or f"p{paragraph.page}"
    text = "".join(span.text for span in paragraph.spans)
    edits = []
    for match in REFERENCE.finditer(text):
        if OTHER_DOCUMENT.match(text, match.end()):
            continue
        snippet = context(text, match.start(), match.end())
        for replacement in replacements_for(match):
            targets = {number: number if number in number_map else containing_section(number, number_map)
                       for number in replacement.source_numbers}
            missing = [number for number, target in targets.items() if target is None]
            for number in missing:
                report.unresolved.append(f"{where}: {number} not in output: {snippet}")
            if missing:
                continue
            for number, target in targets.items():
                if target != number:
                    report.review.append(f"{where}: {number} has no heading in the source; "
                                         f"pointed at containing section {target} -> {number_map[target]}: {snippet}")
            new_numbers = [number_map[targets[number]] for number in replacement.source_numbers]
            for number, new_number in zip(replacement.source_numbers, new_numbers):
                if number in also_sources:
                    report.review.append(
                        f"{where}: {number} -> {new_number}, content also belongs elsewhere: {snippet}")
            new_text = replacement.template.format(*new_numbers)
            if new_text != text[replacement.start:replacement.end]:
                edits.append((replacement.start, replacement.end, new_text))
                report.rewritten += len(new_numbers)
    for start, end, new_text in sorted(edits, reverse=True):
        replace_text(paragraph.spans, start, end, new_text)


def replacements_for(match):
    if match.group("appendix_first"):
        group = next(name for name in ("paren_number", "bracket_number", "plain_number") if match.group(name))
        return [Replacement(match.start(), match.end(), [match.group(group)], "Section {}")]
    if match.group("section_first"):
        return [Replacement(match.start("section_first"), match.end(), [match.group("section_first")], "{}")]
    if match.group("section_list"):
        plural = match.group("section_word").lower().endswith("s")
        return [Replacement(start, end, [number], "{}")
                for start, end, number in list_numbers(match, "section_list", plural)]
    return [appendix_list_replacement(match)]


def list_numbers(match, group, plural):
    """(start, end, number) for each number in "Sections 5.1, 5.2 and 6".

    After a singular keyword, a later number without a dot is more likely a count or a dose
    ("Section 8.1, 2 weeks") than a section, so the list stops there.
    """
    numbers = []
    offset = match.start(group)
    for index, number_match in enumerate(LIST_NUMBER.finditer(match.group(group))):
        number = number_match.group()
        if index and not plural and "." not in number:
            break
        numbers.append((offset + number_match.start(), offset + number_match.end(), number))
    return numbers


def appendix_list_replacement(match):
    """"Appendix 3" or "Appendices 2 and 3" as one replacement, so the keyword only changes to
    "Section" when every number resolves."""
    numbers = list_numbers(match, "appendix_list", plural=True)
    keyword = "Sections" if len(numbers) > 1 else "Section"
    template = keyword + match.string[match.end("appendix_word"):numbers[0][0]]
    for index, (start, end, _) in enumerate(numbers):
        following = numbers[index + 1][0] if index + 1 < len(numbers) else match.end()
        template += "{}" + match.string[end:following]
    source_numbers = [number if "." in number else f"{APPENDIX_CHAPTER}.{number}" for _, _, number in numbers]
    return Replacement(match.start(), match.end(), source_numbers, template)


def context(text, start, end):
    before = text[max(0, start - SNIPPET_CONTEXT):start]
    after = text[end:end + SNIPPET_CONTEXT]
    return f"…{before}[{text[start:end]}]{after}…"


def replace_text(spans, start, end, new_text):
    """Replace characters start:end of the spans' joined text in place.

    Each span keeps its formatting; the new text takes the formatting of the span where the
    replaced range starts, and spans left empty are removed.
    """
    offset = 0
    placed = False
    for span in spans:
        span_start, span_end = offset, offset + len(span.text)
        offset = span_end
        if span_end <= start or span_start >= end:
            continue
        cut_from = max(start, span_start) - span_start
        cut_to = min(end, span_end) - span_start
        span.text = span.text[:cut_from] + ("" if placed else new_text) + span.text[cut_to:]
        placed = True
    spans[:] = [span for span in spans if span.text]
