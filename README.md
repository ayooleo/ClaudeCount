# ClaudeCount

**English** | [简体中文](./README.zh-CN.md)

[![Version](https://img.shields.io/badge/version-1.1.0-blue.svg)](#changelog)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Real-time token usage and cost tracking for Claude Code (Terminal), shown directly in the status bar.

```
MYPROJECT Sonnet 4.6 200k 🌡️ 22% 🎯 87% │ Turn: $0.03 (↑180k ↓450 179k «) │ Sess: $0.18 (↑180k ↓450 179k «, 5 turns, 12 min) │ Proj: $2.40 (↑18M ↓62k 17M «, 5 sess, 40 turns, 3hr)
```

| Segment | Meaning |
|---------|---------|
| **PROJECT NAME** | Active project (directory name, uppercased) |
| **Model** | Current model name and context window size |
| **🌡️ %** | Context window fill — green < 50%, blue 50–74%, yellow 75–89%, red ≥ 90% |
| **🎯 %** | Session cache hit rate (`cache_read` / total input) — orange < 50%, yellow 50–74%, blue 75–89%, green ≥ 90%; hidden until the session has any input |
| **Turn** | Cost + tokens for the last completed turn (all API calls summed) |
| **Sess** | Cumulative cost, tokens, turn count, and active time for this session |
| **Proj** | All-time cost, tokens, session count, total turns, and active hours |

### Token notation

| Symbol | Meaning |
|--------|---------|
| `↑` | Total input tokens (non-cached + cache_creation + cache_read) |
| `↓` | Output tokens generated |
| `«` | Portion of `↑` served from cache (0.1× input rate) |

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/ayooleo/ClaudeCount/main/install.sh | bash
```

Restart Claude Code after installing.

**Requirements:** Python 3, curl, Claude Code with status bar support.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/ayooleo/ClaudeCount/main/uninstall.sh | bash
```

Usage data at `~/.claude/token_usage/` is kept. Delete it manually for a clean removal.

## Detailed reports

```bash
# Current project (verbose session breakdown)
python3 ~/.claude/hooks/token_report.py

# All projects, sorted by cost
python3 ~/.claude/hooks/token_report.py --all

# All projects + session-level detail
python3 ~/.claude/hooks/token_report.py --all -v
```

## Adopting an older project

If you installed ClaudeCount on a project that already has a Claude Code history, you can import past sessions in one shot — Claude Code stores every transcript at `~/.claude/projects/<encoded-cwd>/*.jsonl`, so cost, tokens, turn count, and active time can all be reconstructed from disk:

```bash
# From the project directory
python3 ~/.claude/hooks/token_tracker.py --import

# Or point at any project path
python3 ~/.claude/hooks/token_tracker.py --import /path/to/project
```

Idempotent — sessions already on file are skipped, so you can re-run safely. Imported sessions are tagged `"imported": true` in `~/.claude/token_usage/projects/{pid}.json`.

## Parent / sub-projects

Each working directory is its own project — opening Claude Code inside a subfolder creates a separate counter on purpose, never automatically. If you want a subfolder rolled up under its parent (e.g. `myrepo/web` and `myrepo/server` both contributing to `myrepo`'s total), opt in with `--set-parent`:

```bash
# From the sub-project directory
python3 ~/.claude/hooks/token_tracker.py --set-parent /path/to/parent

# Or point at both ends explicitly
python3 ~/.claude/hooks/token_tracker.py --set-parent /path/to/parent /path/to/child

# Remove the link (sub-project becomes independent again, history kept)
python3 ~/.claude/hooks/token_tracker.py --unset-parent

# Merge the sub-project's history into the parent and delete the sub-project
# record. Destructive — runs a preview by default; pass --yes to actually apply.
python3 ~/.claude/hooks/token_tracker.py --merge-into-parent /path/to/child
python3 ~/.claude/hooks/token_tracker.py --merge-into-parent /path/to/child --yes
```

Single-level only — a parent can't itself have a parent. Once linked:

- **When opened in the parent directory**, the status bar becomes multi-line: the parent's full stats (🌡️ context %, 🎯 cache hit, Turn, Sess, Proj family aggregate) occupy line 1; each child with non-zero spend gets its own compact row below (`  › Name  Sub: $cost (tokens, sessions, turns, time)`), sorted by cost. Children with $0.00 stay hidden. **When opened in a sub-project directory**, the status bar remains a single line showing the sub-project's own stats
- The sub-project's status bar header reads `parent › CHILD`; its third segment is relabelled `Sub:` (instead of `Proj:`) and shows the sub-project's own totals
- The parent's status bar `Proj:` segment becomes a **family aggregate** — its cost, tokens, session count, turns and active time are summed across the parent and every child. Updated automatically: a child's Stop hook also refreshes the parent's status, so the parent's bar reflects the latest family numbers without waiting for its own Stop
- `Sess:` and `Turn:` are never aggregated — you can only be in one session at a time
- `token_report` on the parent gets a `Sub-projects:` block (children with $0.00 are hidden as noise), followed by `Project total:` summing the parent and all children
- The sub-project's `token_report` block shows a `Parent:` line at the top; its `Total cost` is its own
- Sub-projects still appear as independent top-level entries — nothing is hidden, just rolled up

## Custom pricing

Create `.claude/claudecount.json` in the project root to override pricing:

```json
{
  "pricing": {
    "input": 3.00,
    "output": 15.00,
    "cache_write_5m": 3.75,
    "cache_write_1h": 6.00,
    "cache_read": 0.30
  }
}
```

To disable tracking for a specific project:

```json
{ "enabled": false }
```

A global config can also be placed at `~/.claude/token_usage/config.json`.

## How it works

Three hooks cooperate to keep the display accurate:

**SessionStart hook** (synchronous) fires the moment a session opens:
- Resets Turn, Sess, and context window display before the first prompt, so the status bar never shows stale data from a previous session
- Detects new vs. resumed sessions by comparing session IDs — resumes are left untouched

**UserPromptSubmit hook** (synchronous) fires when you send a message:
- Acts as a fallback reset in case SessionStart didn't fire (e.g. older Claude Code versions)

**Stop hook** (async) fires after each Claude response:
- Reads the session transcript and deduplicates API calls (one response can span multiple transcript entries)
- Counts real human turns (tool-result messages are excluded)
- Calculates Turn, Sess, and Proj costs and token totals
- Writes `~/.claude/token_usage/status/current.json`

**Status bar** runs every 30 seconds via `token_status.sh`:
- Receives live model ID and `context_window.current_usage` from Claude Code on stdin
- Model name updates immediately when you switch models with `/model`
- Context window **size** is taken from Claude Code's live `context_window_size` (so 1M variants opt-ins are picked up correctly); the built-in model table is a fallback only
- Context window **percentage** is recomputed locally from `current_usage` over the live window size, using the same input-only formula Claude Code uses (`input + cache_creation + cache_read`, output excluded)

**Active time** counts the total time when human and AI are collaborating: it sums all inter-message gaps (both sides) up to 3 minutes. Gaps longer than 3 minutes — idle pauses, away-from-screen periods, unanswered permission prompts — are excluded.

## Pricing reference (defaults)

Cache write has two tiers: **5-minute** (1.25× input) and **1-hour** (2× input).

| Model | Input | Output | Cache write 5m | Cache write 1h | Cache read |
|-------|-------|--------|----------------|----------------|------------|
| Opus 4.7 / 4.6 / 4.5 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| Opus 4.1 / 4 | $15.00 | $75.00 | $18.75 | $30.00 | $1.50 |
| Sonnet 4.6 / 4.5 / 4 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| Haiku 4.5 | $1.00 | $5.00 | $1.25 | $2.00 | $0.10 |
| Sonnet 3.7 / 3.5 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| Haiku 3.5 | $0.80 | $4.00 | $1.00 | $1.60 | $0.08 |
| Opus 3 | $15.00 | $75.00 | $18.75 | $30.00 | $1.50 |
| Haiku 3 | $0.25 | $1.25 | $0.30 | $0.50 | $0.03 |

Prices are per million tokens. **Opus 4.7 / 4.6** and **Sonnet 4.6** support a 1M-token context window at standard rates; all other models default to 200K. For authoritative billing check the [Claude Console usage page](https://platform.claude.com/usage).

## Data location

```
~/.claude/token_usage/
├── projects/   # per-project history (one JSON per project)
└── status/     # live status snapshots (current.json read by status bar)
```

## Changelog

This project follows [Semantic Versioning 2.0](https://semver.org/) and the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

### [1.1.0] — 2026-04-29

**Added**
- `🎯` session cache hit rate indicator in the status-bar header (`cache_read / total_input`), with industry-standard color tiers (green ≥90% / blue ≥75% / yellow ≥50% / orange <50%)
- `🌡️` context-window pressure indicator (replaces the previous symbol; emoji and number now separated by a space)
- `--import` mode: adopt sessions that pre-date ClaudeCount by reconstructing them from on-disk Claude Code transcripts
- `--set-parent` / `--unset-parent`: opt-in parent / sub-project linking. Sub-projects render as `parent › CHILD` in the status bar header and relabel the third segment as `Sub:` (own usage). The parent's `Proj:` segment becomes a *family aggregate* (parent + every child's cost / tokens / sessions / turns / active time), refreshed automatically when any child's Stop hook fires. `token_report` lists each visible (non-zero-spend) child and shows a `Project total:` rollup
- `--merge-into-parent [child] [--yes]`: absorb a sub-project's sessions into its parent and delete the sub-project record. Destructive — runs a preview by default; sessions are tagged `merged_from: <child_name>` for audit. Refuses if the child carries a `legacy` block (manual consolidation needed)
- Per-project routing in `token_status.sh`: simultaneous Claude Code instances in different projects now show their own data instead of fighting over a shared `current.json`
- `--version` / `-V` flag

**Changed**
- Cache hit rate granularity is per-session (per-turn fluctuates too much; per-project converges and loses signal)
- Status-bar header layout: emoji and value separated by a space (`🌡️ 35%` instead of `🌡️35%`)
- Sub-project separator in status bar uses the U+203A breadcrumb chevron (`›`) instead of `/`

**Fixed**
- `current.json` cross-project pollution that caused two simultaneous Claude Code sessions to display each other's session totals
- Cache-creation pricing now correctly handles the `ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` tier breakdown when present (verified against 289 real assistant usage entries)
- `token_report.py` with no arguments now shows only the current project (matching the documented behavior); pass `--all` to see every project. Previously the no-arg path silently fell through to `--all`, burying the current project's `Sub-projects:` rollup beneath higher-cost siblings
- Status bar in parent project now renders multiple lines (parent + compact child rows) when children have non-zero spend; `token_status.sh` pid routing now prefers `cwd` (session startup directory) over `workspace.current_dir` to prevent project identity drift

### [1.0.0] — 2026-04-26

Initial release.

## License

MIT — see [LICENSE](LICENSE).
