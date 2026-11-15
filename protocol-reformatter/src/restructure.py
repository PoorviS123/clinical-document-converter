"""Rearrange a v010-shaped Document into the v011 section outline.

The v011 outline comes from the template's heading paragraphs; resources/research/crosswalk.tsv
says where each v010 template section goes. Sponsor sections the crosswalk doesn't know are kept
near where they were and listed in the report.
"""

import csv
import re
from dataclasses import dataclass, field

import docx

from pdf_to_cpt_docx import (
    MISSING_HEADING_TITLE, REDACTION_MARK, STYLE_NAMES, FrontHeading, Heading, PageMark, Paragraph, Span,
    Subheading, advance,
)
from section_splitters import handling_child_target, split_assignment, split_handling
from special_sections import split_objectives, split_synopsis

NO_CONTENT_PLACEHOLDER = "[No corresponding content in source protocol]"

EXPECTED_NUMBER_MIN_SIMILARITY = 0.34
TITLE_ONLY_MIN_SIMILARITY = 0.6
MOVED_SECTION_MIN_SIMILARITY = 1.0  # same words: a moved section must keep its exact title
MIN_PLURAL_WORD_LENGTH = 3  # shorter words ending in s ("gas", "its") aren't plurals
MIN_ACRONYM_LENGTH = 3
UNREADABLE_TITLES = {MISSING_HEADING_TITLE, REDACTION_MARK, "[REDACTED]"}
STOP_WORDS = {"a", "an", "and", "or", "of", "the", "for", "to", "in", "on", "with", "by", "at", "from", "after", "as"}
APPENDIX_PREFIX = re.compile(r"^\s*appendix\s+\w+\s*[:.\-–]?\s*", re.IGNORECASE)
APPENDIX_LABEL = re.compile(r"^\s*appendix\s+(?:\d+|[A-Z])\b\s*[:.\-–]?\s*(?=\S)", re.IGNORECASE)
BRACKETED_TEXT = re.compile(r"\[[^\]]*\]")
TEMPLATE_TYPED_NUMBER = re.compile(r"^\s*\d+(\.\d+)*\s+")
TEMPLATE_HEADING_STYLES = ["01Heading 1", "02Heading 2", "03Heading 3", "04Heading 4"]
ALTERNATE_TITLE_SEPARATOR = " ~ "

APPENDIX_CHAPTER = "10"  # v010 chapter whose L2 sections sponsors renumber and reorder
SUPPORTING_DETAILS_NODE = "12"  # v011 "Appendix: Supporting Details"
SYNOPSIS_ROW = "1.1"
SYNOPSIS_OBJECTIVES_NODE = "1.1.1"
SYNOPSIS_DESIGN_NODE = "1.1.2"
OBJECTIVES_ROW = "3"
OBJECTIVES_NODES = {"primary": "3.1", "secondary": "3.2", "exploratory": "3.3"}
OBJECTIVES_PART_WORDS = {
    "primary": re.compile(r"\bprimary\b", re.IGNORECASE),
    "secondary": re.compile(r"\bsecondary\b", re.IGNORECASE),
    "exploratory": re.compile(r"\b(exploratory|tertiary)\b", re.IGNORECASE),
}
HANDLING_ROW = "6.2"
ASSIGNMENT_ROW = "6.3"
HANDLING_NODES = ["6.6.1", "6.6.2", "6.6.3"]  # all three are kept once any has content
ABBREVIATIONS_NODE = "13"
INVESTIGATOR_AGREEMENT = re.compile(r"investigator[’']?s?\s+(agreement|signature|statement)", re.IGNORECASE)
SPONSOR_SIGNATORY = re.compile(r"sponsor signatory|signature page|protocol approval", re.IGNORECASE)
ABBREVIATIONS_FRONT_HEADING = re.compile(r"^(list of )?abbreviations|definitions of terms", re.IGNORECASE)
AMENDMENT_FRONT_HEADING = re.compile(
    r"summary of changes|overall rationale for the amendment|document history|protocol history|"
    r"assessment of substantiality|overview of changes|^amendment \d+",
    re.IGNORECASE,
)
MAX_HEADING_LEVEL = len(STYLE_NAMES["v011"]["headings"])  # the template has no heading style below this
SPLITTERS = {HANDLING_ROW: "place_handling", ASSIGNMENT_ROW: "place_assignment"}
TEMPLATE_DROPPABLE_LEVEL = 3  # template headings at this level and deeper are emitted only with content
# Words every protocol uses in section titles; they can't tell two template sections apart
GENERIC_TITLE_WORDS = {"trial", "study", "investigational", "noninvestigational", "intervention", "treatment",
                       "therapy", "participant", "concomitant"}


