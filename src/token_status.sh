#!/usr/bin/env bash
# Claude Code Token Status Line — reads live session data from stdin.
#
# Routes each render to the *per-project* status file (status/<pid>.json) so
# multiple Claude Code instances running side-by-side don't fight over a single
# shared current.json. The pid is md5(cwd)[:12] — same scheme as token_tracker.
# Falls back to current.json only when the per-project file doesn't exist yet
# (very first render before SessionStart / Stop has had a chance to create it).
STATUS_DIR="$HOME/.claude/token_usage/status"

LIVE=$(cat)

PID=$(printf '%s' "$LIVE" | python3 -c '
import sys, json, hashlib
try:
    d = json.load(sys.stdin)
    # Prefer top-level cwd (session startup dir, fixed for the session lifetime)
    # over workspace.current_dir (which may track the shell cwd if it drifts).
    cwd = d.get("cwd") or (d.get("workspace") or {}).get("current_dir") or ""
    print(hashlib.md5(cwd.encode()).hexdigest()[:12] if cwd else "")
except Exception:
    print("")
' 2>/dev/null)

if [ -n "$PID" ] && [ -f "$STATUS_DIR/$PID.json" ]; then
    STATUS_JSON="$STATUS_DIR/$PID.json"
elif [ -f "$STATUS_DIR/current.json" ]; then
    STATUS_JSON="$STATUS_DIR/current.json"
else
    echo "💰 --"
    exit 0
fi

printf '%s' "$LIVE" | python3 ~/.claude/hooks/token_tracker.py --render "$STATUS_JSON" 2>/dev/null \
    || echo "💰 --"
