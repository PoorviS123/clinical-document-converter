"""Look up coded terms for fields the schema types as external codes (dose form, route, drug, condition).

A TermSource answers one question: which coded terms match this text? The TermRegistry maps a field
name to the sources to try, in order. To add a terminology (MedDRA, EDQM, WHO INN, ...), write a
TermSource and list it for the fields it covers in `default_registry`.
"""

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Term:
    system: str  # a value of the schema's ExternalCodeSlot `system` enum
    code: str
    decode: str


class TermSource(Protocol):
    def find(self, text: str) -> list[Term]:
        """Terms whose name or synonym equals the text. Empty when the source isn't available."""


def normalize(text: str) -> str:
    """Case, spacing and punctuation-insensitive form used to compare names."""
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


class TermRegistry:
    def __init__(self, sources_by_field: dict[str, list[TermSource]]) -> None:
        self.sources_by_field = sources_by_field

    def resolve(self, field_name: str, text: str) -> Term | None:
        """The term for the text, from the first source that matches. None when no source matches
        or the first match is ambiguous (several different codes): a wrong code is worse than none."""
        for source in self.sources_by_field.get(field_name, []):
            terms = list(dict.fromkeys(source.find(text)))
            if len(terms) == 1:
                return terms[0]
            if terms:
                return None
        return None
