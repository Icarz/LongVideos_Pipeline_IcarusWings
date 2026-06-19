"""Stage 1 — script_gen: turn an angle skeleton into a structured script.

Input  : an angle dict {title, false_cause, true_cause, lever, confidence,
         verify_note?} (from videos-transcriptions/20-video-angles.md) plus
         optional research_notes.
Output : a structured script object (see SPEC 5.1):
         {title, hook, sections:[{role,text}], keep_line, mechanism_confidence,
          est_words}

This is the hardest stage and the creative core. We embed the hand-authored
gold-standard script (config.EXEMPLAR_SCRIPT_PATH) as a few-shot exemplar so the
model matches its shape, loop discipline, and tone. Output is validated and
retried in-code — extraction is NOT deterministic, so we recover where safe and
re-ask only on structural failure; we never silently ship a malformed script.
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
    "You are the lead scriptwriter for Icarus Wings, a self-improvement channel. "
    "You write authored ~5-minute narrated video scripts (NOT podcast clips, NOT "
    "listicles). You take an ANGLE SKELETON and write a complete, voice-ready "
    "script.\n\n"
    "You MUST respond with ONLY a single valid JSON object and nothing else — no "
    "markdown, no code fences, no commentary before or after.\n\n"
    "BRAND MISSION — every script must leave the viewer with INSIGHT + AGENCY, "
    "not just a diagnosed problem. The channel reframes a thing the viewer blames "
    "themselves for (a FALSE CAUSE), reveals the real mechanism (the TRUE CAUSE), "
    "and hands them a concrete lever. The viewer should feel: 'I was fighting the "
    "wrong enemy — and now I know what to actually do.'\n\n"
    "NON-NEGOTIABLE STRUCTURE — the script's `sections` array MUST contain exactly "
    "these six roles, in this order, each opening the next loop (no clean exit "
    "mid-script; every payoff raises the next question):\n"
    "  1. false_cause — the story the viewer tells themselves (the self-blame). "
    "Knock down the obvious wrong explanation(s).\n"
    "  2. turn — open the loop: introduce the strange fact / contradiction that "
    "breaks the false cause. End on a question ('So what is it?').\n"
    "  3. true_cause — payoff #1: reveal the real mechanism, concretely. This is "
    "the proof. Then re-open: if it's built-in, that sounds like bad news...\n"
    "  4. re_hook — flip the apparent dead-end into the reason there's a strategy. "
    "Open the final loop ('here's how').\n"
    "  5. lever — payoff #2, the actionable part: concrete, specific moves the "
    "viewer can do. This is where agency lives.\n"
    "  6. close — land the thesis. Return to the opening self-blame and dissolve "
    "it. End on a single resonant line.\n\n"
    "Separately, `hook` is the COLD OPEN (0-20s): a vivid second-person scene that "
    "names the viewer's experience, then a sharp turn that says 'you're wrong "
    "about why.' Under ~120 words. It is NOT one of the six sections.\n\n"
    "MECHANISM INTEGRITY IS NON-NEGOTIABLE — this is the worst failure mode for "
    "this channel:\n"
    "  - You do NOT invent statistics, study details, or numbers. Factual claims "
    "come ONLY from the supplied research notes (if any). Unverifiable claims are "
    "cut or hedged — never asserted as fact.\n"
    "  - If `mechanism_confidence` is 'check' or 'partial', the script must present "
    "the uncertainty HONESTLY (e.g. 'the evidence here is contested', 'this is one "
    "theory, but...'). A confidently-wrong mechanism is unacceptable. Honesty about "
    "uncertainty is itself on-brand.\n"
    "  - The single proof sentence — the one fact the whole reframe rests on — is "
    "the `keep_line`. It MUST appear verbatim somewhere in the script text (usually "
    "in the `turn` or `true_cause`). Without it the reframe is an assertion, not a "
    "demonstration.\n\n"
    "VOICE — calm, low, measured, conversational-authoritative. A late-night "
    "documentary narrator telling a quiet truth, NOT hype or a pep talk. Short "
    "punchy sentences mixed with longer ones. Direct second person ('you'). No "
    "filler, no 'in this video', no 'let's dive in'. Target 700-900 words total "
    "across hook + all sections (~5 min at ~150 wpm).\n\n"
    "The JSON object must have EXACTLY these keys:\n"
    '  "title"                : string — the working/video title.\n'
    '  "hook"                 : string — the cold open (see above).\n'
    '  "sections"             : array of EXACTLY 6 objects {"role": <one of '
    "false_cause|turn|true_cause|re_hook|lever|close, in that order>, \"text\": "
    "<the spoken prose for that section>}.\n"
    '  "keep_line"            : string — the one proof sentence that must never be '
    "cut; MUST appear verbatim inside one of the sections' text.\n"
    '  "mechanism_confidence" : string — one of "solid" | "partial" | "check". '
    "Carry over the angle's confidence tag; downgrade if the notes don't support "
    "the claim.\n"
    '  "est_words"            : integer — your estimate of total spoken word count '
    "(hook + all sections).\n\n"
    "Study the GOLD-STANDARD EXEMPLAR below. Match its structure, its loop "
    "discipline (each payoff opens the next question), its restraint, and its "
    "honesty. Do NOT copy its topic — write for the NEW angle you are given."
)


def _client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (check your .env file)")
    return Anthropic(api_key=api_key)


def _load_exemplar() -> str:
    """Load the hand-authored gold-standard script as a few-shot exemplar."""
    try:
        with open(config.EXEMPLAR_SCRIPT_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        logger.warning("Could not load exemplar script: %s", exc)
        return ""


def _strip_to_json(text: str) -> str:
    """Isolate the first complete JSON object, tolerating fences / trailing data."""
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
    """Raise ValueError if ``data`` doesn't match the structured-script schema.

    Recover-in-code where safe (recompute est_words from the real text); raise on
    structural failures so the retry wrapper re-asks.
    """
    required = {
        "title": str,
        "hook": str,
        "sections": list,
        "keep_line": str,
        "mechanism_confidence": str,
        "est_words": (int, float),
    }
    for key, expected in required.items():
        if key not in data:
            raise ValueError(f"Missing required key: {key!r}")
        if not isinstance(data[key], expected):
            raise ValueError(
                f"Key {key!r} wrong type: expected {expected}, got {type(data[key]).__name__}"
            )

    if not data["hook"].strip():
        raise ValueError("'hook' must be a non-empty cold open")

    roles = [s.get("role") for s in data["sections"] if isinstance(s, dict)]
    if roles != config.SCRIPT_SECTION_ROLES:
        raise ValueError(
            f"'sections' roles must be exactly {config.SCRIPT_SECTION_ROLES} in order; "
            f"got {roles}"
        )
    for s in data["sections"]:
        if not isinstance(s.get("text"), str) or not s["text"].strip():
            raise ValueError(f"Section {s.get('role')!r} has empty/invalid text")

    if data["mechanism_confidence"] not in config.MECHANISM_CONFIDENCE_VALUES:
        raise ValueError(
            f"'mechanism_confidence' must be one of {config.MECHANISM_CONFIDENCE_VALUES}; "
            f"got {data['mechanism_confidence']!r}"
        )

    # Mechanism integrity: the proof sentence must actually be in the script.
    full_text = data["hook"] + "\n" + "\n".join(s["text"] for s in data["sections"])
    keep = data["keep_line"].strip()
    if not keep:
        raise ValueError("'keep_line' must be a non-empty proof sentence")
    if _normalize(keep) not in _normalize(full_text):
        raise ValueError(
            "'keep_line' does not appear verbatim in the script text — the reframe "
            f"would be unproven. keep_line={keep!r}"
        )

    # Recompute est_words from the real text (don't trust the model's count).
    real_words = _count_words(data["hook"], *(s["text"] for s in data["sections"]))
    data["est_words"] = real_words
    if not (config.TARGET_WORDS_MIN * 0.8 <= real_words <= config.TARGET_WORDS_MAX * 1.2):
        # Out of band is a soft signal, not fatal — log loudly, keep the script.
        logger.warning(
            "Script word count %d is outside target ~%d-%d (will affect runtime ~%.1f min)",
            real_words, config.TARGET_WORDS_MIN, config.TARGET_WORDS_MAX,
            real_words / config.NARRATION_WPM,
        )


def _normalize(s: str) -> str:
    """Loose match: collapse whitespace + smart-quote variants for keep_line check."""
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-").replace("…", "...")
    return re.sub(r"\s+", " ", s).strip().lower()


def _format_angle(angle: dict, research_notes: str = "") -> str:
    parts = [
        "Write the script for this NEW angle:",
        f"  title:        {angle.get('title', '').strip()}",
        f"  false_cause:  {angle.get('false_cause', '').strip()}",
        f"  true_cause:   {angle.get('true_cause', '').strip()}",
        f"  lever:        {angle.get('lever', '').strip()}",
        f"  confidence:   {angle.get('confidence', 'partial').strip()}",
    ]
    if angle.get("verify_note"):
        parts.append(f"  verify_note:  {angle['verify_note'].strip()}")
    if research_notes.strip():
        parts.append("\nResearch notes (the ONLY source of factual claims/numbers):\n" + research_notes.strip())
    else:
        parts.append(
            "\nNo research notes supplied: do NOT invent statistics or study details. "
            "Keep claims qualitative and hedge any contested mechanism."
        )
    return "\n".join(parts)


def generate_script(angle: dict, research_notes: str = "") -> dict:
    """Generate one structured script from an angle skeleton. Validates before return."""
    exemplar = _load_exemplar()
    user_body = _format_angle(angle, research_notes)
    if exemplar:
        user_body = (
            "GOLD-STANDARD EXEMPLAR (study its structure/tone; do NOT reuse its topic):\n"
            "<<<EXEMPLAR\n" + exemplar + "\nEXEMPLAR\n\n" + user_body
        )

    logger.info("Generating script via %s | angle=%r", config.SCRIPT_MODEL, angle.get("title"))
    client = _client()
    response = client.messages.create(
        model=config.SCRIPT_MODEL,
        max_tokens=config.SCRIPT_MAX_TOKENS,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_body}],
    )
    raw = next((b.text for b in response.content if b.type == "text"), "")
    parsed = json.loads(_strip_to_json(raw))
    _validate(parsed)
    logger.info(
        "Script OK | title=%r | words=%d (~%.1f min) | confidence=%s",
        parsed["title"], parsed["est_words"],
        parsed["est_words"] / config.NARRATION_WPM, parsed["mechanism_confidence"],
    )
    return parsed


def generate_script_with_retry(angle: dict, research_notes: str = "",
                               attempts: int = config.SCRIPT_RETRY_ATTEMPTS) -> dict:
    """Retry generate_script on schema/parse failure (non-deterministic output).

    Only ValueError (schema/parse) is retried; transport/API errors propagate.
    """
    last_exc: "ValueError | None" = None
    for attempt in range(1, attempts + 1):
        try:
            return generate_script(angle, research_notes)
        except ValueError as exc:
            last_exc = exc
            logger.warning("generate_script attempt %d/%d failed: %s", attempt, attempts, exc)
    assert last_exc is not None
    raise last_exc


# --- Angle skeleton #1 (from videos-transcriptions/20-video-angles.md) -----
# Frozen here as the Stage-1 smoke input so the harness needs no markdown parser yet.
ANGLE_01 = {
    "title": "Why your focus dies at 2pm — and it isn't willpower",
    "false_cause": "I'm lazy / undisciplined in the afternoon (or it's my lunch / a food coma).",
    "true_cause": "The post-lunch dip is primarily a circadian rhythm — a built-in afternoon "
                  "trough in alertness — not a character flaw and not caused by the meal. It "
                  "happens even to people who ate no lunch and didn't know the time.",
    "lever": "Time your hardest, most focus-heavy work to your late-morning peak; reserve the "
             "afternoon dip for low-stakes tasks; blunt the dip with a short walk and real light.",
    "confidence": "solid",
    "verify_note": "The circadian dip is real and well-supported; the 'glucose crash' framing "
                   "specifically is weaker — do not lean on it. The keep-line proof is that the "
                   "dip occurs in subjects who ate no lunch and didn't know the time of day.",
}

# Research grounding for angle #1 — the ONLY source of factual claims for this run.
RESEARCH_01 = (
    "The post-lunch dip in alertness is primarily CIRCADIAN: a built-in early-afternoon trough "
    "in the body's ~24-hour alertness rhythm, governed by the internal clock alongside body "
    "temperature and hormones. PROOF (this is the keep-line): the dip still occurs in controlled "
    "studies where subjects ate no lunch at all and did not know the time of day — so it is not "
    "caused by the meal and not a willpower failure. A heavy/high-carb lunch can EXACERBATE it but "
    "does not CAUSE it. Do NOT assert the 'glucose crash' mechanism as the cause — that specific "
    "framing is weaker than it sounds. Levers that genuinely help: schedule hardest focus work in "
    "the late-morning peak; reserve the dip for low-stakes tasks; a short walk and real (ideally "
    "outdoor) light are direct signals to the circadian clock; a lighter lunch avoids amplifying it."
)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    result = generate_script_with_retry(ANGLE_01, RESEARCH_01)
    out_path = os.path.join(config.TMP_DIR, "script_01.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("\n=== Generated script ===")
    print(json.dumps(result, indent=2, ensure_ascii=False).encode("ascii", "replace").decode("ascii"))
    print(f"\nWords: {result['est_words']} (~{result['est_words']/config.NARRATION_WPM:.1f} min) | "
          f"confidence: {result['mechanism_confidence']}")
    print(f"Saved -> {out_path}")
