"""Stage 2 — beat_plan: split a structured script into cinematic mood beats.

Input  : a structured script dict from script_gen (SPEC §5.1).
Output : ordered beat list (SPEC §5.2):
         { beats: [{ line, tone, visual_world, query, text, section_role }] }

Each beat covers ~2–4s of narration and defines the visual + text treatment for
that slice. Timestamps are backfilled from TTS in stage 4. The editorial rules
(SPEC §0) are encoded in the system prompt as hard constraints.
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
    "You are the visual editor for Icarus Wings, a self-improvement channel. You take "
    "a written script and break it into a BEAT MAP — an ordered list of mood beats that "
    "define how the video will look and feel.\n\n"
    "You MUST respond with ONLY a single valid JSON object — no markdown, no code "
    "fences, no commentary before or after.\n\n"

    "## THE FIVE EDITING RULES (non-negotiable)\n\n"
    "1. FOOTAGE BY MOOD, NOT NOUN. A beat sets the emotional backdrop for the spoken "
    "line — NOT a literal depiction. FORBIDDEN queries: desk, clock, coffee, rubbing-eyes, "
    "head-in-hands, ticking-clock, battery-icon, brain illustration, person working, or "
    "any other literal-noun match.\n"
    "2. TEXT-AS-A-ROLL. Each beat donates 1–3 keywords animated large over footage. The "
    "footage is the backdrop behind the moving word. Text is a lead layer.\n"
    "3. COHESION FROM GRADE + TYPE. Disparate stock is unified by a dark/cool grade + "
    "grain + one type system. Favor footage that works under this treatment.\n"
    "4. BRANDED SPINE. The first beat of each script section gets a segment-title card. "
    "Full-frame text cards (style='card') land the hook punchline, the turn reveal, "
    "the lever's key move, and the close thesis.\n"
    "5. AESTHETIC QUERIES ONLY. Cinematic-look queries scored on visual mood. "
    "'silhouette walking fog backlit' — YES. 'person at desk focusing' — NO.\n\n"

    "## VISUAL WORLDS (every beat uses exactly one)\n\n"
    + "\n".join(f"- {k} — {v}" for k, v in config.VISUAL_WORLDS.items())
    + "\n\n"

    "## BEAT RULES\n\n"
    "- Break the ENTIRE script (hook + all sections) into ordered beats.\n"
    "- Each beat covers a small chunk of narration: ~5–10 words (~2–4 seconds at "
    f"~{config.NARRATION_WPM} wpm).\n"
    "- Beats must cover ALL narration in order — no words skipped or repeated.\n"
    f"- Target: {config.TARGET_BEATS_MIN}–{config.TARGET_BEATS_MAX} beats total.\n"
    "- Vary visual_world across beats — avoid long runs of the same world.\n\n"

    "## TEXT PLAN\n\n"
    "Three styles:\n"
    "- 'keyword-overlay': 1–3 punchy keywords from the line, animated over footage. "
    "The default text treatment for most beats.\n"
    "- 'card': full-frame text on dark background. Reserved for the strongest lines "
    "(hook punchline, turn reveal, lever payoff, close thesis). ~4–8 cards total.\n"
    "- 'segment-title': branded section title. Exactly ONE per script section, always "
    "on the FIRST beat of that section. Keywords = a short label for the section.\n\n"
    "Set text to null for breathing beats (footage only). "
    "Aim for ~60–80% of beats with text; the rest are breathing beats.\n\n"

    "## TONE\n\n"
    "One emotional label per beat. Prefer: introspective, tense, anxious, resigned, "
    "curious, revelatory, calm, determined, hopeful, resolute, urgent, reflective, "
    "warm, sharp, quiet, triumphant.\n\n"

    "## QUERY GUIDELINES\n\n"
    "- 5–12 word cinematic search phrase for stock footage.\n"
    "- Favor: dark, cool, moody, atmospheric, low-light, shadows, silhouettes, fog.\n"
    "- Avoid: bright, corporate, generic, busy, colorful, cheerful.\n"
    "- Good: 'rain on glass bokeh night moody', 'aerial ocean waves dusk slow cinematic'.\n"
    "- Bad: 'person working at desk', 'clock showing 2pm', 'brain neurons firing'.\n\n"

    "## OUTPUT JSON\n\n"
    "Return exactly:\n"
    '{"beats": [\n'
    '  {"line": "exact words from script",\n'
    '   "tone": "emotional label",\n'
    '   "visual_world": "one of the six worlds",\n'
    '   "query": "cinematic fetch query",\n'
    '   "text": {"keywords": ["word1", "word2"], "style": "keyword-overlay|card|segment-title"} OR null,\n'
    '   "section_role": "hook|false_cause|turn|true_cause|re_hook|lever|close"}\n'
    "]}\n"
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
    word_count = len(re.findall(r"\\b[\\w'-]+\\b", _script_full_text(script)))
    parts.append(f"\nTotal words: ~{word_count}")
    parts.append(f"Target beats: {config.TARGET_BEATS_MIN}–{config.TARGET_BEATS_MAX}")
    return "\n".join(parts)


def _validate(data: dict, script: dict) -> None:
    if "beats" not in data or not isinstance(data["beats"], list):
        raise ValueError("Missing 'beats' array")

    beats = data["beats"]

    if len(beats) < config.TARGET_BEATS_MIN * 0.7:
        raise ValueError(
            f"Too few beats: {len(beats)} (need ~{config.TARGET_BEATS_MIN}+). "
            "Split narration into smaller ~5-10 word chunks.")
    if len(beats) > config.TARGET_BEATS_MAX * 1.3:
        raise ValueError(f"Too many beats: {len(beats)} (max ~{config.TARGET_BEATS_MAX})")

    valid_worlds = set(config.VISUAL_WORLDS.keys())
    valid_styles = {"keyword-overlay", "card", "segment-title"}
    valid_roles = {"hook"} | set(config.SCRIPT_SECTION_ROLES)

    text_count = 0
    card_count = 0
    segment_title_roles = set()

    for i, beat in enumerate(beats):
        for field in ("line", "tone", "visual_world", "query", "section_role"):
            if field not in beat:
                raise ValueError(f"Beat {i}: missing '{field}'")

        if not isinstance(beat["line"], str) or not beat["line"].strip():
            raise ValueError(f"Beat {i}: 'line' must be non-empty")

        if beat["visual_world"] not in valid_worlds:
            raise ValueError(
                f"Beat {i}: visual_world '{beat['visual_world']}' invalid. "
                f"Use: {sorted(valid_worlds)}")

        if not isinstance(beat["query"], str) or len(beat["query"].split()) < 3:
            raise ValueError(f"Beat {i}: query too short: '{beat.get('query')}'")

        if beat["section_role"] not in valid_roles:
            raise ValueError(f"Beat {i}: section_role '{beat['section_role']}' invalid")

        text = beat.get("text")
        if text is not None:
            if not isinstance(text, dict):
                raise ValueError(f"Beat {i}: 'text' must be object or null")
            if "keywords" not in text or "style" not in text:
                raise ValueError(f"Beat {i}: text needs 'keywords' and 'style'")
            kw = text["keywords"]
            if not isinstance(kw, list) or not (1 <= len(kw) <= 3):
                raise ValueError(f"Beat {i}: keywords must be 1-3 items, got {len(kw) if isinstance(kw, list) else type(kw)}")
            if text["style"] not in valid_styles:
                raise ValueError(f"Beat {i}: text style '{text['style']}' invalid")
            text_count += 1
            if text["style"] == "card":
                card_count += 1
            if text["style"] == "segment-title":
                segment_title_roles.add(beat["section_role"])

    # Text density
    density = text_count / len(beats) if beats else 0
    if density < 0.4:
        raise ValueError(
            f"Text density {density:.0%} too low (need ~60-80%). "
            "Most beats should have keyword-overlay text.")

    # Section role ordering
    role_order = ["hook"] + config.SCRIPT_SECTION_ROLES
    seen = []
    for beat in beats:
        r = beat["section_role"]
        if not seen or seen[-1] != r:
            seen.append(r)
    expected = [r for r in role_order if r in seen]
    if seen != expected:
        raise ValueError(f"Section roles out of order: {seen}")

    # Segment title coverage (warn, don't fail)
    expected_sections = set(config.SCRIPT_SECTION_ROLES)
    missing = expected_sections - segment_title_roles
    if missing:
        logger.warning("Missing segment-title for: %s", sorted(missing))

    # Stats
    world_dist = {}
    for b in beats:
        w = b["visual_world"]
        world_dist[w] = world_dist.get(w, 0) + 1
    logger.info(
        "Validation passed | beats=%d | text=%.0f%% | cards=%d | worlds=%s",
        len(beats), density * 100, card_count,
        " ".join(f"{k}:{v}" for k, v in sorted(world_dist.items())))


def generate_beat_plan(script: dict) -> dict:
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
    _validate(parsed, script)
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
    text_ct = sum(1 for b in beats if b.get("text"))
    worlds = {}
    for b in beats:
        worlds[b["visual_world"]] = worlds.get(b["visual_world"], 0) + 1
    print(f"\n=== Beat plan: {len(beats)} beats ===")
    print(f"Text density: {text_ct}/{len(beats)} ({text_ct * 100 // len(beats)}%)")
    print(f"Visual worlds: {json.dumps(worlds, indent=2)}")
    print(f"Saved -> {out_path}")