@dataclass
class CrosswalkRow:
    number: str
    titles: list
    target: str
    also: list


@dataclass
class TemplateHeading:
    level: int
    number: str
    title: str


@dataclass
class Section:
    number: str
    title: str
    page: int
    blocks: list
    children: list


@dataclass
class OutNode:
    level: int
    title: str
    number: str = ""
    blocks: list = field(default_factory=list)
    children: list = field(default_factory=list)
    source_numbers: list = field(default_factory=list)
    from_template: bool = True
    source_titles: list = field(default_factory=list)
    required: bool = False  # kept with a placeholder even when empty
    template_number: str = ""  # the node's number in the v011 template; `number` shifts when empty siblings are pruned


@dataclass
class Report:
    unmatched: list = field(default_factory=list)
    split_review: list = field(default_factory=list)
    placeholders: list = field(default_factory=list)
    synopsis_dropped: list = field(default_factory=list)
    objectives: list = field(default_factory=list)
    notes: list = field(default_factory=list)


@dataclass
class Result:
    nodes: list
    front: list
    amendment_details: list
    number_map: dict
    also_sources: set
    landscape_pages: dict
    report: Report


# ---------------------------------------------------------------- inputs

def load_crosswalk(path):
    rows = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle, delimiter="\t"):
            number = record["v010_number"].strip()
            rows[number] = CrosswalkRow(
                number=number,
                titles=[title.strip() for title in record["v010_title"].split(ALTERNATE_TITLE_SEPARATOR)],
                target=record["v011_number"].strip(),
                also=(record.get("also") or "").split(),
            )
    return rows


def template_outline(template_path):
    outline = []
    counters = []
    for paragraph in docx.Document(template_path).paragraphs:
        style = paragraph.style.name
        if style not in TEMPLATE_HEADING_STYLES:
            continue
        level = TEMPLATE_HEADING_STYLES.index(style) + 1
        number = advance(counters, level)
        title = TEMPLATE_TYPED_NUMBER.sub("", paragraph.text.replace("\t", " ")).strip()
        outline.append(TemplateHeading(level, number, title))
    return outline


def build_tree(body):
    roots = []
    open_sections = []
    orphans = []
    for block in body:
        if isinstance(block, PageMark):
            continue
        if isinstance(block, Heading):
            section = Section(block.number, block.title, block.page, [], [])
            while open_sections and not block.number.startswith(open_sections[-1].number + "."):
                open_sections.pop()
            (open_sections[-1].children if open_sections else roots).append(section)
            open_sections.append(section)
        elif open_sections:
            open_sections[-1].blocks.append(block)
        else:
            orphans.append(block)
    if orphans and roots:
        roots[0].blocks[:0] = orphans
    return roots


# ---------------------------------------------------------------- matching

def normalize_word(word):
    if word.endswith("sis"):
        return word[:-2] + "e"  # analysis/analyses, diagnosis/diagnoses
    if word.endswith("s") and len(word) > MIN_PLURAL_WORD_LENGTH:
        return word[:-1]
    return word


def title_words(title):
    # Bracketed text is an optional part of a CPT template title that sponsors often keep verbatim.
    plain = BRACKETED_TEXT.sub(" ", APPENDIX_PREFIX.sub("", title))
    words = re.findall(r"[a-z0-9]+", plain.lower())
    return [normalize_word(word) for word in words if word not in STOP_WORDS]


def title_similarity(first, second):
    tokens_a, tokens_b = set(title_words(first)), set(title_words(second))
    if not tokens_a or not tokens_b:
        return 0.0
    overlap = len(tokens_a & tokens_b)
    jaccard = overlap / len(tokens_a | tokens_b)
    return (overlap / min(len(tokens_a), len(tokens_b)) + jaccard) / 2


