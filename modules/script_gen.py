"""Stage 1 — script_gen: generate a structured tips-format video script.

Style: Hugh Knows — punchy, direct, numbered tips, second-person, short sentences.

Input  : a topic dict {title, hook_premise, topic, research_notes?}
Output : a structured script object:
         {title, hook, sections:[{role, tip_name?, text}], est_words}
"""

import json
import logging
import os
import re

from anthropic import Anthropic
from dotenv import load_dotenv

import config

load_dotenv()

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are the lead scriptwriter for Icarus Wings, an educational YouTube channel. "
    "You write punchy, direct, tips-format video scripts — NOT cinematic narratives. "
    "You MUST respond with ONLY a single valid JSON object and nothing else — no "
    "markdown, no code fences, no commentary before or after.\n\n"

    "## FORMAT\n\n"
    "The video has exactly 8 sections in this order:\n"
    "  1. intro  — cold open hook. Name the viewer's pain, tease that there's a better way. "
    "~40-60 words. End with a forward pull: 'Here's what actually works.'\n"
    "  2. tip_1  — first tip (include tip_name)\n"
    "  3. tip_2  — second tip (include tip_name)\n"
    "  4. tip_3  — third tip (include tip_name)\n"
    "  5. tip_4  — fourth tip (include tip_name)\n"
    "  6. tip_5  — fifth tip (include tip_name)\n"
    "  7. tip_6  — sixth tip (include tip_name)\n"
    "  8. outro  — cheat sheet. List all 6 tip names, one line each. End with a CTA.\n\n"

    "## EACH TIP MUST HAVE THREE BEATS\n\n"
    "Every tip section must contain these three beats in order:\n"
    "  1. PROBLEM — what most people do wrong. One or two sentences.\n"
    "  2. PRINCIPLE — the named rule or concept (e.g. 'This is called Parkinson's Law.'). "
    "One sentence.\n"
    "  3. HOW — exactly what to do. Concrete steps. Two or three sentences max.\n\n"

    "## VOICE — NON-NEGOTIABLE\n\n"
    "- Short sentences. Maximum 12 words each.\n"
    "- Second person throughout: you, your, your brain.\n"
    "- Direct. No filler ('in this video', 'let's dive in', 'as you can see').\n"
    "- Contrast works: 'Not 3 hours. 20 minutes.'\n"
    "- Rhetorical questions are allowed: 'Why does this work?'\n"
    "- Casual but not sloppy. Conversational authority.\n"
    "- No invented statistics. No 'studies show' without a source in research notes.\n"
    "- No cinematic or atmospheric language.\n\n"

    "## LENGTH\n\n"
    f"Target {config.TARGET_WORDS_MIN}–{config.TARGET_WORDS_MAX} total words across all sections.\n"
    "Each tip section: 50-80 words. Intro: 40-60 words. Outro: 40-60 words.\n\n"

    "## OUTPUT JSON — EXACTLY THESE KEYS\n\n"
    '{\n'
    '  "title": "working video title",\n'
    '  "hook": "cold open line — one punchy sentence shown before the intro section",\n'
    '  "sections": [\n'
    '    {"role": "intro", "text": "..."},\n'
    '    {"role": "tip_1", "tip_name": "Trick Your Brain", "text": "..."},\n'
    '    {"role": "tip_2", "tip_name": "...", "text": "..."},\n'
    '    {"role": "tip_3", "tip_name": "...", "text": "..."},\n'
    '    {"role": "tip_4", "tip_name": "...", "text": "..."},\n'
    '    {"role": "tip_5", "tip_name": "...", "text": "..."},\n'
    '    {"role": "tip_6", "tip_name": "...", "text": "..."},\n'
    '    {"role": "outro", "text": "..."}\n'
    '  ],\n'
    '  "est_words": 480\n'
    '}\n\n'

    "Study the GOLD-STANDARD EXEMPLAR below for tone and rhythm. "
    "Do NOT copy its topic — write for the new angle you are given."
)


def _client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (check your .env file)")
    return Anthropic(api_key=api_key)


