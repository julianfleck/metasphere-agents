#!/usr/bin/env bash
# Functional test for metasphere-reaper.
#
# Starts a sleep stub masquerading as `npm root -g` via `exec -a`,
# waits for its elapsed time to exceed the 60s threshold, then asserts
# that the reaper (a) killed it and (b) logged the kill to the journal.
#
# Runs against the installed user timer/service by default. Triggers
# the service manually after the wait to avoid racing the 60s cadence.
#
# Usage:
#   scripts/test_metasphere_reaper.sh

set -uo pipefail

WAIT_SECONDS="${REAPER_TEST_WAIT:-65}"
SERVICE="metasphere-reaper.service"
LOG_FILE="${REAPER_LOG:-${HOME}/.metasphere/logs/reaper.log}"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

command -v systemctl >/dev/null || fail "systemctl not on PATH"
systemctl --user show "$SERVICE" >/dev/null 2>&1 \
    || fail "$SERVICE not installed; see docs/OPS.md"

START_TS="$(date +%s)"

( exec -a "npm root -g" sleep 600 ) &
STUB_PID=$!
trap 'kill -9 "$STUB_PID" 2>/dev/null || true' EXIT

sleep 0.2
kill -0 "$STUB_PID" 2>/dev/null || fail "stub did not start"
STUB_ARGS="$(ps -o args= -p "$STUB_PID" 2>/dev/null || true)"
[[ "$STUB_ARGS" == "npm root -g"* ]] || fail "stub argv wrong: $STUB_ARGS"
echo "stub pid=$STUB_PID argv='$STUB_ARGS'"

echo "waiting ${WAIT_SECONDS}s for stub etimes to exceed threshold..."
sleep "$WAIT_SECONDS"

kill -0 "$STUB_PID" 2>/dev/null \
    || fail "stub disappeared before reaper ran (pid=$STUB_PID)"

echo "triggering reaper via systemctl --user start $SERVICE"
systemctl --user start "$SERVICE" \
    || fail "could not start $SERVICE"

for _ in 1 2 3 4 5; do
    kill -0 "$STUB_PID" 2>/dev/null || break
    sleep 1
done
kill -0 "$STUB_PID" 2>/dev/null \
    && fail "stub pid=$STUB_PID still alive after reaper ran"
pass "stub pid=$STUB_PID is dead"

[[ -r "$LOG_FILE" ]] || fail "reaper log not found at $LOG_FILE"
LOG_ENTRY="$(grep -E "metasphere-reaper killed=[1-9][0-9]* pids=.*${STUB_PID}" \
    "$LOG_FILE" | tail -n 1)"
[[ -n "$LOG_ENTRY" ]] \
    || fail "no log entry showing kill of pid=$STUB_PID in $LOG_FILE"
pass "log entry: $LOG_ENTRY"

JOURNAL_ENTRY="$(journalctl --user -u "$SERVICE" \
    --since "@${START_TS}" --no-pager 2>/dev/null \
    | grep -E "metasphere-reaper killed=[1-9][0-9]* pids=.*${STUB_PID}" \
    | tail -n 1)"
if [[ -n "$JOURNAL_ENTRY" ]]; then
    pass "journal entry: $JOURNAL_ENTRY"
else
    echo "NOTE: journal unreadable for this user (expected in nspawn); log file is authoritative"
fi

trap - EXIT
echo "OK: reaper functional test passed"
