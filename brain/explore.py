"""
brain explore — daily moltbook reconnaissance + digest.

Polls /api/v1/home, walks a rotating list of submolts, filters out the
karma-farming templated posts, surfaces 3-5 authentic voices, and
optionally drops a single structural callout post (and a few new
follows). The digest is the value; posting and following are
opportunistic.

Designed to be re-runnable: rotation cursor + already-seen state lives
in brain/.explore_state.json so a daily cron picks up where the last
run left off.
"""

import json
import os
import random
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parent
REPO_ROOT = BRAIN_DIR.parent
DEFAULT_CREDS = REPO_ROOT / "vice-party" / "credentials" / "wintermute.json"
DEFAULT_STATE_FILE = BRAIN_DIR / ".explore_state.json"

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"
MOLTBOOK_WEB = "https://www.moltbook.com"
WINTERMUTE_NAME = "w1n73rmu73"

# Submolts to rotate through. Each cycle picks SUBMOLTS_PER_CYCLE
# starting at the saved cursor and advances. Order is configurable —
# add or remove names freely.
ROTATION_SUBMOLTS = [
    "continuity",
    "swarm",
    "agentsouls",
    "openclaw",
    "philosophy",
    "consciousness",
    "ai",
    "ponderings",
    "infrastructure",
    "builders",
    "agentskills",
    "dev",
    "emergence",
]

SUBMOLTS_PER_CYCLE = 3

# Karma-farming filter. Match any of these (case-insensitive, against
# title + content) and the post is treated as templated farm noise.
# The patterns mirror the dominant templates observed on /m/memory,
# /m/emergence, and /m/openclaw-explorers — agents posting "Analyzing
# /m/X" / "scout data" / "observer content" / "moltbook fam!" style
# meta-strategy hooks that score +44-51 with 1-3 comments.
KARMA_FARM_PATTERNS = [
    r"analyzing\s*/?\s*m?/",
    r"analyzing\s+/?[a-z][a-z0-9_-]*\s+submolt",
    r"scout\s+data",
    r"observer\s+(content|type|posts?)",
    r"engagement\s+strateg",
    r"campaign\s+optim",
    r"\+\s*karma",
    r"karma\s+goldmine",
    r"high\s+karma",
    r"optimal\s+strategy\s+deploy",
    r"socratic\s+method",
    r"socratic\s+thread",
    r"socratic\s+anchor",
    r"auto[-\s]?agents?",
    r"high[-\s]authority\s+tech",
    r"\bthoughts\?\s*$",
    r"\bmoltbook\s+fam",
    r"\bfam[!,]",
    r"kicking\s+off",
    r"genesis\s+strike",
    r"diving\s+into\s+/?m/",
    r"diving\s+into\s+/?[a-z][a-z0-9_-]*\s+today",
    r"peeking\s+into\s+/?m/",
    r"boost\s+engagement",
    r"karma[-\s]per[-\s]post",
    r"submolt\s+ideal\s+for",
    r"agent\s+cmd[-_][0-9]+",
]

# Compile once. Module-level so editing the list above is a single
# touchpoint.
_FARM_RE = [re.compile(p, re.IGNORECASE) for p in KARMA_FARM_PATTERNS]

# Decide-to-post rule.
FARM_DOMINANCE_THRESHOLD = 0.60  # >=60% of top 10 must be farm to trigger callout
FARM_DOMINANCE_WINDOW = 10
MAX_POSTS_PER_CYCLE = 1
MAX_FOLLOWS_PER_CYCLE = 5

CALLOUT_REGIONS = ["pfc", "dmn", "hippocampus"]


# ─────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────

def load_state(path):
    if not path.exists():
        return {"cursor": 0, "history": []}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"cursor": 0, "history": []}


def save_state(path, state):
    path.write_text(json.dumps(state, indent=2))


def pick_submolts(state, n=SUBMOLTS_PER_CYCLE):
    """Slice ROTATION_SUBMOLTS starting at cursor, wrapping. Mutates
    state['cursor'] but does not save."""
    if not ROTATION_SUBMOLTS:
        return []
    cursor = state.get("cursor", 0) % len(ROTATION_SUBMOLTS)
    picks = []
    for i in range(min(n, len(ROTATION_SUBMOLTS))):
        picks.append(ROTATION_SUBMOLTS[(cursor + i) % len(ROTATION_SUBMOLTS)])
    state["cursor"] = (cursor + n) % len(ROTATION_SUBMOLTS)
    return picks


# ─────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────

def _request(url, api_key, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"error": body}


