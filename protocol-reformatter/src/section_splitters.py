"""Keyword splitters for v010 sections that spread over several v011 sections.

6.2 Preparation, Handling, Storage, and Accountability -> 6.6.1 / 6.6.2 / 6.6.3
6.3 Assignment to Study Intervention                   -> 6.7.1 / 6.7.2 (and 6.6.3 for dispensing)
"""

import re
from dataclasses import dataclass, field

from pdf_to_cpt_docx import Paragraph

PREPARATION_NODE = "6.6.1"
STORAGE_NODE = "6.6.2"
ACCOUNTABILITY_NODE = "6.6.3"
ASSIGNMENT_NODE = "6.7.1"
RANDOMIZATION_NODE = "6.7.2"

# Each rule is (target node, keyword pattern). On a tie in hits the earlier rule wins.
# Patterns match word starts, so "prepar" covers "prepared" but "kit" does not match "skit".
HANDLING_RULES = [
    (PREPARATION_NODE, re.compile(r"\b(prepar|reconstitut|dilut|thaw|mix|assembl)", re.IGNORECASE)),
    (STORAGE_NODE, re.compile(r"\b(transit|stored in a secure|storage|temperature|packag|label|kit)", re.IGNORECASE)),
    (ACCOUNTABILITY_NODE, re.compile(
        r"\b(only participants|accountab|reconcil|disposition|destr|return|dispens|supply)", re.IGNORECASE)),
]
HANDLING_DEFAULT_NODE = STORAGE_NODE  # the crosswalk's primary target for 6.2

# Titles of 6.2 sub-sections that name their v011 target outright.
HANDLING_CHILD_TITLES = [
    (PREPARATION_NODE, re.compile(r"prepar", re.IGNORECASE)),
    (STORAGE_NODE, re.compile(r"packag|labell?ing|storage|transit", re.IGNORECASE)),
    (ACCOUNTABILITY_NODE, re.compile(r"accountab|supply|disposition", re.IGNORECASE)),
]

# The identification rule comes first so that "assigned a patient number through IRT" (one hit each) is 6.7.1.
ASSIGNMENT_RULES = [
    (ASSIGNMENT_NODE, re.compile(
        r"\b(identification code|identification number|unique identifier|participant number|patient number"
        r"|enrol|screening number)", re.IGNORECASE)),
    (RANDOMIZATION_NODE, re.compile(r"\b(random|stratif|IRT|IWRS|block|ratio|kit number|allocat)", re.IGNORECASE)),
]
ASSIGNMENT_DEFAULT_NODE = RANDOMIZATION_NODE
DISPENSING = re.compile(r"\b(dispens|return)", re.IGNORECASE)


@dataclass
class SectionSplit:
    """Blocks per v011 node number, in source order within each node."""
    parts: dict = field(default_factory=dict)

    def add(self, node_number, block):
        self.parts.setdefault(node_number, []).append(block)

    def summary(self):
        return ", ".join(f"{node}: {len(blocks)}" for node, blocks in sorted(self.parts.items())) or "no blocks"


def best_rule_target(text, rules):
    """The node whose pattern has the most distinct hits in `text`, or None when none match."""
    best_node, best_hits = None, 0
    for node_number, pattern in rules:
        hits = len({match.group(1).lower() for match in pattern.finditer(text)})
        if hits > best_hits:
            best_node, best_hits = node_number, hits
    return best_node


def split_blocks(blocks, rules, default_node, special_target=None):
    """Route each paragraph by keywords; tables, figures and other blocks stay with the block before them."""
    split = SectionSplit()
    current = default_node
    for block in blocks:
        if isinstance(block, Paragraph) and re.search(r"\w", block.text):  # a bare bullet mark stays with its list
            current = special_target(block.text) if special_target else None
            current = current or best_rule_target(block.text, rules) or default_node
        split.add(current, block)
    return split


def split_handling(blocks):
    return split_blocks(blocks, HANDLING_RULES, HANDLING_DEFAULT_NODE)


def accountability_target(text):
    """6.3 sentences about dispensing or returning go to 6.6.3 unless they are about randomization."""
    if DISPENSING.search(text) and best_rule_target(text, ASSIGNMENT_RULES) is None:
        return ACCOUNTABILITY_NODE
    return None


def split_assignment(blocks):
    return split_blocks(blocks, ASSIGNMENT_RULES, ASSIGNMENT_DEFAULT_NODE, accountability_target)


def handling_child_target(title):
    """The v011 6.6.x node a 6.2.x sub-section title points to, or None."""
    return next((node for node, pattern in HANDLING_CHILD_TITLES if pattern.search(title)), None)
