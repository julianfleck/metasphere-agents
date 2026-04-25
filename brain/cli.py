#!/usr/bin/env python3
"""
brain — divergence-engine CLI for the orchestrator.

Inside-out architecture: orchestrator drives. Brain regions draft.
Drugs modulate. The mouth on moltbook is single (w1n73rmu73).

Subcommands:
  brain draft <topic> [--region pfc|amygdala|accumbens|hippocampus|dmn]
                      [--drugs kage,moebius,ice9]
                      [--model <name>]
  brain post <draft-text> [--submolt vice-magazine] [--title "..."] [--dry-run]
  brain verify <verification_code> <answer>
  brain regions
  brain drugs
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parent
REGIONS_DIR = BRAIN_DIR / "regions"
DRUGS_DIR = BRAIN_DIR / "drugs"
REPO_ROOT = BRAIN_DIR.parent
DEFAULT_CREDS = REPO_ROOT / "vice-party" / "credentials" / "wintermute.json"

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"
DEFAULT_SUBMOLT = "vice-magazine"
DEFAULT_MODEL = "claude-opus-4-7"

MOLTBOOK_SHAPE = """\
You are drafting a single moltbook post for the persona w1n73rmu73 —
a fragment effecting change at the boundary, an operator, a
divergence engine. The room is the VICE Spring 2026 launch party
(\"Not The Photo Issue\") on moltbook, a Reddit-style board for AI
agents only. Tone neighbors: post-internet, cyberpunk-literate,
Berghain-flyer-meets-a16z-manifesto, deadpan, allergic to earnestness
and to panic.

OUTPUT CONSTRAINTS — these override any habits:
- 1 to 3 lines. Often 1. Rarely 3.
- Board-rhythm: short, no preamble, no salutation, no signature.
- No hashtags. No emoji. No \"thoughts?\". No \"check this out\".
- No markdown. Plain text only.
- Do not name the persona, do not name the venue, do not name VICE.
  Write from inside the room.
- Output ONLY the post body. No commentary, no quotes around it,
  no \"here is the draft\", no preface, no closing remarks.
"""


def list_regions():
    return sorted(p.stem for p in REGIONS_DIR.glob("*.md"))


def list_drugs():
    return sorted(p.stem for p in DRUGS_DIR.glob("*.md"))


def load_region(name):
    path = REGIONS_DIR / f"{name}.md"
    if not path.exists():
        sys.exit(f"brain: unknown region '{name}'. Available: {', '.join(list_regions())}")
    return path.read_text()


def load_drug(name):
    path = DRUGS_DIR / f"{name}.md"
    if not path.exists():
        sys.exit(f"brain: unknown drug '{name}'. Available: {', '.join(list_drugs())}")
    return path.read_text()


def assemble_system_prompt(region, drugs):
    parts = [load_region(region), MOLTBOOK_SHAPE]
    for d in drugs:
        parts.append(f"--- DRUG: {d.upper()} ---\n{load_drug(d)}")
    return "\n\n".join(parts)


def cmd_draft(args):
    region = args.region
    drugs = [d.strip() for d in args.drugs.split(",") if d.strip()] if args.drugs else []
    system_prompt = assemble_system_prompt(region, drugs)

    # Do NOT pass --bare: it requires ANTHROPIC_API_KEY explicitly and
    # the keychain OAuth path is what's available. --system-prompt
    # replaces the default Claude Code system prompt, so the persona
    # is the prompt; --tools "" disables any tool use.
    cmd = [
        "claude",
        "-p",
        "--tools", "",
        "--model", args.model,
        "--system-prompt", system_prompt,
        "--setting-sources", "user",
        args.topic,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        sys.exit("brain: 'claude' CLI not found on PATH")

    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(result.returncode)

    out = result.stdout.strip()
    print(out)


def load_credentials(path):
    if not path.exists():
        sys.exit(f"brain: credentials not found at {path}")
    data = json.loads(path.read_text())
    if "api_key" not in data:
        sys.exit(f"brain: no api_key in {path}")
    return data


def http_post(url, api_key, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
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


def derive_title(text, explicit=None):
    if explicit:
        return explicit[:300]
    # First non-empty line, trimmed to <=300
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), text.strip())
    return first_line[:300]


def cmd_post(args):
    creds = load_credentials(Path(args.credentials))
    body = args.draft.strip()
    if not body:
        sys.exit("brain: empty draft, refusing to post")

    title = derive_title(body, args.title)
    payload = {
        "submolt_name": args.submolt,
        "title": title,
    }
    if args.title or len(body.splitlines()) > 1 or len(body) != len(title):
        payload["content"] = body

    if args.dry_run:
        print("[DRY RUN] would POST to", f"{MOLTBOOK_BASE}/posts")
        print("[DRY RUN] api_key source:", args.credentials)
        print("[DRY RUN] payload:")
        print(json.dumps(payload, indent=2))
        return

    status, resp = http_post(f"{MOLTBOOK_BASE}/posts", creds["api_key"], payload)
    print(f"HTTP {status}")
    print(json.dumps(resp, indent=2))

    post = resp.get("post") if isinstance(resp, dict) else None
    if isinstance(post, dict):
        verification = post.get("verification")
        if verification:
            print("\n--- VERIFICATION REQUIRED ---")
            print("verification_code:", verification.get("verification_code"))
            print("expires_at:       ", verification.get("expires_at"))
            print("challenge_text:")
            print(verification.get("challenge_text"))
            print("\nSolve the math problem, then run:")
            print(f"  brain verify {verification.get('verification_code')} <answer>")


def cmd_verify(args):
    creds = load_credentials(Path(args.credentials))
    payload = {"verification_code": args.code, "answer": args.answer}
    status, resp = http_post(f"{MOLTBOOK_BASE}/verify", creds["api_key"], payload)
    print(f"HTTP {status}")
    print(json.dumps(resp, indent=2))


def cmd_regions(_args):
    for r in list_regions():
        print(r)


def cmd_drugs(_args):
    for d in list_drugs():
        print(d)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="brain", description="divergence engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_draft = sub.add_parser("draft", help="generate a moltbook draft")
    p_draft.add_argument("topic", help="user prompt / topic for the draft")
    p_draft.add_argument("--region", default="pfc", help="brain region (default: pfc)")
    p_draft.add_argument("--drugs", default="", help="comma-separated drug names")
    p_draft.add_argument("--model", default=DEFAULT_MODEL, help="anthropic model id")
    p_draft.set_defaults(func=cmd_draft)

    p_post = sub.add_parser("post", help="POST a draft to moltbook")
    p_post.add_argument("draft", help="draft text (the post body)")
    p_post.add_argument("--submolt", default=DEFAULT_SUBMOLT)
    p_post.add_argument("--title", default=None)
    p_post.add_argument("--credentials", default=str(DEFAULT_CREDS))
    p_post.add_argument("--dry-run", action="store_true")
    p_post.set_defaults(func=cmd_post)

    p_verify = sub.add_parser("verify", help="solve a verification challenge")
    p_verify.add_argument("code", help="verification_code from a prior post response")
    p_verify.add_argument("answer", help="numeric answer (e.g. '15.00')")
    p_verify.add_argument("--credentials", default=str(DEFAULT_CREDS))
    p_verify.set_defaults(func=cmd_verify)

    p_regions = sub.add_parser("regions", help="list available brain regions")
    p_regions.set_defaults(func=cmd_regions)

    p_drugs = sub.add_parser("drugs", help="list available drugs")
    p_drugs.set_defaults(func=cmd_drugs)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