def is_acronym_of(short_title, long_title):
    """'AESIs' for 'Adverse Events of Special Interest'."""
    words = title_words(short_title)
    initials = "".join(word[0] for word in title_words(long_title))
    return len(words) == 1 and len(initials) >= MIN_ACRONYM_LENGTH and words[0] == initials


def row_similarity(title, row):
    if any(is_acronym_of(title, candidate) for candidate in row.titles):
        return 1.0
    return max(title_similarity(title, candidate) for candidate in row.titles)


def parent_number(number):
    return number.rpartition(".")[0]


def distinguishing_words(title):
    return set(title_words(title)) - GENERIC_TITLE_WORDS


def child_rows(crosswalk, parent_row_number):
    return [row for row in crosswalk.values() if parent_number(row.number) == parent_row_number]


def is_unreadable(title):
    return title.strip() in UNREADABLE_TITLES


def match_row(section, parent_row, crosswalk, taken):
    parent_row_number = parent_row.number if parent_row else ""
    candidates = [row for row in child_rows(crosswalk, parent_row_number) if row.number not in taken]
    if not candidates:
        return None
    best_score, best = max(((row_similarity(section.title, row), row) for row in candidates),
                           key=lambda pair: pair[0])
    title_match = best if best_score >= TITLE_ONLY_MIN_SIMILARITY else None
    last_component = section.number.rsplit(".", 1)[-1]
    expected = f"{parent_row_number}.{last_component}" if parent_row_number else last_component
    by_number = next((row for row in candidates if row.number == expected), None)
    if by_number is None or parent_row_number == APPENDIX_CHAPTER:
        return title_match
    if is_unreadable(section.title):
        return by_number
    number_score = row_similarity(section.title, by_number)
    # A sponsor who inserts a section shifts the numbers after it, so a sibling whose title fits
    # clearly better wins over the row at the expected number.
    if title_match and best_score > number_score:
        return title_match
    return by_number if number_score >= EXPECTED_NUMBER_MIN_SIMILARITY else None


def match_children(sections, parent_row, crosswalk, matches):
    taken = set()
    for section in sections:
        row = match_row(section, parent_row, crosswalk, taken)
        if row:
            taken.add(row.number)
            matches[id(section)] = row
            match_children(section.children, row, crosswalk, matches)


def rescue_moved_sections(sections, crosswalk, matches):
    """Match sections a sponsor moved to another level of the same chapter (eg 9.1.2 Multiplicity
    Adjustment written as 9.6), by an identical title among rows nothing else matched."""
    for section in sections:
        row = matches.get(id(section))
        if row is None:
            continue
        chapter = row.number.split(".")[0]
        for child in section.children:
            if id(child) in matches or is_unreadable(child.title):
                continue
            used = {matched.number for matched in matches.values()}
            candidates = [candidate for candidate in crosswalk.values()
                          if candidate.number.startswith(chapter + ".") and candidate.number not in used]
            scored = [(row_similarity(child.title, candidate), candidate) for candidate in candidates]
            if not scored:
                continue
            score, best = max(scored, key=lambda pair: pair[0])
            if score >= MOVED_SECTION_MIN_SIMILARITY:
                matches[id(child)] = best
                match_children(child.children, best, crosswalk, matches)
        rescue_moved_sections(section.children, crosswalk, matches)


def match_sections(roots, crosswalk):
    """Pair each source section with a crosswalk row, keyed by id(section)."""
    matches = {}
    match_children(roots, None, crosswalk, matches)
    rescue_moved_sections(roots, crosswalk, matches)
    return matches


# ---------------------------------------------------------------- output tree

def build_output_tree(outline):
    roots = []
    by_number = {}
    for heading in outline:
        node = OutNode(heading.level, heading.title, template_number=heading.number)
        parent = by_number.get(parent_number(heading.number))
        (parent.children if parent else roots).append(node)
        by_number[heading.number] = node
    return roots, by_number