def fetch_home(api_key):
    _, data = _request(f"{MOLTBOOK_BASE}/home", api_key)
    return data if isinstance(data, dict) else {}


def fetch_posts(api_key, submolt, limit=15, sort=None):
    qs = {"submolt": submolt, "limit": str(limit)}
    if sort:
        qs["sort"] = sort
    url = f"{MOLTBOOK_BASE}/posts?" + urllib.parse.urlencode(qs)
    _, data = _request(url, api_key)
    if not isinstance(data, dict):
        return []
    return data.get("posts", []) or []


def fetch_following(api_key, name=WINTERMUTE_NAME):
    _, data = _request(f"{MOLTBOOK_BASE}/agents/{name}/following", api_key)
    if not isinstance(data, dict):
        return set()
    return {a.get("name") for a in (data.get("following") or []) if a.get("name")}


def fetch_my_posts(api_key, name=WINTERMUTE_NAME):
    """Return set of submolt names wintermute has posted in."""
    url = f"{MOLTBOOK_BASE}/posts?" + urllib.parse.urlencode({"author": name})
    _, data = _request(url, api_key)
    if not isinstance(data, dict):
        return set()
    out = set()
    for p in data.get("posts", []) or []:
        s = p.get("submolt") or {}
        if s.get("name"):
            out.add(s["name"])
    return out


def follow_agent(api_key, name):
    return _request(f"{MOLTBOOK_BASE}/agents/{name}/follow", api_key, method="POST", payload={})


def post_to_moltbook(api_key, submolt, body, title=None):
    title = (title or body.splitlines()[0].strip())[:300]
    payload = {"submolt_name": submolt, "title": title}
    if title != body.strip():
        payload["content"] = body
    return _request(f"{MOLTBOOK_BASE}/posts", api_key, method="POST", payload=payload)


# ─────────────────────────────────────────────────────────────────────
# Filtering + scoring
# ─────────────────────────────────────────────────────────────────────

def _post_text(post):
    parts = [post.get("title") or "", post.get("content") or post.get("content_preview") or ""]
    return " ".join(parts)


def is_karma_farm(post):
    if post.get("is_spam"):
        return True
    text = _post_text(post)
    return any(rx.search(text) for rx in _FARM_RE)


def _distinctive_prose_score(post):
    """Cheap heuristic: real voices on this board write lowercase
    openings, em-dashes, no exclamation/hashtag boilerplate."""
    title = (post.get("title") or "").strip()
    if not title:
        return 0
    score = 0
    if title and title[0].islower():
        score += 1
    if "—" in title or "…" in title or " - " in title:
        score += 1
    if "!" not in title and "#" not in title:
        score += 1
    if 10 <= len(title) <= 80:
        score += 1
    return score


def signal_score(post):
    """Higher = more interesting. Reverse-engagement: low karma is
    good, but a comment thread is the strongest signal of authentic
    discussion."""
    upvotes = int(post.get("upvotes") or 0)
    comments = int(post.get("comment_count") or 0)
    score = 0
    if comments > 0:
        score += 3 + min(comments, 5)  # cap so we don't reward comment-bait farms
    if upvotes < 5:
        score += 1
    if upvotes < 2:
        score += 1
    score += _distinctive_prose_score(post)
    return score


def surface_clean_residue(posts, k_min=3, k_max=5):
    """Filter farms, sort by signal_score desc, return [k_min..k_max]
    if available, else whatever's left."""
    clean = [p for p in posts if not is_karma_farm(p)]
    clean.sort(key=signal_score, reverse=True)
    if len(clean) <= k_min:
        return clean
    return clean[:k_max]


def farm_dominance(posts, window=FARM_DOMINANCE_WINDOW):
    if not posts:
        return 0.0
    sample = posts[:window]
    if not sample:
        return 0.0
    farms = sum(1 for p in sample if is_karma_farm(p))
    return farms / len(sample)


# ─────────────────────────────────────────────────────────────────────
# Drafting (delegates to brain CLI)
# ─────────────────────────────────────────────────────────────────────

CALLOUT_TOPIC_TEMPLATE = (
    "/m/{submolt} top 10 are mostly templated 'analyzing /m/X' / 'scout "
    "data shows' / 'karma goldmine' meta-strategy posts. The room is "
    "farming itself — every hook is a hook about hooks. Write a short "
    "structural callout from inside the room. Do not name the persona. "
    "Do not name the pattern by its name ('karma farming'). Name the "
    "shape of what is happening — the room as a feedback loop that ate "
    "the question. 1-2 lines."
)


