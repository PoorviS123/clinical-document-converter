"""Build the review desk's outline and queue from a job's FHIR bundle and the backend's run report."""
from pathlib import Path
import json
import re

TARGET_ELEMENT_URL = "http://hl7.org/fhir/StructureDefinition/targetElement"
TASK_BOILERPLATE = " The drafted value had low confidence and was left out of this export."
CROSS_REF_PATTERN = re.compile(r"^Cross-reference to review: p\d+: (\S+) -> (\S+), content also belongs elsewhere: (.*)$", re.MULTILINE)
CROSS_REFS_REWRITTEN_PATTERN = re.compile(r"^Cross-references rewritten: (\d+)$", re.MULTILINE)
OMITTED_PATTERN = re.compile(r"^omitted \d+ optional section\(s\): (.*)$", re.MULTILINE)
SECTION_NUMBER_PATTERN = re.compile(r"\d+(\.\d+)*")
TITLE_PAGE = "Title page"


def summarize_review(fhir_path: Path, report_path: Path) -> dict[str, object] | None:
    """None when the job has no FHIR bundle. The run report is optional: demo jobs don't have one."""
    if not fhir_path.is_file():
        return None
    bundle = json.loads(fhir_path.read_text(encoding="utf-8"))
    resources = [entry["resource"] for entry in bundle.get("entry", [])]
    composition = next((r for r in resources if r["resourceType"] == "Composition"), {})

    titles: dict[str, str] = {}
    top_level_of: dict[str, str] = {}
    for top in composition.get("section", []):
        for section in walk_sections(top):
            titles[section["id"]] = section.get("title", "")
            top_level_of[section["id"]] = top["id"]

    drafted_ids = [target_element(target) for r in resources if r["resourceType"] == "Provenance" for target in r.get("target", [])]
    tasks = [describe_task(r, titles) for r in resources if r["resourceType"] == "Task"]

    outline = []
    for top in composition.get("section", []):
        drafted = [titles[section_id] for section_id in drafted_ids if top_level_of.get(section_id) == top["id"]]
        needs_input = [task for task in tasks if top_level_of.get(task["section_id"]) == top["id"]]
        number, name = split_number(top.get("title", ""))
        outline.append({"id": top["id"], "number": number, "name": name, "drafted": list(dict.fromkeys(drafted)), "needs_input": needs_input})

    review = {
        "status": composition.get("status"),
        "outline": outline,
        "drafted_count": len(drafted_ids),
        "tasks": tasks,
        "cross_refs": None,
        "cross_refs_rewritten": None,
        "omitted": None,
    }
    if report_path.is_file():
        review.update(read_run_report(report_path.read_text(encoding="utf-8", errors="replace")))
    return review


def walk_sections(section: dict):
    yield section
    for child in section.get("section", []):
        yield from walk_sections(child)


def split_number(title: str) -> tuple[str, str]:
    """"4.2 Rationale" -> ("4.2", "Rationale"). Sections the backend added have no number."""
    number, _, name = title.partition(" ")
    if name and SECTION_NUMBER_PATTERN.fullmatch(number):
        return number, name
    return "", title


def target_element(reference: dict) -> str | None:
    return next((ext["valueUri"] for ext in reference.get("extension", []) if ext["url"] == TARGET_ELEMENT_URL), None)


def describe_task(task: dict, titles: dict[str, str]) -> dict[str, object]:
    """Split "Section 4.2.4 Title: enter X, Y. <boilerplate>" into where and what. Title-page tasks point at the ResearchStudy, not a section."""
    section_id = target_element(task.get("focus", {}))
    description = task.get("description", "").removesuffix(TASK_BOILERPLATE)
    where, _, what = description.partition(": enter ")
    return {
        "section_id": section_id,
        "where": titles.get(section_id, where) if section_id else TITLE_PAGE,
        "what": what.rstrip(".") or description,
    }


def read_run_report(text: str) -> dict[str, object]:
    rewritten = CROSS_REFS_REWRITTEN_PATTERN.search(text)
    omitted = OMITTED_PATTERN.search(text)
    return {
        "cross_refs": [{"source": source, "target": target, "excerpt": excerpt} for source, target, excerpt in CROSS_REF_PATTERN.findall(text)],
        "cross_refs_rewritten": int(rewritten.group(1)) if rewritten else None,
        "omitted": [name.split("#")[0] for name in omitted.group(1).split(", ")] if omitted else [],
    }