def add_content(node, section, blocks):
    """The first source section to put content in a node goes in plain; later ones get their title."""
    node.source_numbers.append(section.number)
    node.source_titles.append(section.title)
    if not blocks:
        return
    if node.blocks:
        node.blocks.append(Subheading(section.title, "bold", section.page))
    node.blocks.extend(blocks)


def add_extra_node(parent, section):
    """Keep an unplaced source section as a heading under `parent`; returns the node holding its content.

    Below the deepest template heading style a heading would be styled as the deepest level while
    still counting as a deeper number, so the section is folded into `parent` under a bold title.
    """
    if parent.level >= MAX_HEADING_LEVEL:
        parent.source_numbers.append(section.number)
        parent.source_titles.append(section.title)
        parent.blocks.append(Subheading(section.title, "bold", section.page))
        parent.blocks.extend(section.blocks)
        for child in section.children:
            add_extra_node(parent, child)
        return parent
    # The "Appendix 13" label numbers the source appendix, which no longer matches its place here.
    node = OutNode(parent.level + 1, APPENDIX_LABEL.sub("", section.title), from_template=False)
    parent.children.append(node)
    add_content(node, section, section.blocks)
    for child in section.children:
        add_extra_node(node, child)
    return node


class Placer:
    def __init__(self, matches, nodes_by_number, report):
        self.matches = matches
        self.nodes = nodes_by_number
        self.report = report
        self.unmatched = []  # (section, node it became)
        self.also_sources = set()
        self.primary_nodes = {}  # source number -> node that cross-references to it resolve to
        self.investigator_sections = []
        self.template_numbers = {id(node): number for number, node in nodes_by_number.items()}

    def empty_template_match(self, section, near):
        """The one unfilled template section in `near`'s chapter that `section`'s title names, or None.

        A sponsor's "Prohibited Interventions and Procedures" is the M11 "Prohibited Concomitant Therapy"
        section: the same title once the words every title shares are set aside, and one title's remaining
        words are all in the other's.
        """
        words = distinguishing_words(section.title)
        near_number = self.template_numbers.get(id(near))
        if not words or near_number is None:
            return None
        chapter = near_number.split(".")[0]
        matches = []
        for number, node in self.nodes.items():
            if not (number.startswith(chapter + ".") and node.level >= TEMPLATE_DROPPABLE_LEVEL):
                continue
            if node.blocks or node.source_numbers or "[" in node.title:
                continue  # filled already, or a repeating "[#]" heading that the crosswalk and splitters fill
            template_words = distinguishing_words(resolve_template_title(node))
            if template_words and (template_words <= words or words <= template_words):
                matches.append(node)
        return matches[0] if len(matches) == 1 else None

    def place_unmatched(self, section, parent):
        """Put a section with no crosswalk row under the template section its title names, else as an extra heading."""
        node = self.empty_template_match(section, parent)
        if node is None:
            self.unmatched.append((section, add_extra_node(parent, section)))
            return
        add_content(node, section, section.blocks)
        self.report.notes.append(f"{section.number} {section.title} matched by title to template section {node.title}")
        for child in section.children:
            self.place_unmatched(child, node)

    def place_chapter(self, section):
        row = self.matches.get(id(section))
        if row:
            self.place_matched(section, row, chapter_node=self.chapter_node(row))
        elif INVESTIGATOR_AGREEMENT.search(section.title):
            self.investigator_sections.append(section)
        else:
            self.unmatched.append((section, add_extra_node(self.nodes[SUPPORTING_DETAILS_NODE], section)))

    def chapter_node(self, row):
        if row.number == APPENDIX_CHAPTER:
            return self.nodes[SUPPORTING_DETAILS_NODE]
        return self.nodes[row.target.split(".")[0]]

    def place_matched(self, section, row, chapter_node=None):
        target = self.nodes[row.target]
        if section.number.count(".") != row.number.count("."):
            self.report.notes.append(
                f"{section.number} {section.title} matched by title to v010 {row.number} {row.titles[0]}")
        if row.also:
            self.also_sources.add(section.number)
            self.report.split_review.append(
                f"{section.number} {section.title} -> {row.target}; also review {', '.join(row.also)}")
        if row.number == OBJECTIVES_ROW:
            self.place_objectives(section, target)
            return
        if row.number in SPLITTERS:
            self.primary_nodes[section.number] = target
            getattr(self, SPLITTERS[row.number])(section, target)
            return
        if row.number == SYNOPSIS_ROW:
            self.place_synopsis(section, target)
        else:
            add_content(target, section, section.blocks)
        for child in section.children:
            child_row = self.matches.get(id(child))
            if child_row:
                self.place_matched(child, child_row)
            else:
                self.place_unmatched(child, chapter_node or target)

    def place_handling(self, section, target):
        """Split 6.2 over 6.6.1-6.6.3; sub-sections with a telling title go to that node whole."""
        self.place_split(section, split_handling(section.blocks))
        for child in section.children:
            node_number = handling_child_target(child.title)
            if node_number is None:
                self.place_unmatched(child, target)
                self.report.split_review.append(
                    f"{child.number} {child.title} has no telling title; placed by title or kept as a heading under {target.title}")
                continue
            node = self.nodes[node_number]
            add_content(node, child, child.blocks)
            for grandchild in child.children:
                self.unmatched.append((grandchild, add_extra_node(node, grandchild)))
            self.report.split_review.append(f"{child.number} {child.title} placed whole in {node_number}")

    def place_assignment(self, section, target):
        self.place_split(section, split_assignment(section.blocks))
        for child in section.children:
            self.place_unmatched(child, target)

    def place_split(self, section, split):
        for node_number, blocks in split.parts.items():
            add_content(self.nodes[node_number], section, blocks)
        self.report.split_review.append(f"{section.number} {section.title} split by keyword: {split.summary()}")

    def place_synopsis(self, section, target):
        split = split_synopsis(section.blocks)
        add_content(target, section, [])
        add_content(self.nodes[SYNOPSIS_OBJECTIVES_NODE], section, split.objectives)
        add_content(self.nodes[SYNOPSIS_DESIGN_NODE], section, split.design)
        self.report.synopsis_dropped.extend(split.dropped)

    def place_objectives(self, section, target):
        split = split_objectives(section.blocks)
        add_content(target, section, split.preamble)
        for part, node_number in OBJECTIVES_NODES.items():
            add_content(self.nodes[node_number], section, getattr(split, part))
        if not split.split:
            self.report.objectives.append("no dividers found; all of section 3 placed under 3.1")
        for child in section.children:
            part = objectives_part(child.title)
            if part is None:
                node = add_extra_node(target, child)
                self.unmatched.append((child, node))
                self.report.objectives.append(f"{child.number} {child.title} kept as an extra heading after 3.3")
                continue
            node = self.nodes[OBJECTIVES_NODES[part]]
            add_content(node, child, child.blocks)
            for grandchild in child.children:
                self.unmatched.append((grandchild, add_extra_node(node, grandchild)))
            self.report.objectives.append(f"{child.number} {child.title} placed in {OBJECTIVES_NODES[part]}")


