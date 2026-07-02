#!/bin/bash
# OpenClaw detection. Exit 0 if ~/.openclaw exists, non-zero otherwise.
# Override the source dir via OPENCLAW_DIR.
set -e
OPENCLAW_DIR="${OPENCLAW_DIR:-$HOME/.openclaw}"
[[ -d "$OPENCLAW_DIR" ]]
