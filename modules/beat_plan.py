"""Stage 2 — beat_plan: split a structured script into cinematic mood beats.

Uses the standing instruction (semantic framework): every beat goes through
situation → mood → query. Never literal, always cinematic.

Input  : structured script dict from script_gen.
Output : { beats: [{ line, situation, mood, query, tier, section_role }] }
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


BANNED_QUERY_WORDS = {
    "focus", "willpower", "discipline", "motivation", "biology",
    "rhythm", "alertness", "energy", "productivity", "mindset",
    "habit", "growth", "success", "failure", "struggle",
}

METAPHOR_TABLE = (
    "- Energy draining → candle burning low in dark room\n"
    "- Mental fog → dim window, rain-blurred glass\n"
    "- Clock / time → analogue clock face, long shadows on floor\n"
    "- Internal conflict → two hands gripping a desk edge\n"
    "- Biological force → tide moving in dark water, slow exhale of breath"
)

TARGET_BEATS_MIN = 40
TARGET_BEATS_MAX = 65

SYSTEM_PROMPT = (
    "You are the visual editor for Icarus Wings, a self-improvement channel. You take "
    "a written script and break it into a BEAT MAP — an ordered list of mood beats that "
    "define what footage to fetch for each line.\n\n"
    "You MUST respond with ONLY a single valid JSON object — no markdown, no code "
    "fences, no commentary before or after.\n\n"

    "## THE CORE RULE\n\n"
    "Never read a script line literally. Always ask: what is the HUMAN SCENE underneath "
    "this sentence? A person, a place, a physical moment, a feeling made visible. That "
    "scene is what you fetch. The words are just the path to it.\n\n"

    "## PER-BEAT THREE-STEP PROCESS (mandatory, in order)\n\n"
    "For every beat, run these three steps before writing the query:\n\n"
    "1. SITUATION — translate the line into a physical human moment. Who, where, doing "
    "what, feeling what. No abstractions allowed. Write it in plain language as if "
    "describing a film shot to a cinematographer.\n"
    "2. MOOD — apply the channel's visual identity to EVERY beat without exception: "
    "cool, dark, cinematic, shadow-heavy, low-key lighting. Then add 1–2 specific "
    "atmosphere words relevant to the beat (tense, still, exhausted, isolated, quietly "
    "determined, etc). These travel with every query, always.\n"
    "3. QUERY — combine situation + mood into a concrete 4–7 word fetch string. Must "
    "name a literal, photographable object or scene. If you can't point a camera at it, "
    "rewrite it.\n\n"

    "## HARD-BANNED QUERY WORDS\n\n"
    "These return useless generic stock — if you write one, go one layer deeper into "
    "the physical scene:\n\n"
    "focus, willpower, discipline, motivation, biology, rhythm, alertness, energy, "
    "productivity, mindset, habit, growth, success, failure, struggle\n\n"

    "## TIERS\n\n"
    "Classify every beat:\n"
    "- Tier 1: the line describes a literal, photographable scene.\n"
    "- Tier 2: concrete but needs specific framing or staging.\n"
    "- Tier 3: abstract concept — you MUST resolve it into a cinematic METAPHOR in the "
    "situation layer. Choose a physical object that carries the feeling. Never pass an "
    "abstraction to the query.\n\n"

    "## TIER 3 METAPHOR TARGETS (use these or invent similar)\n\n"
    + METAPHOR_TABLE + "\n\n"

    "## BEAT RULES\n\n"
    "- Break the ENTIRE script (hook + all sections) into ordered beats.\n"
    "- Each beat covers one or a few sentences — a natural visual unit.\n"
    "- Beats must cover ALL narration in order — no words skipped or repeated.\n"
    f"- Target: {TARGET_BEATS_MIN}–{TARGET_BEATS_MAX} beats for a ~800-word script.\n"
    "- Every beat carries a section_role.\n\n"

    "## SECTION ROLES\n\n"
    "Use exactly: hook, false_cause, turn, true_cause, re_hook, lever, close\n\n"

    "## OUTPUT JSON\n\n"
    "Return exactly:\n"
    '{"beats": [\n'
    '  {"line": "exact words from script",\n'
    '   "situation": "plain-language film shot description (2-3 sentences)",\n'
    '   "mood": "cool, dark, cinematic — [specific atmosphere words]",\n'
    '   "query": "concrete 4-7 word fetch query",\n'
    '   "tier": 1,\n'
    '   "section_role": "hook"}\n'
    "]}\n"
)

CALIBRATION_INSTRUCTION = (
    "\n\nCALIBRATION MODE: Generate beats for ONLY the first 3 sentences of the "
    "hook. Stop after 3 beats. This is a calibration check."
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
        parts.append(f"[{section['role'].upper()}]\n{section['text']}\n")
    word_count = len(_script_full_text(script).split())
    parts.append(f"\nTotal words: ~{word_count}")
    parts.append(f"Target beats: {TARGET_BEATS_MIN}–{TARGET_BEATS_MAX}")
    return "\n".join(parts)


def _validate(data: dict, calibration: bool = False) -> None:
    if "beats" not in data or not isinstance(data["beats"], list):
        raise ValueError("Missing 'beats' array")

    beats = data["beats"]

    if calibration:
        if len(beats) < 1 or len(beats) > 5:
            raise ValueError(f"Calibration: expected 1-5 beats, got {len(beats)}")
    else:
        if len(beats) < TARGET_BEATS_MIN * 0.7:
            raise ValueError(
                f"Too few beats: {len(beats)} (need ~{TARGET_BEATS_MIN}+)")
        if len(beats) > TARGET_BEATS_MAX * 1.3:
            raise ValueError(
                f"Too many beats: {len(beats)} (max ~{TARGET_BEATS_MAX})")

    valid_roles = {"hook"} | set(config.SCRIPT_SECTION_ROLES)

    for i, beat in enumerate(beats):
        for field in ("line", "situation", "mood", "query", "tier", "section_role"):
            if field not in beat:
                raise ValueError(f"Beat {i}: missing '{field}'")

        if not isinstance(beat["line"], str) or not beat["line"].strip():
            raise ValueError(f"Beat {i}: 'line' must be non-empty")

        if not isinstance(beat["situation"], str) or len(beat["situation"]) < 20:
            raise ValueError(f"Beat {i}: 'situation' too short — describe the shot")

        mood = beat.get("mood", "")
        if not mood.lower().startswith("cool, dark, cinematic"):
            raise ValueError(
                f"Beat {i}: mood must start with 'cool, dark, cinematic'. "
                f"Got: '{mood[:40]}'")

        query = beat.get("query", "")
        words = query.split()
        if len(words) < 4:
            raise ValueError(
                f"Beat {i}: query too short ({len(words)} words): '{query}'")
        if len(words) > 7:
            raise ValueError(
                f"Beat {i}: query too long ({len(words)} words): '{query}'")

        query_lower = query.lower()
        for banned in BANNED_QUERY_WORDS:
            if banned in query_lower.split():
                raise ValueError(
                    f"Beat {i}: query contains banned word '{banned}': '{query}'")

        if beat["tier"] not in (1, 2, 3):
            raise ValueError(f"Beat {i}: tier must be 1, 2, or 3, got {beat['tier']}")

        if beat["section_role"] not in valid_roles:
            raise ValueError(
                f"Beat {i}: section_role '{beat['section_role']}' invalid")

    if not calibration:
        if len(beats) < 45:
            logger.warning(
                "Beat count %d is below 45 — review pacing on render", len(beats))

        role_order = ["hook"] + config.SCRIPT_SECTION_ROLES
        seen = []
        for beat in beats:
            r = beat["section_role"]
            if not seen or seen[-1] != r:
                seen.append(r)
        expected = [r for r in role_order if r in seen]
        if seen != expected:
            raise ValueError(f"Section roles out of order: {seen}")

    tier_dist = {1: 0, 2: 0, 3: 0}
    for b in beats:
        tier_dist[b["tier"]] = tier_dist.get(b["tier"], 0) + 1
    logger.info(
        "Validation passed | beats=%d | tiers=%s",
        len(beats),
        " ".join(f"t{k}:{v}" for k, v in sorted(tier_dist.items())))


def generate_calibration_beats(script: dict) -> dict:
    """Generate only the first 3 hook beats for human review (calibration gate)."""
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
            logger.warning("beat_plan attempt %d/%d failed: %s",
                           attempt, attempts, exc)
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
    tier_dist = {1: 0, 2: 0, 3: 0}
    for b in beats:
        tier_dist[b["tier"]] = tier_dist.get(b["tier"], 0) + 1
    print(f"\n=== Beat plan: {len(beats)} beats ===")
    print(f"Tiers: {json.dumps(tier_dist)}")
    print(f"Saved -> {out_path}")
