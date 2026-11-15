"""Validate protocol JSON against schema/protocol.schema.json plus the cross-field checks."""

import sys
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"
sys.path.insert(0, str(SCHEMA_DIR))
from validate_protocol import SCHEMA_PATH, load_json, validate_document  # noqa: E402

__all__ = ["SCHEMA_PATH", "load_json", "validate_document", "load_schema"]


def load_schema() -> dict[str, object]:
    return load_json(SCHEMA_PATH)
