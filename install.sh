#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://raw.githubusercontent.com/ayooleo/ClaudeCount/main"
HOOKS_DIR="$HOME/.claude/hooks"
DATA_DIR="$HOME/.claude/token_usage"

echo "Installing ClaudeCount..."

mkdir -p "$HOOKS_DIR" "$DATA_DIR/projects" "$DATA_DIR/status"

for f in token_tracker.py token_status.sh token_report.py; do
    curl -fsSL "$REPO_URL/src/$f" -o "$HOOKS_DIR/$f"
    echo "  ✓ $f"
done
chmod +x "$HOOKS_DIR/token_status.sh"

python3 << 'PYEOF'
import json
from pathlib import Path

settings_path = Path.home() / ".claude" / "settings.json"
settings = {}
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text())
    except Exception:
        pass

tracker_cmd = "python3 ~/.claude/hooks/token_tracker.py"
hooks = settings.setdefault("hooks", {})

def already_registered(event, command):
    return any(
        h.get("command") == command
        for entry in hooks.get(event, [])
        for h in entry.get("hooks", [])
    )

stop_hooks = hooks.setdefault("Stop", [])
if not already_registered("Stop", tracker_cmd):
    stop_hooks.append({
        "hooks": [{"type": "command", "command": tracker_cmd, "async": True}]
    })

# SessionStart resets Turn/Sess display the moment a new session opens, so the
# status bar never shows leftover data from the previous session.
session_cmd = f"{tracker_cmd} --session-start"
session_hooks = hooks.setdefault("SessionStart", [])
if not already_registered("SessionStart", session_cmd):
    session_hooks.append({
        "hooks": [{"type": "command", "command": session_cmd}]
    })

# UserPromptSubmit is a fallback in case SessionStart didn't fire.
preturn_cmd = f"{tracker_cmd} --pre-turn"
preturn_hooks = hooks.setdefault("UserPromptSubmit", [])
if not already_registered("UserPromptSubmit", preturn_cmd):
    preturn_hooks.append({
        "hooks": [{"type": "command", "command": preturn_cmd}]
    })

STATUSLINE_CMD = "bash ~/.claude/hooks/token_status.sh"
existing = settings.get("statusLine", {})
if not existing or existing.get("command") == STATUSLINE_CMD:
    settings["statusLine"] = {
        "type": "command",
        "command": STATUSLINE_CMD,
        "refreshInterval": 30,
    }

settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
print("  ✓ ~/.claude/settings.json updated")
PYEOF

VERSION=$(python3 "$HOOKS_DIR/token_tracker.py" --version 2>/dev/null | awk '{print $2}')
echo ""
echo "✓ ClaudeCount ${VERSION:-installed}! Restart Claude Code to activate."
echo ""
echo "  Status bar shows: PROJECT 🌡️ ctx% ⚡ hit% │ Turn │ Sess │ Proj"
echo ""
echo "  Detailed report:"
echo "    python3 ~/.claude/hooks/token_report.py           # current project"
echo "    python3 ~/.claude/hooks/token_report.py --all     # all projects"
echo "    python3 ~/.claude/hooks/token_report.py --all -v  # verbose"
echo ""
echo "  Adopt sessions that pre-date ClaudeCount:"
echo "    python3 ~/.claude/hooks/token_tracker.py --import"
echo ""
echo "  Link a sub-project under its parent:"
echo "    python3 ~/.claude/hooks/token_tracker.py --set-parent /path/to/parent"
