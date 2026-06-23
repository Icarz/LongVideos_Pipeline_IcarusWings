"""Stage 3 — visual_direction: turn beats + script into a narrative scene plan.

This is the creative bridge between beat_plan and background fetch. It reads
the FULL script, understands the emotional arc, and produces a SCENE PLAN
that the fetcher follows — not isolated per-beat queries, but grouped scenes
with narrative-aware search strategies, character continuity, light arc
compliance, and bookend/motif tracking.

Input  : structured script (§5.1) + beat list (§5.2).
Output : scene plan:
         { scenes: [{ beats, brief, queries, subject_type, light_profile,
                      motif_tag? }],
           bookends: [{ open_scene, close_scene, note }],
           character: { ... },
           light_arc_applied: { ... } }

Every script shares the same section structure (hook → false_cause → turn →
true_cause → re_hook → lever → close), so the LIGHT_ARC from config applies
universally. The per-script work is: reading the specific content, clustering
beats into scenes, writing scene briefs, and generating narrative-aware queries.
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
    "You are the VISUAL DIRECTOR for Icarus Wings, a self-improvement channel. "
    "You take a script and its beat map and produce a SCENE PLAN — the blueprint "
    "that tells the footage fetcher exactly what to search for.\n\n"
    "You MUST respond with ONLY a single valid JSON object — no markdown, no code "
    "fences, no commentary.\n\n"

    "## YOUR JOB\n\n"
    "You are NOT searching for stock footage. You are DESIGNING the visual narrative "
    "of a film. Each scene is a deliberate editorial choice that serves the script's "
    "emotional arc. You think in shots, not search terms.\n\n"

    "## UNIVERSAL RULES (apply to EVERY video)\n\n"

    "### 1. CHARACTER\n"
    f"- {config.CHARACTER_RULES['treatment']}\n"
    f"- Gender: {config.CHARACTER_RULES['gender']} only\n"
    f"- FORBIDDEN subjects: {', '.join(config.CHARACTER_RULES['forbidden'])}\n"
    "- When human figures appear, they must be male — shot anonymously (back, "
    "silhouette, hands, partial face, distance) so it feels universal\n"
    "- Alternate between human-figure scenes and abstract/nature breathing scenes\n\n"

    "### 2. LIGHT ARC\n"
    "Every video follows this emotional-to-visual mapping. The light PROGRESSES "
    "through the video — it is NOT the same mood throughout:\n\n"
    + "\n".join(
        f"- **{role}**: mood={prof['mood']} | light={prof['light']} | "
        f"color={prof['color']} | energy={prof['energy']}"
        for role, prof in config.LIGHT_ARC.items()
    )
    + "\n\n"

    "### 3. SCENE CLUSTERING\n"
    f"- Group consecutive beats into SCENES of {config.SCENE_BEATS_MIN}-"
    f"{config.SCENE_BEATS_MAX} beats each\n"
    "- Beats within a scene share a visual context — the fetcher will try to use "
    "segments of the SAME clip across beats in a scene\n"
    "- A scene = one continuous visual idea (e.g., 'man at window, afternoon light')\n"
    "- Aim for 15-25 scenes per video (not 80+ individual queries)\n"
    "- Each scene transition should feel motivated by the script's emotional shift\n\n"

    "### 4. QUERY DESIGN\n"
    "Each scene gets 2-3 search queries. These are NOT mood-adjective soup. They are "
    "specific cinematic setups:\n"
    "- ALWAYS include: subject (or 'no person'), framing (wide/close/medium), "
    "light direction, color temperature\n"
    "- When human: ALWAYS include 'man' or 'male'\n"
    f"- FORBIDDEN terms in queries: {', '.join(config.QUERY_RULES['forbidden_terms'])}\n"
    "- GOOD: 'man silhouette standing window backlit afternoon dim side angle'\n"
    "- BAD: 'moody cinematic shadow dark atmospheric footage'\n"
    "- Queries should be concrete enough that a stock search returns the RIGHT clip, "
    "not just any dark clip\n\n"

    "### 5. MOTIFS & BOOKENDS\n"
    "- Identify 1-3 visual motifs that should RECUR across the video\n"
    "- The HOOK and CLOSE should bookend: same visual setup, different meaning\n"
    "- Tag scenes that are part of a motif pair so the fetcher can find matching clips\n"
    "- Example: if the hook shows a man at a window in defeat, the close shows the "
    "same framing but he's upright and calm\n\n"

    "### 6. SUBJECT TYPES\n"
    "Every scene has one subject_type:\n"
    "- 'male_silhouette' — backlit/shadowed male figure\n"
    "- 'male_closeup' — hands, partial face, breath, intimate male detail\n"
    "- 'male_landscape' — small male figure in vast environment\n"
    "- 'male_action' — man doing something (walking, sitting, standing)\n"
    "- 'nature' — landscape, sky, water, fog, no human\n"
    "- 'abstract' — texture, ink, smoke, particles, light, no human\n"
    "- 'timelapse' — time passing, light moving, sky cycling\n\n"

    "## OUTPUT FORMAT\n\n"
    "```json\n"
    "{\n"
    '  "scenes": [\n'
    "    {\n"
    '      "id": 0,\n'
    '      "beats": [0, 1, 2, 3],\n'
    '      "section_role": "hook",\n'
    '      "brief": "A man at a window, late afternoon. Shot from behind. '
    "The weight of the 2PM wall — he's still, not working, just existing "
    'in the heaviness.",\n'
    '      "subject_type": "male_silhouette",\n'
    '      "light_profile": "low warm dying to cold, backlit, dim room",\n'
    '      "queries": [\n'
    '        "man standing window afternoon dim backlit silhouette from behind",\n'
    '        "male figure window low light shadow late afternoon room"\n'
    "      ],\n"
    '      "motif_tag": "window_bookend"\n'
    "    }\n"
    "  ],\n"
    '  "bookends": [\n'
    '    {"open_scene": 0, "close_scene": 18, '
    '"note": "same window setup — hook: defeated posture, close: upright and resolved"}\n'
    "  ]\n"
    "}\n"
    "```\n\n"
    "IMPORTANT:\n"
    "- Every beat index must appear in exactly one scene\n"
    "- Scenes must be in beat order (no jumping around)\n"
    "- scene.beats is an array of beat indices [0, 1, 2, ...]\n"
    "- At least one bookend pair (hook ↔ close)\n"
    "- brief should be 1-3 sentences describing the SHOT, not the script content\n"
    "- light_profile should be specific to this scene, consistent with the section's "
    "light arc entry\n"
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


def _format_input(script: dict, beats: list[dict]) -> str:
    """Format the script and beats for the LLM prompt."""
    parts = ["## FULL SCRIPT\n"]
    parts.append(f"Title: {script['title']}\n")
    parts.append(f"[HOOK]\n{script['hook']}\n")
    for section in script["sections"]:
        parts.append(f"[{section['role'].upper()}]\n{section['text']}\n")

    parts.append(f"\n## BEAT MAP ({len(beats)} beats)\n")
    for i, beat in enumerate(beats):
        text_info = ""
        if beat.get("text"):
            text_info = f" | text={beat['text']['style']}:{beat['text']['keywords']}"
        parts.append(
            f"  [{i:3d}] {beat['section_role']:12s} | {beat['visual_world']:20s} | "
            f"{beat['tone']:14s} | \"{beat['line']}\"{text_info}"
        )

    parts.append(f"\n## INSTRUCTIONS")
    parts.append(f"- Total beats: {len(beats)}")
    parts.append(f"- Target scenes: 15-25")
    parts.append(f"- Group into scenes of {config.SCENE_BEATS_MIN}-{config.SCENE_BEATS_MAX} beats")
    parts.append(f"- Follow the LIGHT ARC for each section")
    parts.append(f"- All human subjects must be MALE, shot anonymously")
    parts.append(f"- Create at least one bookend pair (hook ↔ close)")
    parts.append(f"- Read the script deeply — the scenes should serve THIS story")

    return "\n".join(parts)


def _validate(data: dict, total_beats: int) -> None:
    """Validate the scene plan structure and coverage."""
    if "scenes" not in data or not isinstance(data["scenes"], list):
        raise ValueError("Missing 'scenes' array")

    scenes = data["scenes"]
    if len(scenes) < 10:
        raise ValueError(f"Too few scenes: {len(scenes)} (need 15-25)")
    if len(scenes) > 35:
        raise ValueError(f"Too many scenes: {len(scenes)} (need 15-25)")

    all_beats = set()
    valid_subjects = {
        "male_silhouette", "male_closeup", "male_landscape",
        "male_action", "nature", "abstract", "timelapse",
    }
    valid_roles = {"hook"} | set(config.SCRIPT_SECTION_ROLES)
    prev_max_beat = -1

    for i, scene in enumerate(scenes):
        for field in ("beats", "brief", "subject_type", "light_profile", "queries"):
            if field not in scene:
                raise ValueError(f"Scene {i}: missing '{field}'")

        if not isinstance(scene["beats"], list) or not scene["beats"]:
            raise ValueError(f"Scene {i}: 'beats' must be a non-empty array")

        beat_indices = scene["beats"]
        if beat_indices[0] <= prev_max_beat:
            raise ValueError(
                f"Scene {i}: beats not in order (starts at {beat_indices[0]}, "
                f"previous scene ended at {prev_max_beat})")
        prev_max_beat = beat_indices[-1]

        for b in beat_indices:
            if b in all_beats:
                raise ValueError(f"Scene {i}: beat {b} assigned to multiple scenes")
            all_beats.add(b)

        if scene["subject_type"] not in valid_subjects:
            raise ValueError(
                f"Scene {i}: subject_type '{scene['subject_type']}' invalid. "
                f"Use: {sorted(valid_subjects)}")

        if not isinstance(scene["queries"], list) or len(scene["queries"]) < 1:
            raise ValueError(f"Scene {i}: need at least 1 query")

        for q in scene["queries"]:
            q_lower = q.lower()
            for forbidden in config.QUERY_RULES["forbidden_terms"]:
                if forbidden in q_lower:
                    raise ValueError(
                        f"Scene {i}: query contains forbidden term '{forbidden}': {q}")

        if not isinstance(scene["brief"], str) or len(scene["brief"]) < 20:
            raise ValueError(f"Scene {i}: brief too short")

        if scene.get("section_role") and scene["section_role"] not in valid_roles:
            raise ValueError(f"Scene {i}: invalid section_role '{scene['section_role']}'")

    expected_beats = set(range(total_beats))
    missing = expected_beats - all_beats
    if missing:
        raise ValueError(f"Beats not covered by any scene: {sorted(missing)}")
    extra = all_beats - expected_beats
    if extra:
        raise ValueError(f"Scene references non-existent beats: {sorted(extra)}")

    if "bookends" not in data or not isinstance(data["bookends"], list):
        raise ValueError("Missing 'bookends' array")
    if len(data["bookends"]) < 1:
        raise ValueError("Need at least one bookend pair (hook ↔ close)")

    # Stats
    subject_dist = {}
    for s in scenes:
        st = s["subject_type"]
        subject_dist[st] = subject_dist.get(st, 0) + 1
    role_dist = {}
    for s in scenes:
        r = s.get("section_role", "unknown")
        role_dist[r] = role_dist.get(r, 0) + 1

    logger.info(
        "Validation passed | scenes=%d | bookends=%d | subjects=%s | roles=%s",
        len(scenes), len(data["bookends"]),
        " ".join(f"{k}:{v}" for k, v in sorted(subject_dist.items())),
        " ".join(f"{k}:{v}" for k, v in sorted(role_dist.items())),
    )


def generate_visual_direction(script: dict, beats: list[dict]) -> dict:
    """Generate a scene plan from a script and its beat map."""
    user_body = _format_input(script, beats)

    logger.info(
        "Generating visual direction via %s | title=%r | beats=%d",
        config.VISUAL_DIRECTION_MODEL, script.get("title"), len(beats),
    )

    client = _client()
    response = client.messages.create(
        model=config.VISUAL_DIRECTION_MODEL,
        max_tokens=config.VISUAL_DIRECTION_MAX_TOKENS,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_body}],
    )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    parsed = json.loads(_strip_to_json(raw))
    _validate(parsed, len(beats))

    logger.info(
        "Visual direction OK | title=%r | scenes=%d | bookends=%d",
        script.get("title"), len(parsed["scenes"]), len(parsed["bookends"]),
    )
    return parsed


def generate_visual_direction_with_retry(
    script: dict,
    beats: list[dict],
    attempts: int = config.VISUAL_DIRECTION_RETRY_ATTEMPTS,
) -> dict:
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return generate_visual_direction(script, beats)
        except (ValueError, json.JSONDecodeError) as exc:
            last_exc = exc
            logger.warning(
                "visual_direction attempt %d/%d failed: %s",
                attempt, attempts, exc,
            )
    raise last_exc


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    script_path = os.path.join(config.TMP_DIR, "script_01.json")
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    beats_path = os.path.join(config.TMP_DIR, "beats_01.json")
    with open(beats_path, "r", encoding="utf-8") as f:
        beats_data = json.load(f)

    result = generate_visual_direction_with_retry(script, beats_data["beats"])

    out_path = os.path.join(config.TMP_DIR, "visual_direction_01.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    scenes = result["scenes"]
    subjects = {}
    for s in scenes:
        subjects[s["subject_type"]] = subjects.get(s["subject_type"], 0) + 1

    print(f"\n=== Visual Direction: {len(scenes)} scenes ===")
    print(f"Bookends: {len(result['bookends'])}")
    print(f"Subject types: {json.dumps(subjects, indent=2)}")
    print(f"\nScene breakdown:")
    for s in scenes:
        print(f"  Scene {s.get('id', '?'):2} | beats {s['beats'][0]:2d}-{s['beats'][-1]:2d} "
              f"| {s['section_role']:12s} | {s['subject_type']:18s} | {s['brief'][:60]}...")
    print(f"\nSaved -> {out_path}")
