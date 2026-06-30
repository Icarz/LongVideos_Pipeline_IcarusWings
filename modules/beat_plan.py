"""Stage 2 — beat_plan: split a tips script into visual beats.

Each beat is classified as either:
  - illustration : a stick-figure scene described by image_prompt
  - text_card    : text displayed full-screen (title cards, quotes, principles)

Input  : structured script dict from script_gen.
Output : { beats: [{ line, beat_type, image_prompt, card_text, section_role }] }
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


TARGET_BEATS_MIN = config.TARGET_BEATS_MIN
TARGET_BEATS_MAX = config.TARGET_BEATS_MAX

SYSTEM_PROMPT = (
    "You are the visual editor for Icarus Wings, an educational YouTube channel. "
    "You take a tips-format script and break it into a BEAT MAP — an ordered list "
    "of visual beats that define what appears on screen for each line of narration.\n\n"
    "You MUST respond with ONLY a single valid JSON object — no markdown, no code "
    "fences, no commentary before or after.\n\n"

    "## BEAT TYPES\n\n"
    "Every beat is one of two types:\n\n"

    "### text_card\n"
    "Full-screen text on the cream background. Use for:\n"
    "- Tip title announcements ('Tip 1: Trick Your Brain')\n"
    "- Named principles or laws ('Parkinson's Law: Work expands to fill the time you allow')\n"
    "- Punchy one-line quotes or rules\n"
    "- The outro cheat sheet items\n"
    "For text_card beats: write the exact text to display in `card_text`. "
    "Use \\n for line breaks. Keep it short — max 2 lines, max 8 words per line. "
    "`image_prompt` must be null.\n\n"

    "### illustration\n"
    "A minimalist black stick-figure scene on cream background. Use for:\n"
    "- Problem demonstrations ('most students re-read their notes...')\n"
    "- How-to steps ('set a 20-minute timer and start')\n"
    "- Consequences or results\n"
    "- Any narration that describes a person doing something\n"
    "For illustration beats: write a 1-sentence stick-figure scene description in "
    "`image_prompt`. Be specific: who is doing what, what object or prop is visible. "
    "Examples:\n"
    "  - 'stick figure sitting at desk staring at clock looking bored'\n"
    "  - 'stick figure teaching a small child at a whiteboard'\n"
    "  - 'stick figure running up a rising arrow graph'\n"
    "`card_text` must be null.\n\n"

    "## RULES\n\n"
    "- Break the ENTIRE script (hook + all sections) into ordered beats.\n"
    "- Each beat = one natural sentence or phrase from the narration.\n"
    "- Every word of the script must appear in exactly one beat's `line`. No skipping.\n"
    f"- Target: {TARGET_BEATS_MIN}–{TARGET_BEATS_MAX} beats total.\n"
    "- Tip sections: open with a text_card (the tip title), then alternate "
    "illustration and text_card as the content demands.\n"
    "- Outro: each cheat sheet item is its own text_card beat.\n"
    "- `section_role` must match the section the line comes from.\n\n"

    "## OUTPUT JSON\n\n"
    'Return exactly:\n'
    '{"beats": [\n'
    '  {\n'
    '    "line": "exact words from the script narration",\n'
    '    "beat_type": "text_card",\n'
    '    "card_text": "Tip 1:\\nTrick Your Brain",\n'
    '    "image_prompt": null,\n'
    '    "section_role": "tip_1"\n'
    '  },\n'
    '  {\n'
    '    "line": "Most students sit down with no end in mind.",\n'
    '    "beat_type": "illustration",\n'
    '    "card_text": null,\n'
    '    "image_prompt": "stick figure sitting at desk looking overwhelmed, pile of books nearby",\n'
    '    "section_role": "tip_1"\n'
    '  }\n'
    ']}\n'
)

CALIBRATION_INSTRUCTION = (
    "\n\nCALIBRATION MODE: Generate beats for ONLY the hook + intro section. "
    "Stop after those. This is a calibration check."
)


def _client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (check your .env file)")
    return Anthropic(api_key=api_key)


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


def _script_full_text(script: dict) -> str:
    return script["hook"] + " " + " ".join(s["text"] for s in script["sections"])


def _format_script(script: dict) -> str:
    parts = ["SCRIPT TO BEAT-MAP:\n"]
    parts.append(f"[HOOK]\n{script['hook']}\n")
    for section in script["sections"]:
        role = section["role"]
        tip_name = section.get("tip_name", "")
        label = f"{role.upper()} — {tip_name}" if tip_name else role.upper()
        parts.append(f"[{label}]\n{section['text']}\n")
    word_count = len(_script_full_text(script).split())
    parts.append(f"\nTotal words: ~{word_count}")
    parts.append(f"Target beats: {TARGET_BEATS_MIN}–{TARGET_BEATS_MAX}")
    return "\n".join(parts)


def _validate(data: dict, calibration: bool = False) -> None:
    if "beats" not in data or not isinstance(data["beats"], list):
        raise ValueError("Missing 'beats' array")

    beats = data["beats"]

    if calibration:
        if len(beats) < 1 or len(beats) > 10:
            raise ValueError(f"Calibration: expected 1-10 beats, got {len(beats)}")
    else:
        if len(beats) < TARGET_BEATS_MIN * 0.7:
            raise ValueError(f"Too few beats: {len(beats)} (need ~{TARGET_BEATS_MIN}+)")
        if len(beats) > TARGET_BEATS_MAX * 1.3:
            raise ValueError(f"Too many beats: {len(beats)} (max ~{TARGET_BEATS_MAX})")

    valid_roles = {"hook"} | set(config.SCRIPT_SECTION_ROLES)

    for i, beat in enumerate(beats):
        for field in ("line", "beat_type", "image_prompt", "card_text", "section_role"):
            if field not in beat:
                raise ValueError(f"Beat {i}: missing '{field}'")

        if not isinstance(beat["line"], str) or not beat["line"].strip():
            raise ValueError(f"Beat {i}: 'line' must be non-empty")

        bt = beat["beat_type"]
        if bt not in config.BEAT_TYPES:
            raise ValueError(
                f"Beat {i}: beat_type '{bt}' invalid. Must be one of {config.BEAT_TYPES}"
            )

        if bt == "illustration":
            if not isinstance(beat.get("image_prompt"), str) or not beat["image_prompt"].strip():
                raise ValueError(
                    f"Beat {i}: illustration beat requires a non-empty 'image_prompt'"
                )
            if beat.get("card_text") is not None:
                raise ValueError(
                    f"Beat {i}: illustration beat must have card_text=null"
                )
        else:  # text_card
            if not isinstance(beat.get("card_text"), str) or not beat["card_text"].strip():
                raise ValueError(
                    f"Beat {i}: text_card beat requires a non-empty 'card_text'"
                )
            if beat.get("image_prompt") is not None:
                raise ValueError(
                    f"Beat {i}: text_card beat must have image_prompt=null"
                )

        if beat["section_role"] not in valid_roles:
            raise ValueError(
                f"Beat {i}: section_role '{beat['section_role']}' invalid"
            )

    if not calibration:
        type_dist = {"illustration": 0, "text_card": 0}
        for b in beats:
            type_dist[b["beat_type"]] = type_dist.get(b["beat_type"], 0) + 1
        logger.info(
            "Validation passed | beats=%d | types=%s",
            len(beats),
            " ".join(f"{k}:{v}" for k, v in type_dist.items()),
        )


def generate_calibration_beats(script: dict) -> dict:
    """Generate beats for the hook + intro only (calibration gate)."""
    user_body = _format_script(script) + CALIBRATION_INSTRUCTION

    logger.info("Generating calibration beats via %s", config.BEAT_MODEL)
    client = _client()
    response = client.messages.create(
        model=config.BEAT_MODEL,
        max_tokens=2000,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_body}],
    )
    raw = next((b.text for b in response.content if b.type == "text"), "")
    parsed = json.loads(_strip_to_json(raw))
    _validate(parsed, calibration=True)
    logger.info("Calibration OK | beats=%d", len(parsed["beats"]))
    return parsed


def generate_beat_plan(script: dict) -> dict:
    """Generate the full beat plan for a script."""
    user_body = _format_script(script)

    logger.info("Generating beat plan via %s | title=%r",
                config.BEAT_MODEL, script.get("title"))
    client = _client()
    response = client.messages.create(
        model=config.BEAT_MODEL,
        max_tokens=config.BEAT_MAX_TOKENS,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_body}],
    )
    raw = next((b.text for b in response.content if b.type == "text"), "")
    parsed = json.loads(_strip_to_json(raw))
    _validate(parsed)
    logger.info("Beat plan OK | title=%r | beats=%d",
                script.get("title"), len(parsed["beats"]))
    return parsed


def generate_beat_plan_with_retry(
    script: dict,
    attempts: int = config.BEAT_RETRY_ATTEMPTS,
) -> dict:
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return generate_beat_plan(script)
        except (ValueError, json.JSONDecodeError) as exc:
            last_exc = exc
            logger.warning("beat_plan attempt %d/%d failed: %s", attempt, attempts, exc)
    raise last_exc


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    script_path = os.path.join(config.TMP_DIR, "script_01.json")
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    result = generate_beat_plan_with_retry(script)
    out_path = os.path.join(config.TMP_DIR, "beats_01.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    beats = result["beats"]
    type_dist = {"illustration": 0, "text_card": 0}
    for b in beats:
        type_dist[b["beat_type"]] = type_dist.get(b["beat_type"], 0) + 1
    print(f"\n=== Beat plan: {len(beats)} beats ===")
    print(f"Types: {json.dumps(type_dist)}")
    print(f"\nFirst 6 beats:")
    for b in beats[:6]:
        content = b.get("card_text") or b.get("image_prompt") or ""
        print(f"  [{b['beat_type']:12s}] [{b['section_role']:8s}] {content[:60]}")
    print(f"\nSaved -> {out_path}")
