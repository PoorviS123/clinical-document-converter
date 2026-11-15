"""Drop optional sections that are still empty once drafting is done, instead of leaving red placeholders."""

from converter.generate.fill import JsonObject, allowed_statuses, now_iso

OMITTED = "omitted"
SCRIPT_ACTOR: JsonObject = {"kind": "script", "name": "converter.omit", "version": "1"}


def omit_empty_optional(protocol: JsonObject, schema: JsonObject) -> list[str]:
    """Set `omitted` on every missing section the M11 spec allows to be deleted; return their numbers.

    A section is omitted only when it has no content and each of its subsections is omitted too, because
    the schema requires an omitted section to omit its whole subtree.
    """
    omitted: list[str] = []
    sections = protocol["sections"]
    assert isinstance(sections, dict)
    for key, section in sections.items():
        omit_branch(key, section, schema, omitted)
    log = protocol.setdefault("processing_log", [])
    assert isinstance(log, list)
    log.append({
        "step": "omit_empty_optional", "actor": dict(SCRIPT_ACTOR), "at": now_iso(),
        "summary": f"omitted {len(omitted)} optional section(s): {', '.join(omitted) or 'none'}",
    })
    return omitted


def omit_branch(key: str, section: JsonObject, schema: JsonObject, omitted: list[str]) -> bool:
    """True when `section` is now omitted."""
    children = section.get("subsections")
    child_flags = [
        omit_branch(child_key, child, schema, omitted)
        for child_key, child in (children.items() if isinstance(children, dict) else [])
    ]
    can_omit = (
        section.get("status") == "missing"
        and OMITTED in allowed_statuses(schema, key)
        and all(child_flags)
        and not section.get("additional_subsections")
    )
    if can_omit:
        section["status"] = OMITTED
        omitted.append(str(section.get("number") or key))
    elif children_omitted_with_parent(schema, key):
        restore_omitted_children(section, omitted)
    return can_omit


def children_omitted_with_parent(schema: JsonObject, key: str) -> bool:
    """True when the schema only lets a child be omitted if its parent is omitted too (a section kept as a unit)."""
    definition = schema["$defs"].get("Section_" + key.replace(".", "_").replace("#", "__"), {})
    return any("not" in rule.get("if", {}) for rule in definition.get("allOf", []))


def restore_omitted_children(section: JsonObject, omitted: list[str]) -> None:
    children = section.get("subsections")
    for child_key, child in (children.items() if isinstance(children, dict) else []):
        if child.get("status") == OMITTED:
            child["status"] = "missing"
            omitted.remove(str(child.get("number") or child_key))
            restore_omitted_children(child, omitted)