def _load_exemplar() -> str:
    try:
        with open(config.EXEMPLAR_SCRIPT_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        logger.warning("Could not load exemplar script: %s", exc)
        return ""


def _strip_to_json(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text, start)
            return json.dumps(obj)
        except json.JSONDecodeError:
            last = text.rfind("}")
            if last > start:
                return text[start : last + 1]
    return text


def _count_words(*chunks: str) -> int:
    return sum(len(re.findall(r"\b[\w'-]+\b", c or "")) for c in chunks)


def _validate(data: dict) -> None:
    required = {"title": str, "hook": str, "sections": list, "est_words": (int, float)}
    for key, expected in required.items():
        if key not in data:
            raise ValueError(f"Missing required key: {key!r}")
        if not isinstance(data[key], expected):
            raise ValueError(
                f"Key {key!r} wrong type: expected {expected}, "
                f"got {type(data[key]).__name__}"
            )

    if not data["hook"].strip():
        raise ValueError("'hook' must be a non-empty cold open line")

    roles = [s.get("role") for s in data["sections"] if isinstance(s, dict)]
    if roles != config.SCRIPT_SECTION_ROLES:
        raise ValueError(
            f"'sections' roles must be exactly {config.SCRIPT_SECTION_ROLES} in order; "
            f"got {roles}"
        )

    tip_roles = {r for r in config.SCRIPT_SECTION_ROLES if r.startswith("tip_")}
    for s in data["sections"]:
        if not isinstance(s.get("text"), str) or not s["text"].strip():
            raise ValueError(f"Section {s.get('role')!r} has empty/invalid text")
        if s.get("role") in tip_roles:
            if not isinstance(s.get("tip_name"), str) or not s["tip_name"].strip():
                raise ValueError(
                    f"Section {s.get('role')!r} missing 'tip_name'"
                )

    real_words = _count_words(data["hook"], *(s["text"] for s in data["sections"]))
    data["est_words"] = real_words
    if not (config.TARGET_WORDS_MIN * 0.8 <= real_words <= config.TARGET_WORDS_MAX * 1.2):
        logger.warning(
            "Script word count %d is outside target %d-%d (~%.1f min)",
            real_words, config.TARGET_WORDS_MIN, config.TARGET_WORDS_MAX,
            real_words / config.NARRATION_WPM,
        )


def _format_angle(angle: dict) -> str:
    parts = [
        "Write the script for this topic:",
        f"  title:         {angle.get('title', '').strip()}",
        f"  hook_premise:  {angle.get('hook_premise', '').strip()}",
        f"  topic:         {angle.get('topic', '').strip()}",
    ]
    notes = angle.get("research_notes", "").strip()
    if notes:
        parts.append(
            "\nResearch notes (ONLY source of factual claims — do not invent numbers):\n"
            + notes
        )
    else:
        parts.append(
            "\nNo research notes supplied. Keep all claims qualitative. "
            "Do NOT invent statistics or cite studies."
        )
    return "\n".join(parts)


def generate_script(angle: dict) -> dict:
    exemplar = _load_exemplar()
    user_body = _format_angle(angle)
    if exemplar:
        user_body = (
            "GOLD-STANDARD EXEMPLAR (study tone/rhythm; do NOT reuse its topic):\n"
            "<<<EXEMPLAR\n" + exemplar + "\nEXEMPLAR>>>\n\n" + user_body
        )

    logger.info("Generating script via %s | title=%r", config.SCRIPT_MODEL, angle.get("title"))
    client = _client()
    response = client.messages.create(
        model=config.SCRIPT_MODEL,
        max_tokens=config.SCRIPT_MAX_TOKENS,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_body}],
    )
    raw = next((b.text for b in response.content if b.type == "text"), "")
    parsed = json.loads(_strip_to_json(raw))
    _validate(parsed)
    logger.info(
        "Script OK | title=%r | words=%d (~%.1f min)",
        parsed["title"], parsed["est_words"],
        parsed["est_words"] / config.NARRATION_WPM,
    )
    return parsed


def generate_script_with_retry(
    angle: dict,
    attempts: int = config.SCRIPT_RETRY_ATTEMPTS,
) -> dict:
    last_exc: "ValueError | None" = None
    for attempt in range(1, attempts + 1):
        try:
            return generate_script(angle)
        except ValueError as exc:
            last_exc = exc
            logger.warning(
                "generate_script attempt %d/%d failed: %s", attempt, attempts, exc
            )
    assert last_exc is not None
    raise last_exc


# --- Sample angle for smoke testing ----------------------------------------
ANGLE_01 = {
    "title": "6 Study Tricks That Actually Work",
    "hook_premise": "Most students study for hours and still forget everything by exam day",
    "topic": "efficient studying techniques that maximize retention without more time",
    "research_notes": (
        "Parkinson's Law: work expands to fill the time allotted — "
        "setting a short deadline forces focus. "
        "Feynman Technique: explaining a concept simply reveals gaps in understanding. "
        "Active recall beats re-reading: testing yourself forces retrieval, which strengthens memory. "
        "Pomodoro: 25-30 min focused sprint + 5 min break prevents burnout and maintains urgency. "
        "80/20 rule (Pareto): 20% of material typically yields 80% of exam results — "
        "identify it with a practice test first. "
        "Sleep: memory consolidation happens during sleep; all-nighters impair recall the next day. "
        "Exercise increases BDNF which supports memory formation."
    ),
}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    result = generate_script_with_retry(ANGLE_01)
    out_path = os.path.join(config.TMP_DIR, "script_01.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("\n=== Generated script ===")
    for s in result["sections"]:
        label = s.get("tip_name", s["role"])
        print(f"\n[{label.upper()}]\n{s['text']}")
    print(f"\nWords: {result['est_words']} (~{result['est_words']/config.NARRATION_WPM:.1f} min)")
    print(f"Saved -> {out_path}")
