"""Single source of truth for the long-form narrated-video pipeline.

Every module imports tuning constants from here rather than hardcoding. Change
behavior here, not in module bodies. Windows-first, Python 3.12.
"""

import os

# --- Paths -----------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "tmp")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
LOG_FILE = os.path.join(LOGS_DIR, "pipeline.log")

for _d in (TMP_DIR, LOGS_DIR, OUTPUT_DIR):
    os.makedirs(_d, exist_ok=True)

# The hand-authored gold-standard exemplar that script_gen must match in shape,
# discipline, and tone. Embedded as a few-shot example in the script prompt.
EXEMPLAR_SCRIPT_PATH = os.path.join(SCRIPTS_DIR, "script-01-2pm-focus.txt")

# --- Video frame -----------------------------------------------------------
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
VIDEO_BG_COLOR = (8, 10, 14)  # near-black, cool — matches the moody palette

# --- Script (stage 1) ------------------------------------------------------
SCRIPT_MODEL = "claude-opus-4-8"
SCRIPT_MAX_TOKENS = 8000
SCRIPT_RETRY_ATTEMPTS = 3
TARGET_WORDS_MIN = 700
TARGET_WORDS_MAX = 900
NARRATION_WPM = 150  # words/min; word-count -> runtime estimate. VALIDATE on first real TTS.
# Canonical section roles, in the order the spec mandates. Each payoff opens the
# next loop; no clean exits mid-script.
SCRIPT_SECTION_ROLES = [
    "false_cause",
    "turn",
    "true_cause",
    "re_hook",
    "lever",
    "close",
]
MECHANISM_CONFIDENCE_VALUES = ["solid", "partial", "check"]

# --- Beats (stage 2) -------------------------------------------------------
BEAT_MODEL = "claude-opus-4-8"
BEAT_MAX_TOKENS = 8000
BEAT_RETRY_ATTEMPTS = 3
BEAT_MIN_SECONDS = 3
BEAT_MAX_SECONDS = 8
# Cinematic treatment appended to every fetch query to bias the stock search
# toward the channel's dark/cool/moody look (a hard filter, not a vibe).
MOOD_TREATMENT = "low light, moody, cinematic, cool tone, shadows"
BEAT_TIERS = [1, 2, 3]

# --- TTS + alignment (stage 4) --------------------------------------------
ELEVENLABS_VOICE_ID = "PLACEHOLDER_PICK_A_DEEP_CALM_MALE_VOICE"  # TODO: user sets real ID
TTS_MODEL = "eleven_multilingual_v2"
ALIGN_MODEL = "whisper-large-v3"  # Groq Whisper for forced alignment of word timings

# --- Background fetch (stage 3) -------------------------------------------
# Degrade-never chain order; CC0 / no-attribution sources only.
BACKGROUND_CHAIN = ["pexels", "pixabay", "coverr", "gradient"]
PEXELS_ENABLED = True
PIXABAY_ENABLED = True
COVERR_ENABLED = True
PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_ORIENTATION = "landscape"  # 16:9 (long-form), not portrait
PEXELS_VIDEO_PER_PAGE = 15  # pull several candidates so the mood filter can choose
PEXELS_SIZE = "medium"
PEXELS_TIMEOUT = 60
PEXELS_BACKOFFS = [2, 4, 8, 16]  # exponential backoff on 429 (free tier: 200 req/hr)
PIXABAY_SEARCH_URL = "https://pixabay.com/api/videos/"
COVERR_SEARCH_URL = "https://api.coverr.co/videos"
FOOTAGE_HISTORY_PATH = os.path.join(TMP_DIR, "footage_history.json")
FOOTAGE_HISTORY_MAX = 600  # 40-70 beats/video -> larger ledger than Shorts

# --- Mood footage filter (stage 3) ----------------------------------------
# Prefer darker, cooler clips; reject generic-bright stock. Scored on poster frames.
MOOD_LUMA_TARGET = 70        # ideal median luma (0-255): dim but not crushed
MOOD_LUMA_LEGIBILITY_FLOOR = 18  # reject below this — too dark to read footage at all
MOOD_COOL_BIAS_WEIGHT = 1.0  # weight on (blue - red) channel mean in the score
MOOD_DARK_WEIGHT = 1.0       # weight on darkness preference in the score

# --- Music & motion (stage 5) ---------------------------------------------
MUSIC_PATH = os.path.join(ASSETS_DIR, "music", "ambient_bed.mp3")
MUSIC_GAIN_DB = -18          # under the full-volume VO
MUSIC_SWELL_GAIN_DB = -14    # slightly louder at the turn and the close
MUSIC_FADE_IN = 1.5
MUSIC_FADE_OUT = 2.0
BG_CROSSFADE = 0.8           # gentle crossfade between beats (default)
BG_HARD_CUT_ROLE = "turn"    # this section's first beat is a hard cut (the reveal), no crossfade
BG_KENBURNS_ZOOM = 1.10      # constant over-scale base (bar-proof: single resize)
BG_KENBURNS_PAN = 40         # max pan drift in px per axis
BG_OVERLAY_OPACITY = 0.45    # darken footage for on-screen-text legibility

# --- On-screen emphasis text (stage 5) ------------------------------------
EMPHASIS_FONT_SIZE = 64
EMPHASIS_COLOR = (240, 242, 245)  # near-white
EMPHASIS_FADE = 0.5

# --- Brand watermark -------------------------------------------------------
BRAND_NAME = "Icarus Wings"
WATERMARK_FONT_SIZE = 28
WATERMARK_OPACITY = 0.35     # low-opacity wordmark, bottom corner

# --- Edge-brightness verification (stage 5) -------------------------------
EDGE_SCAN_STRIP_PX = 15      # leftmost/rightmost strip width to sample
EDGE_SCAN_INTERVAL_S = 2.0   # sample one frame every N seconds across the whole video
EDGE_DARK_THRESHOLD = 8      # one edge below this...
EDGE_BRIGHT_THRESHOLD = 15   # ...while the opposite is above this => suspected bar

# --- Publish (stage 6) -----------------------------------------------------
YOUTUBE_CATEGORY_ID = "22"   # People & Blogs (long-form, NOT a Short)
YOUTUBE_DEFAULT_PRIVACY = "private"