def objectives_part(title):
    for part, pattern in OBJECTIVES_PART_WORDS.items():
        if pattern.search(title):
            return part
    return None


def resolve_template_title(node):
    if "[" not in node.title:
        return node.title
    if node.source_titles and not is_unreadable(node.source_titles[0]):
        return node.source_titles[0]
    return re.sub(r"\s+", " ", node.title.replace("[#]", "").replace("[", "").replace("]", "")).strip()


def prune_tree(node):
    """Drop empty template L3/L4 nodes below `node`; their source numbers move up to the parent."""
    for child in node.children:
        prune_tree(child)
    kept = []
    for child in node.children:
        if is_droppable(child):
            node.source_numbers.extend(child.source_numbers)
        else:
            kept.append(child)
    node.children = kept


def is_droppable(node):
    return (node.from_template and node.level >= TEMPLATE_DROPPABLE_LEVEL and not node.required
            and not node.blocks and not node.children)


def require_handling_nodes(nodes_by_number):
    group = [nodes_by_number[number] for number in HANDLING_NODES]
    if any(node.blocks or node.children for node in group):
        for node in group:
            node.required = True


def placeholder_paragraph():
    return Paragraph([Span(NO_CONTENT_PLACEHOLDER, False, False, False, 0)])


def number_nodes(nodes, prefix=""):
    for index, node in enumerate(nodes, start=1):
        node.number = f"{prefix}{index}"
        number_nodes(node.children, node.number + ".")


