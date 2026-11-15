"""Prompt for drafting one v011 section that has no v010 source."""

import json

MAX_CONTEXT_CHARS = 24000
MAX_DRAFT_WORDS = 250  # about half a single-spaced page

INSTRUCTIONS = """\
You are drafting one section of a clinical trial protocol in the TransCelerate CPT v011 / ICH M11 format.
Draft only from facts stated in the SOURCE PROTOCOL below. Do not invent numbers, names, doses, dates or
procedures. If the source gives no basis for this section, answer with status "missing".
If the source clearly says the topic does not apply, answer with status "not_applicable".

What to write:
- Follow the ICH M11 GUIDANCE for this section. It says what the section is for and what belongs elsewhere.
- Write the section's own content. Do not restate the objectives, endpoints, estimands or design summary unless the
  guidance asks for it. Point to another section by number (for example "see Section 6.2") instead of repeating it.
- A rationale needs a reason. A reason is a purpose, goal or background the source ties to that design choice (for
  example "a placebo add-on design to test X on top of standard of care"). Use those and say them plainly. A list of
  design features is not a reason: if the source only describes what the trial does and gives nothing that
  explains the choice, answer "missing". Never invent a justification.
- For a "Rationale for Other Trial Design Aspects" style section, write only about a design aspect that the
  earlier rationale sections do not cover and the source explains. Do not recap visits, procedures or adjudication.
- Silence is not a fact. Never write that something is absent, not planned or not used unless the source says so.
- Write as finished protocol text: third person, present or future tense ("Participants will be randomized..."), the
  source's own terms and abbreviations. Never mention "the source", "the text" or "the protocol provided".
- Do not repeat the section heading, add citations the source does not contain, or pad with general background.

Answer with one JSON object and nothing else:
{
  "status": "populated" | "not_applicable" | "missing",
  "paragraphs": ["plain text paragraph", ...],
  "basis": ["source section number or heading each fact came from", ...],
  "confidence": "high" | "medium" | "low"
}
Use "paragraphs": [] unless status is "populated". Plain text only, no markdown, no bullet characters.
Keep the whole draft under {max_words} words (half a page) in total across all paragraphs. Be concise and
keep only what this section needs.
Confidence: "high" when the source states the content directly, "medium" when you combined facts from several
places, "low" when you inferred or the source is thin.
""".replace("{max_words}", str(MAX_DRAFT_WORDS))


def build_prompt(
    number: str, title: str, guidance: list[str], source_text: str, example: str | None = None
) -> str:
    trimmed = source_text[:MAX_CONTEXT_CHARS]
    guidance_text = "\n".join(f"- {line}" for line in guidance) or "(none)"
    example_block = (
        f"STYLE EXAMPLE, from a different trial. Copy its tone and structure only, none of its facts:\n{example}\n\n"
        if example else ""
    )
    return (
        f"{INSTRUCTIONS}\n"
        f"SECTION TO DRAFT: {number} {title}\n\n"
        f"ICH M11 GUIDANCE FOR THIS SECTION:\n{guidance_text}\n\n"
        f"{example_block}"
        f"SOURCE PROTOCOL:\n{trimmed}\n"
    )


def parse_response(text: str) -> dict[str, object]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("response JSON is not an object")
    return parsed
