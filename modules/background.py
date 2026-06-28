"""Stage 3 — background: fetch cinematic stock footage per beat.

Input  : beat list from beat_plan (each beat has a concrete 4-7 word query).
Output : ordered list of local clip paths, one per beat.

Fetches directly from each beat's query — no intermediate scene plan. Adjacent
beats with identical queries reuse the same clip automatically via dedup.

Degrade-never chain: Pexels → Pixabay → simplified query retry → gradient fallback.
Dedup: two-tier ledger (within-run + cross-video history).
"""

import json
import logging
import os
import time
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv

import config

load_dotenv()

logger = logging.getLogger(__name__)

CLIPS_DIR = os.path.join(config.TMP_DIR, "clips")
os.makedirs(CLIPS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Dedup ledger
# ---------------------------------------------------------------------------

def _load_history() -> set[str]:
    path = config.FOOTAGE_HISTORY_PATH
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def _save_history(ids: set[str]) -> None:
    trimmed = sorted(ids)[-config.FOOTAGE_HISTORY_MAX:]
    with open(config.FOOTAGE_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(trimmed, f)


# ---------------------------------------------------------------------------
# Pexels
# ---------------------------------------------------------------------------

def _pexels_search(query: str, client: httpx.Client) -> list[dict]:
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        return []

    params = {
        "query": query,
        "orientation": config.PEXELS_ORIENTATION,
        "size": config.PEXELS_SIZE,
        "per_page": config.PEXELS_VIDEO_PER_PAGE,
    }
    url = f"{config.PEXELS_SEARCH_URL}?{urlencode(params)}"

    for attempt, backoff in enumerate(config.PEXELS_BACKOFFS):
        try:
            resp = client.get(url, headers={"Authorization": api_key},
                              timeout=config.PEXELS_TIMEOUT)
            if resp.status_code == 429:
                logger.warning("Pexels 429 — backoff %ds (attempt %d)",
                               backoff, attempt + 1)
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            data = resp.json()
            results = []
            for v in data.get("videos", []):
                files = v.get("video_files", [])
                hd = _pick_pexels_file(files)
                if hd:
                    results.append({
                        "id": f"pexels-{v['id']}",
                        "url": hd["link"],
                        "width": hd.get("width", 0),
                        "height": hd.get("height", 0),
                        "duration": v.get("duration", 0),
                        "source": "pexels",
                    })
            return results
        except httpx.HTTPStatusError as exc:
            logger.warning("Pexels HTTP %d for query %r",
                           exc.response.status_code, query)
            return []
        except httpx.RequestError as exc:
            logger.warning("Pexels request error: %s", exc)
            if attempt < len(config.PEXELS_BACKOFFS) - 1:
                time.sleep(backoff)
            else:
                return []
    return []


def _pick_pexels_file(files: list[dict]) -> dict | None:
    candidates = []
    for f in files:
        w = f.get("width", 0)
        h = f.get("height", 0)
        if w >= 1280 and w > h:
            candidates.append(f)
    if not candidates:
        for f in files:
            if f.get("width", 0) > f.get("height", 0):
                candidates.append(f)
    candidates.sort(key=lambda f: abs(f.get("width", 0) - 1920))
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Pixabay
# ---------------------------------------------------------------------------

def _pixabay_search(query: str, client: httpx.Client) -> list[dict]:
    api_key = os.environ.get("PIXABAY_API_KEY", "")
    if not api_key:
        return []

    params = {
        "key": api_key,
        "q": query,
        "video_type": "film",
        "orientation": "horizontal",
        "per_page": 10,
        "safesearch": "true",
    }
    url = f"{config.PIXABAY_SEARCH_URL}?{urlencode(params)}"

    try:
        resp = client.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for hit in data.get("hits", []):
            videos = hit.get("videos", {})
            pick = (videos.get("large") or videos.get("medium")
                    or videos.get("small", {}))
            if pick and pick.get("url"):
                results.append({
                    "id": f"pixabay-{hit['id']}",
                    "url": pick["url"],
                    "width": pick.get("width", 0),
                    "height": pick.get("height", 0),
                    "duration": hit.get("duration", 0),
                    "source": "pixabay",
                })
        return results
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.warning("Pixabay error for query %r: %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def _pick_best(candidates: list[dict], used_ids: set[str]) -> dict | None:
    fresh = [c for c in candidates if c["id"] not in used_ids]
    if not fresh:
        return candidates[0] if candidates else None
    fresh.sort(key=lambda c: c.get("duration", 0), reverse=True)
    return fresh[0]


def _male_query(query: str) -> str:
    """Append 'man' to steer results toward male subjects only."""
    return query + " man"


def _search_beat(query: str, client: httpx.Client,
                 used_ids: set[str]) -> dict | None:
    """Try query across Pexels → Pixabay. Return best clip or None."""
    q = _male_query(query)

    candidates = _pexels_search(q, client)
    pick = _pick_best(candidates, used_ids)
    if pick:
        return pick

    candidates = _pixabay_search(q, client)
    pick = _pick_best(candidates, used_ids)
    if pick:
        return pick

    # Simplified fallback: first 4 words + "man cinematic dark"
    simple = " ".join(query.split()[:4]) + " man cinematic dark"
    logger.info("  simplified fallback query: %r", simple)
    candidates = _pexels_search(simple, client)
    pick = _pick_best(candidates, used_ids)
    if pick:
        return pick

    return None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _download_clip(url: str, beat_idx: int, source: str,
                   client: httpx.Client) -> str:
    filename = f"beat_{beat_idx:03d}_{source}.mp4"
    path = os.path.join(CLIPS_DIR, filename)

    if os.path.exists(path) and os.path.getsize(path) > 10_000:
        logger.info("  clip cached: %s", filename)
        return path

    resp = client.get(url, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)
    logger.info("  downloaded: %s (%.1f MB)",
                filename, len(resp.content) / 1_048_576)
    return path


# ---------------------------------------------------------------------------
# Gradient fallback
# ---------------------------------------------------------------------------

def _gradient_fallback(beat_idx: int) -> str:
    path = os.path.join(CLIPS_DIR, f"beat_{beat_idx:03d}_gradient.mp4")
    if os.path.exists(path):
        return path
    try:
        import subprocess
        r, g, b = config.VIDEO_BG_COLOR
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=#{r:02x}{g:02x}{b:02x}:s=1920x1080:d=10:r=30",
            "-c:v", "libx264", "-t", "10", "-pix_fmt", "yuv420p",
            path
        ], capture_output=True, timeout=30)
        logger.info("  gradient fallback: beat %d", beat_idx)
    except Exception as exc:
        logger.error("  gradient fallback failed: %s", exc)
    return path


# ---------------------------------------------------------------------------
# Query cache: reuse clip when adjacent beats share a query
# ---------------------------------------------------------------------------

def _normalize_query(query: str) -> str:
    return " ".join(query.lower().split())


# ---------------------------------------------------------------------------
# Main: beat-level fetch
# ---------------------------------------------------------------------------

def fetch_backgrounds(beats: list[dict]) -> dict:
    """Fetch one clip per beat (with query-level reuse for adjacent duplicates).

    Returns:
        { "beat_clips": [ clip_path_for_beat_0, ... ],
          "stats": { "sources": { ... }, "total_beats": N, "reused": N } }
    """
    history_ids = _load_history()
    run_ids: set[str] = set()
    used_ids = history_ids | run_ids

    beat_clips: list[str] = []
    sources: dict[str, int] = {}
    query_cache: dict[str, str] = {}
    reused = 0

    logger.info("Fetching backgrounds for %d beats...", len(beats))

    with httpx.Client() as client:
        for i, beat in enumerate(beats):
            query = beat["query"]
            norm_q = _normalize_query(query)

            logger.info(
                "[%d/%d] tier %d | %s | \"%s\"",
                i + 1, len(beats), beat.get("tier", 0),
                query, beat["line"][:50],
            )

            if norm_q in query_cache:
                beat_clips.append(query_cache[norm_q])
                reused += 1
                logger.info("  reusing cached clip for query: %s", query)
                continue

            clip_path = None
            chosen_id = None

            pick = _search_beat(query, client, used_ids)
            if pick:
                try:
                    clip_path = _download_clip(
                        pick["url"], i, pick["source"], client)
                    chosen_id = pick["id"]
                    sources[pick["source"]] = sources.get(pick["source"], 0) + 1
                except Exception as exc:
                    logger.warning("  download failed: %s", exc)

            if not clip_path:
                logger.warning("  no footage — gradient fallback")
                clip_path = _gradient_fallback(i)
                sources["gradient"] = sources.get("gradient", 0) + 1

            if chosen_id:
                run_ids.add(chosen_id)
                used_ids.add(chosen_id)

            query_cache[norm_q] = clip_path
            beat_clips.append(clip_path)

    _save_history(history_ids | run_ids)

    logger.info(
        "Background fetch complete | beats=%d | reused=%d | sources=%s",
        len(beats), reused,
        " ".join(f"{k}:{v}" for k, v in sorted(sources.items())),
    )

    return {
        "beat_clips": beat_clips,
        "stats": {
            "sources": sources,
            "total_beats": len(beats),
            "reused": reused,
        },
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    beats_path = os.path.join(config.TMP_DIR, "beats_01.json")
    with open(beats_path, "r", encoding="utf-8") as f:
        beats_data = json.load(f)

    result = fetch_backgrounds(beats_data["beats"])

    manifest_path = os.path.join(config.TMP_DIR, "clips_manifest_01.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n=== Background fetch: {result['stats']['total_beats']} beats ===")
    print(f"Reused: {result['stats']['reused']}")
    print(f"Sources: {json.dumps(result['stats']['sources'], indent=2)}")
    print(f"Manifest -> {manifest_path}")