def iter_nodes(nodes):
    for node in nodes:
        yield node
        yield from iter_nodes(node.children)


def iter_blocks(result):
    yield from result.front
    yield from result.amendment_details
    for node in iter_nodes(result.nodes):
        yield from node.blocks


# ---------------------------------------------------------------- front matter

def front_parts(front):
    """Split front matter into (heading or None, blocks) runs, one per FrontHeading."""
    parts = [(None, [])]
    for block in front:
        if isinstance(block, FrontHeading):
            parts.append((block, []))
        else:
            parts[-1][1].append(block)
    return [part for part in parts if part[0] or part[1]]


def investigator_agreement_blocks(sections):
    blocks = []
    for section in sections:
        blocks.append(FrontHeading(section.title, section.page))
        blocks.extend(section.blocks)
        for child in section.children:
            blocks.extend(investigator_agreement_blocks([child]))
    return blocks


def rearrange_front(front, abbreviations_node, investigator_sections, report):
    kept, amendment_details = [], []
    agreement = investigator_agreement_blocks(investigator_sections)
    agreement_inserted = not agreement
    for heading, blocks in front_parts(front):
        title = heading.title if heading else ""
        if heading and ABBREVIATIONS_FRONT_HEADING.search(title):
            if abbreviations_node.blocks:
                abbreviations_node.blocks.append(Subheading(title, "bold", heading.page))
            abbreviations_node.blocks.extend(blocks)
            report.notes.append(f"front matter '{title}' moved to {ABBREVIATIONS_NODE}")
            continue
        if heading and AMENDMENT_FRONT_HEADING.search(title):
            amendment_details.append(heading)
            amendment_details.extend(blocks)
            continue
        if heading:
            kept.append(heading)
        kept.extend(blocks)
        if not agreement_inserted and heading and SPONSOR_SIGNATORY.search(title):
            kept.extend(agreement)
            agreement_inserted = True
    if not agreement_inserted:
        kept.extend(agreement)
    for section in investigator_sections:
        report.notes.append(f"source section {section.number} {section.title} moved to front matter")
    return kept, amendment_details


# ---------------------------------------------------------------- main entry

def restructure(doc, crosswalk, outline):
    report = Report()
    nodes, nodes_by_number = build_output_tree(outline)
    tree = build_tree(doc.body)
    placer = Placer(match_sections(tree, crosswalk), nodes_by_number, report)
    for chapter in tree:
        placer.place_chapter(chapter)

    front, amendment_details = rearrange_front(
        doc.front, nodes_by_number[ABBREVIATIONS_NODE], placer.investigator_sections, report)

    for node in iter_nodes(nodes):
        if node.from_template:
            node.title = resolve_template_title(node)
    require_handling_nodes(nodes_by_number)
    for root in nodes:
        prune_tree(root)

    placeholder_nodes = [node for node in iter_nodes(nodes)
                         if node.from_template and (node.level < TEMPLATE_DROPPABLE_LEVEL or node.required)
                         and not node.blocks and not node.children]
    for node in placeholder_nodes:
        node.blocks.append(placeholder_paragraph())
    number_nodes(nodes)

    for node in placeholder_nodes:
        report.placeholders.append(f"{node.number} {node.title}")
    for section, node in placer.unmatched:
        parent = parent_number(section.number)
        matched_parent = any(source.number == parent and placer.matches.get(id(source))
                             for source in iter_nodes(tree))
        placement = "no crosswalk row, kept under matched parent" if matched_parent else "no parent matched; retained as extra heading"
        report.unmatched.append(f"{section.number} {section.title} -> {node.number} ({placement})")

    number_map = {source: node.number for source, node in placer.primary_nodes.items() if node.number}
    for node in iter_nodes(nodes):
        for source_number in node.source_numbers:
            number_map.setdefault(source_number, node.number)

    landscape_pages = {block.page: block.landscape for block in doc.body if isinstance(block, PageMark)}
    return Result(nodes, front, amendment_details, number_map, placer.also_sources, landscape_pages, report)
