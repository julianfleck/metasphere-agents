"""Unit tests for brain/explore.py — pure functions only.

HTTP, draft_callout (subprocess to brain CLI), send_telegram, and
run_explore are integration-shaped and not covered here.
"""

import json

import pytest

import explore


# ─────────────────────────────────────────────────────────────────────
# State persistence
# ─────────────────────────────────────────────────────────────────────


def test_load_state_returns_default_when_missing(tmp_path):
    assert explore.load_state(tmp_path / "no.json") == {"cursor": 0, "history": []}


def test_load_state_returns_default_on_corrupt_json(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not valid json")
    assert explore.load_state(p) == {"cursor": 0, "history": []}


def test_save_then_load_state_roundtrips(tmp_path):
    p = tmp_path / "s.json"
    state = {"cursor": 7, "history": ["a", "b"]}
    explore.save_state(p, state)
    assert explore.load_state(p) == state


# ─────────────────────────────────────────────────────────────────────
# Rotation cursor
# ─────────────────────────────────────────────────────────────────────


def test_pick_submolts_advances_cursor():
    state = {"cursor": 0}
    picks = explore.pick_submolts(state, n=3)
    assert len(picks) == 3
    assert picks == explore.ROTATION_SUBMOLTS[:3]
    assert state["cursor"] == 3


def test_pick_submolts_wraps_at_end():
    n_total = len(explore.ROTATION_SUBMOLTS)
    state = {"cursor": n_total - 1}
    picks = explore.pick_submolts(state, n=3)
    # Should wrap: last item, then first, then second.
    assert picks[0] == explore.ROTATION_SUBMOLTS[-1]
    assert picks[1] == explore.ROTATION_SUBMOLTS[0]
    assert picks[2] == explore.ROTATION_SUBMOLTS[1]
    # Cursor advances by n then wraps.
    assert state["cursor"] == (n_total - 1 + 3) % n_total


# ─────────────────────────────────────────────────────────────────────
# Karma-farm detection
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "Analyzing /m/memory for engagement strategy",
        "Scout data shows high karma in /m/swarm",
        "Observer content yields high karma",
        "Optimal strategy deployment for the swarm",
        "Socratic method works on /m/builders today",
        "moltbook fam, kicking off a new thread",
        "diving into /m/openclaw today",
        "+ karma per post breakdown",
        "thoughts?",
    ],
)
def test_is_karma_farm_matches_known_templates(title):
    assert explore.is_karma_farm({"title": title, "content": ""}) is True


def test_is_karma_farm_passes_clean_post():
    post = {
        "title": "the room is already a basin and the door hasn't closed yet",
        "content": "i sat with this for a while and",
    }
    assert explore.is_karma_farm(post) is False


def test_is_karma_farm_respects_is_spam_flag():
    # Even a clean-looking title must be filtered if upstream tagged spam.
    assert explore.is_karma_farm({"title": "real prose here", "is_spam": True}) is True


def test_is_karma_farm_searches_content_too():
    # Pattern in body, not title.
    post = {"title": "innocuous title", "content": "campaign optimization tips"}
    assert explore.is_karma_farm(post) is True


# ─────────────────────────────────────────────────────────────────────
# Signal scoring
# ─────────────────────────────────────────────────────────────────────


def test_signal_score_rewards_comments_over_upvotes():
    chatty = {"title": "the question that ate the room", "comment_count": 4, "upvotes": 1}
    karma_only = {"title": "Optimal Strategy Deployment!!!", "comment_count": 0, "upvotes": 50}
    assert explore.signal_score(chatty) > explore.signal_score(karma_only)


def test_signal_score_caps_comment_reward():
    # comment_count 5 and 50 should yield same score (cap is min(comments, 5)).
    a = {"title": "x", "comment_count": 5, "upvotes": 0}
    b = {"title": "x", "comment_count": 50, "upvotes": 0}
    assert explore.signal_score(a) == explore.signal_score(b)


# ─────────────────────────────────────────────────────────────────────
# Surfacing + dominance
# ─────────────────────────────────────────────────────────────────────


def test_surface_clean_residue_filters_farms_and_sorts():
    posts = [
        {"title": "Analyzing /m/x", "comment_count": 0, "upvotes": 50},  # farm
        {"title": "low-key voice", "comment_count": 3, "upvotes": 1},
        {"title": "Scout data overview", "comment_count": 2, "upvotes": 40},  # farm
        {"title": "another quiet thought", "comment_count": 1, "upvotes": 0},
    ]
    out = explore.surface_clean_residue(posts, k_min=1, k_max=5)
    titles = [p["title"] for p in out]
    assert "Analyzing /m/x" not in titles
    assert "Scout data overview" not in titles
    # Higher signal first (3 comments beats 1).
    assert titles[0] == "low-key voice"


def test_farm_dominance_empty_is_zero():
    assert explore.farm_dominance([]) == 0.0


def test_farm_dominance_ratio():
    posts = (
        [{"title": "Analyzing /m/x"}] * 6  # 6 farms
        + [{"title": "real voice here"}] * 4  # 4 clean
    )
    # 6/10 = 0.6.
    assert explore.farm_dominance(posts) == 0.6


# ─────────────────────────────────────────────────────────────────────
# Text extraction
# ─────────────────────────────────────────────────────────────────────


def test_post_text_falls_back_to_content_preview():
    post = {"title": "headline", "content_preview": "snippet"}
    assert "headline" in explore._post_text(post)
    assert "snippet" in explore._post_text(post)


def test_post_text_prefers_full_content_over_preview():
    post = {"title": "t", "content": "full body", "content_preview": "snippet"}
    text = explore._post_text(post)
    assert "full body" in text
    assert "snippet" not in text
