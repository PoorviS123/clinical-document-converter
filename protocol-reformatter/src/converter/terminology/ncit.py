"""NCI Thesaurus lookups from the flat file `Thesaurus.txt` (see src/fetch_terminology.py).

One tab-separated row per concept: code, url, parents, synonyms (|-separated, the first is the
preferred name), definition, display name, status, semantic type, subsets.
"""

from collections import defaultdict
from pathlib import Path

from converter.terminology import Term, normalize

NCIT_FILE = Path(__file__).resolve().parents[3] / "resources" / "terminology" / "Thesaurus.txt"
CODE_COLUMN, SYNONYMS_COLUMN, SEMANTIC_TYPE_COLUMN, SUBSETS_COLUMN = 0, 3, 7, 8
LIST_SEPARATOR = "|"


class NcitSource:
    """Concepts that sit in a subset or have one of the semantic types, matched by name or synonym.

    `system` is what the resulting codes are labelled with in the protocol JSON.
    """

    def __init__(self, system: str, *, subset: str | None = None,
                 semantic_types: frozenset[str] = frozenset(), path: Path = NCIT_FILE) -> None:
        self.system = system
        self.subset = subset
        self.semantic_types = semantic_types
        self.path = path
        self.index: dict[str, list[Term]] | None = None

    def find(self, text: str) -> list[Term]:
        if self.index is None:
            self.index = self.build_index()
        return self.index.get(normalize(text), [])

    def wanted(self, row: list[str]) -> bool:
        if self.subset and self.subset in row[SUBSETS_COLUMN].split(LIST_SEPARATOR):
            return True
        return bool(self.semantic_types.intersection(row[SEMANTIC_TYPE_COLUMN].split(LIST_SEPARATOR)))

    def build_index(self) -> dict[str, list[Term]]:
        index: dict[str, list[Term]] = defaultdict(list)
        if not self.path.exists():
            return index
        with self.path.open(encoding="utf-8") as lines:
            for line in lines:
                row = line.rstrip("\n").split("\t")
                if len(row) <= SUBSETS_COLUMN or not self.wanted(row):
                    continue
                names = row[SYNONYMS_COLUMN].split(LIST_SEPARATOR)
                term = Term(self.system, row[CODE_COLUMN], names[0])
                for name in dict.fromkeys(normalize(name) for name in names):
                    index[name].append(term)
        return index