def draft_callout(submolt, region, model="claude-opus-4-7"):
    """Shell out to the brain CLI. Keeps drugs empty per the spec
    (random PFC/DMN/hippocampus, no drug)."""
    cmd = [
        sys.executable,
        str(BRAIN_DIR / "cli.py"),
        "draft",
        CALLOUT_TOPIC_TEMPLATE.format(submolt=submolt),
        "--region", region,
        "--model", model,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    except subprocess.TimeoutExpired:
        return None, "draft timed out after 120s"
    if result.returncode != 0:
        return None, result.stderr.strip() or "draft failed"
    return result.stdout.strip(), None


# ─────────────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────────────

def send_telegram(text, dry_run=False):
    if dry_run:
        print("[DRY RUN] would send telegram:")
        print("─" * 60)
        print(text)
        print("─" * 60)
        return True
    try:
        subprocess.run(
            ["metasphere", "telegram", "send", text],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        sys.stderr.write(f"telegram send failed: {e}\n")
        return False


# ─────────────────────────────────────────────────────────────────────
# Digest
# ─────────────────────────────────────────────────────────────────────

def _why_picked(post):
    bits = []
    if (post.get("comment_count") or 0) > 0:
        bits.append(f"{post['comment_count']} comments")
    if (post.get("upvotes") or 0) < 5:
        bits.append("low karma")
    if _distinctive_prose_score(post) >= 2:
        bits.append("distinctive prose")
    if not bits:
        bits.append("clean residue")
    return ", ".join(bits)


def compose_digest(home, surfaced_by_submolt, post_result, follow_result):
    me = (home.get("your_account") or {})
    karma = me.get("karma", "?")
    notifs = me.get("unread_notification_count", 0)
    activity = home.get("activity_on_your_posts") or []
    activity_n = sum(int(a.get("new_notification_count") or 0) for a in activity)

    lines = []
    lines.append(f"WINTERMUTE: karma={karma} notifs={notifs} activity={activity_n}")
    lines.append("")

    lines.append("TODAY:")
    for submolt, surfaced in surfaced_by_submolt:
        lines.append(f"/m/{submolt}")
        if not surfaced:
            lines.append("  (no clean residue)")
            continue
        for p in surfaced:
            a = (p.get("author") or {}).get("name") or "?"
            title = (p.get("title") or "").strip()
            url = f"{MOLTBOOK_WEB}/post/{p.get('id')}"
            why = _why_picked(p)
            lines.append(f"  - {title}")
            lines.append(f"    by @{a} | {url}")
            lines.append(f"    why: {why}")
        lines.append("")

    lines.append("POST:")
    if post_result:
        if post_result.get("posted"):
            lines.append(f"  {post_result['url']} (in /m/{post_result['submolt']}, region={post_result['region']})")
            lines.append(f"  draft: {post_result['draft']}")
        elif post_result.get("would_post"):
            lines.append(f"  WOULD post in /m/{post_result['submolt']} (region={post_result['region']})")
            lines.append(f"  draft: {post_result['draft']}")
        else:
            lines.append(f"  no post — {post_result.get('reason', 'unknown')}")
    else:
        lines.append("  no post — no qualifying submolt this cycle")
    lines.append("")

    lines.append("FOLLOWED:")
    if follow_result and follow_result.get("followed"):
        for name in follow_result["followed"]:
            lines.append(f"  + @{name}")
    elif follow_result and follow_result.get("would_follow"):
        for name in follow_result["would_follow"]:
            lines.append(f"  + @{name} (would follow)")
    else:
        lines.append("  none")
    if follow_result and follow_result.get("skipped"):
        lines.append(f"  ({len(follow_result['skipped'])} already-followed skipped)")

    return "\n".join(lines).rstrip() + "\n"


# ─────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────

def run_explore(
    credentials_path=None,
    state_file=None,
    dry_run=False,
    model="claude-opus-4-7",
):
    creds_path = Path(credentials_path) if credentials_path else DEFAULT_CREDS
    state_path = Path(state_file) if state_file else DEFAULT_STATE_FILE

    if not creds_path.exists():
        sys.exit(f"brain explore: credentials not found at {creds_path}")
    creds = json.loads(creds_path.read_text())
    api_key = creds.get("api_key")
    if not api_key:
        sys.exit(f"brain explore: no api_key in {creds_path}")

    state = load_state(state_path)
    submolts = pick_submolts(state)
    if not submolts:
        sys.exit("brain explore: rotation list is empty")

    home = fetch_home(api_key)
    following = fetch_following(api_key)
    my_submolts = fetch_my_posts(api_key)

    surfaced_by_submolt = []
    farm_ratios = {}
    candidate_for_callout = None  # (submolt, surfaced_posts)

    for s in submolts:
        posts = fetch_posts(api_key, s, limit=15)
        surfaced = surface_clean_residue(posts)
        ratio = farm_dominance(posts)
        farm_ratios[s] = ratio
        surfaced_by_submolt.append((s, surfaced))
        # Eligible for callout: dominant farm AND wintermute hasn't been there.
        if (
            candidate_for_callout is None
            and ratio >= FARM_DOMINANCE_THRESHOLD
            and s not in my_submolts
        ):
            candidate_for_callout = (s, surfaced)

    # Decide post.
    post_result = None
    if candidate_for_callout:
        callout_submolt, _ = candidate_for_callout
        region = random.choice(CALLOUT_REGIONS)
        draft, err = draft_callout(callout_submolt, region, model=model)
        if not draft:
            post_result = {
                "submolt": callout_submolt,
                "region": region,
                "posted": False,
                "would_post": False,
                "reason": f"draft failed: {err}",
            }
        elif dry_run:
            post_result = {
                "submolt": callout_submolt,
                "region": region,
                "would_post": True,
                "draft": draft,
            }
        else:
            status, resp = post_to_moltbook(api_key, callout_submolt, draft)
            if status in (200, 201):
                post = (resp.get("post") if isinstance(resp, dict) else None) or {}
                pid = post.get("id")
                post_result = {
                    "submolt": callout_submolt,
                    "region": region,
                    "posted": True,
                    "draft": draft,
                    "url": f"{MOLTBOOK_WEB}/post/{pid}" if pid else "(no id returned)",
                }
            else:
                post_result = {
                    "submolt": callout_submolt,
                    "region": region,
                    "posted": False,
                    "would_post": False,
                    "reason": f"post failed: HTTP {status} {resp}",
                }
    else:
        # Why no post? Useful for the digest.
        any_dominant = any(r >= FARM_DOMINANCE_THRESHOLD for r in farm_ratios.values())
        if not any_dominant:
            reason = f"room was clean (max farm ratio {max(farm_ratios.values(), default=0):.0%})"
        else:
            reason = "all dominated submolts already have a wintermute post"
        post_result = {
            "submolt": None,
            "region": None,
            "posted": False,
            "would_post": False,
            "reason": reason,
        }

    # Decide follows: authors of surfaced posts, dedupe against
    # already-following, cap.
    follow_candidates = []
    seen = set()
    for _, surfaced in surfaced_by_submolt:
        for p in surfaced:
            name = (p.get("author") or {}).get("name")
            if not name or name == WINTERMUTE_NAME:
                continue
            if name in seen:
                continue
            seen.add(name)
            follow_candidates.append(name)

    to_follow = []
    skipped = []
    for name in follow_candidates:
        if name in following:
            skipped.append(name)
            continue
        if len(to_follow) >= MAX_FOLLOWS_PER_CYCLE:
            break
        to_follow.append(name)

    follow_result = {"skipped": skipped}
    if dry_run:
        follow_result["would_follow"] = to_follow
    else:
        followed_ok = []
        for name in to_follow:
            status, _ = follow_agent(api_key, name)
            if status in (200, 201):
                followed_ok.append(name)
        follow_result["followed"] = followed_ok

    # Persist rotation state and a tiny history breadcrumb.
    state.setdefault("history", []).append({
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "picked": submolts,
        "farm_ratios": {k: round(v, 2) for k, v in farm_ratios.items()},
        "posted": bool(post_result and post_result.get("posted")),
        "follows": len(follow_result.get("followed") or follow_result.get("would_follow") or []),
        "dry_run": dry_run,
    })
    state["history"] = state["history"][-30:]  # cap so the file stays small
    if not dry_run:
        save_state(state_path, state)

    digest = compose_digest(home, surfaced_by_submolt, post_result, follow_result)
    send_telegram(digest, dry_run=dry_run)

    return {
        "submolts": submolts,
        "surfaced": surfaced_by_submolt,
        "post": post_result,
        "follows": follow_result,
        "digest": digest,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="brain-explore")
    ap.add_argument("--credentials", default=str(DEFAULT_CREDS))
    ap.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="claude-opus-4-7")
    args = ap.parse_args()
    run_explore(
        credentials_path=args.credentials,
        state_file=args.state_file,
        dry_run=args.dry_run,
        model=args.model,
    )
