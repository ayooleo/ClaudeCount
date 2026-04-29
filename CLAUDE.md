# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## What This Is

ClaudeCount is a Claude Code plugin that displays real-time token usage and API costs in the status bar. It integrates via Claude Code's hook system — no build step, no external dependencies, pure Python 3 stdlib + bash.

## Common Commands

```bash
# Syntax-check the main tracker after editing
python3 -c "import ast; ast.parse(open('src/token_tracker.py').read())" && echo OK

# Test report generation against live data
python3 src/token_report.py
python3 src/token_report.py --all -v

# Manually deploy src/ files to the hooks dir (simulate install)
cp src/token_tracker.py ~/.claude/hooks/token_tracker.py
cp src/token_status.sh ~/.claude/hooks/token_status.sh
cp src/token_report.py ~/.claude/hooks/token_report.py

# Run the tracker manually in main mode (requires a real transcript path)
python3 src/token_tracker.py < <(echo '{"transcript_path":"/path/to/transcript.jsonl","session_id":"abc","hook_event_name":"Stop","cwd":"/tmp"}')

# Recompute missing turn_count / active_minutes for legacy sessions (idempotent)
python3 src/token_tracker.py --backfill

# Adopt sessions that pre-date ClaudeCount: scan ~/.claude/projects/<encoded-cwd>
# for the current dir (or a passed path) and import any session not yet on file.
python3 src/token_tracker.py --import
python3 src/token_tracker.py --import /path/to/other/project

# Mark current project as a sub-project of another (single-level only).
# Pre-creates an empty child project file if needed. User-driven, never auto.
python3 src/token_tracker.py --set-parent /path/to/parent
python3 src/token_tracker.py --set-parent /path/to/parent /path/to/child   # explicit
python3 src/token_tracker.py --unset-parent                                # remove link

# Merge a sub-project's history into its parent and delete the sub-project record.
# Destructive — preview without --yes, apply with --yes.
python3 src/token_tracker.py --merge-into-parent /path/to/child         # preview
python3 src/token_tracker.py --merge-into-parent /path/to/child --yes   # apply

# Render the status bar manually. --render takes a status_path; live statusLine
# JSON (model.id, context_window.current_usage, ...) is read from stdin.
echo '{"session_id":"sid","cwd":"/tmp","model":{"id":"claude-sonnet-4-6"},"context_window":{"context_window_size":200000,"current_usage":{"input_tokens":1000,"cache_read_input_tokens":50000,"cache_creation_input_tokens":0,"output_tokens":500}}}' \
  | python3 src/token_tracker.py --render ~/.claude/token_usage/status/current.json
```

## Architecture

The plugin has three entry points, all backed by `src/token_tracker.py`:

| Entry Point | Hook | Mode | Trigger |
|---|---|---|---|
| `token_tracker.py` (no args) | Stop (async) | `main()` | After every Claude response |
| `token_tracker.py --session-start` | SessionStart (sync) | `session_start_mode()` | New session open |
| `token_tracker.py --pre-turn` | UserPromptSubmit (sync) | `pre_turn_mode()` | Each user message |
| `token_tracker.py --render <json>` | Status bar | `render_mode()` | Every 30s |
| `token_tracker.py --backfill` | — | `backfill_mode()` | One-shot CLI — recompute missing `turn_count` / `active_minutes` for legacy sessions by re-reading their transcripts. Idempotent. |
| `token_tracker.py --import [path]` | — | `import_mode()` | One-shot CLI — adopt sessions that pre-date ClaudeCount: scan `~/.claude/projects/<encoded-cwd>/*.jsonl` and reconstruct any session not already in `projects/{pid}.json`. Encoded form is `cwd.replace("/", "-")`. Idempotent. Path argument optional, defaults to cwd. Imported sessions get `"imported": true`. |
| `token_tracker.py --set-parent <parent> [child]` | — | `set_parent_mode()` | One-shot CLI — mark `child` (defaults to cwd) as a sub-project of `parent`. Pre-creates an empty child project file if it doesn't exist yet. Single-level only (a parent can't itself have a parent — the call refuses). Idempotent. Writes `parent_pid` + cached `parent_name` into both `projects/<child_pid>.json` and `status/<child_pid>.json`. |
| `token_tracker.py --unset-parent [child]` | — | `unset_parent_mode()` | One-shot CLI — remove the parent link from `child` (defaults to cwd). |
| `token_tracker.py --merge-into-parent [child] [--yes]` | — | `merge_into_parent_mode()` | One-shot CLI — **destructive**: absorb `child`'s sessions into its parent, recompute parent totals, delete the child's project + status files. Without `--yes` prints a preview and exits. Refuses if the child carries a `legacy` block (combining legacies is non-trivial, left to the user). Sessions tagged `merged_from: <child_name>` for audit. |
| `token_status.sh` | Status bar wrapper | — | Routes to per-project `status/<pid>.json` based on stdin's cwd |

**Data flow (Stop hook):**
1. Claude Code sends JSON to stdin: `{"transcript_path": "...", "session_id": "...", "cwd": "..."}`
2. `main()` reads the JSONL transcript, deduplicates API calls (multi-block responses share identical usage tuples), counts human turns (excluding tool-result messages)
3. Calculates per-turn, per-session, and per-project costs using model-specific pricing
4. Writes to `~/.claude/token_usage/projects/{pid}.json` (persistent history) and `~/.claude/token_usage/status/{pid}.json` + `current.json` (live display)

**Data flow (Status bar):**
1. Claude Code invokes `token_status.sh`, passing live session data on stdin (`workspace.current_dir`, `model.id`, `context_window.current_usage`, etc.)
2. Shell script extracts cwd from stdin, computes `pid = md5(cwd)[:12]`, and routes to `status/<pid>.json` — **not** the shared `current.json`. This isolates simultaneous Claude Code instances in different projects so they don't overwrite each other's display state. `current.json` is used only as a fallback when the per-project file doesn't exist yet (very first render before SessionStart fires).
3. Calls `token_tracker.py --render <pid>.json` with stdin pass-through; tracker merges live model/context window data and outputs the ANSI-colored status line

## Key Design Decisions

**Project identity:** MD5 hash of `cwd` (first 12 hex chars). Changing the working directory path creates a new project.

**Deduplication:** Multi-block Claude responses generate multiple assistant entries in the transcript with identical `(input, output, cache_read, cache_creation)` tuples. Consecutive duplicates are dropped before cost calculation.

**Human turn counting:** `is_human_message()` returns False for (a) messages with a `tool_result` block, and (b) slash-command / local-command markers (`<command-name>`, `<command-message>`, `<command-args>`, `<local-command-stdout>`, `<local-command-caveat>`). Marker detection covers both top-level string content *and* `{"type":"text","text":"<…>"}` blocks inside list-of-blocks payloads — Claude Code uses both shapes.

**Active time:** Inter-message gaps >3 minutes are excluded (treated as idle). Gaps ≤3 min accumulate into `active_minutes`.

**Cache pricing:** Two tiers — 5-minute ephemeral (1.25× input rate) and 1-hour ephemeral (2× input rate). The API's `usage.cache_creation` object carries the per-tier breakdown (`ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`); `calc_cost` falls back to treating the whole `cache_creation_input_tokens` as 5m when the breakdown is absent. Cache TTL is server-side wall clock and never interacts with our active-time accounting.

**Model/context window:** Live `context_window_size` from Claude Code's status-line stdin is authoritative; `MODELS[k]["context"]` is fallback only. This lets users opt into 1M variants (e.g. `claude-sonnet-4-6` 1M) without ClaudeCount knowing about each variant. Mid-session `/model` switches transiently mismatch model+window for one render cycle (≤30s) and self-heal.

**Context %:** Recomputed locally from `context_window.current_usage` over the live window size, using the input-only formula Claude Code documents: `(input + cache_creation + cache_read) / size`. Output tokens are excluded.

**Timestamps:** All `started` / `created` / `updated` / `last_updated` fields written by the tracker are UTC-aware ISO 8601 (`datetime.now(timezone.utc).isoformat()`, suffix `+00:00`). Older legacy entries written before this change are naive local time; readers (`calc_active_minutes`, `[:10]` date slicing) tolerate both.

**Legacy data:** A project file may carry an optional top-level `legacy` block that
folds pre-ClaudeCount usage (or any other historical estimate) into the totals. Shape:

```json
"legacy": {
  "note": "...",                          // free-form, shown under the Legacy line
  "tokens": {                             // any subset; missing keys treated as 0
    "input_tokens": 0, "output_tokens": 0,
    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0
  },
  "cost_usd": 111.20,                     // added directly to project_total_cost
  "session_count": 4,                     // or len(session_ids); fallback chain in that order
  "session_ids": [...],                   // optional, audit only
  "active_minutes": 0,                    // optional; missing = 0
  "turn_count": 0,                        // optional; missing = 0
  "active_days": 3,                       // optional integer; ignored if period_*  present
  "period_start": "2026-04-21T...",       // optional ISO timestamp; with period_end,
  "period_end":   "2026-04-24T...",       // each calendar day in the range is unioned
                                          //   with session-derived dates (deduplicates
                                          //   if a legacy day overlaps a real session day)
  "repriced_as": "claude-sonnet-4-6",     // optional metadata
  "pricing_basis": "...",
  "recorded_at": "..."
}
```

`recompute_project_totals(project)` is the single source of truth — it rebuilds
`project_total_*` / `session_count` / `active_days` / `project_active_minutes` /
`project_total_turns` from `sessions`, then adds the `legacy` contribution. It is
idempotent and called from both `main()` (Stop hook) and `backfill_mode`.
`token_report` also calls it at render time so the in-memory view is consistent
even if the on-disk totals predate the legacy block.

For backward compatibility, an older `benchmark` field with the same shape is
auto-migrated to `legacy` on first read by `_legacy_view`. The original
`benchmark` is kept as audit redundancy — drop it manually if no longer needed.

The report shows `+legacy` next to `Total cost` and an extra `Legacy:` line with
the cost / session count / period and any `note`.

**Status bar token notation:** `↑<total_in> ↓<out> [<cache_read> «]` — `↑` and `↓` are prefixes, `«` (rewind) is a suffix on the cache-read portion of `↑` with a leading space. `↑` is the *sum* of raw input + cache_creation + cache_read; the `«` segment is the slice of `↑` billed at the cheap 0.1× cache_read rate. This visual convention is intentional — don't move arrows or pick "fast-forward"-style symbols for cache_read; "replayed from past context" is the correct mental model.

**Status bar header emojis:** `🌡️ <ctx%>` is context-window pressure (high = bad: red ≥90, yellow ≥75, blue ≥50, else green). `🎯 <hit%>` is session cache hit rate, defined as `cache_read / (input + cache_creation + cache_read)`. Color tiers are *inverted* from context (high = good: green ≥90, blue ≥75, yellow ≥50, else orange). Hidden when the session has no input yet, so a fresh session doesn't show a misleading `0%`. Granularity is per-session by design — per-turn fluctuates too much (turn 1 is always 0%) and per-project converges and loses signal.

**Parent / sub-project model:** Project IDs are still raw `md5(cwd)[:12]` — entering a subdirectory **always** creates a fresh project (deliberate, no auto-detection). Users opt into a parent / child relationship via `--set-parent`. Single-level only (no chains): if A is a child, A cannot be a parent. The link is stored on the *child*'s project file as `parent_pid` + `parent_name` (cached so renaming is cosmetic-only, refreshed at each session reset). The parent's children list is built on demand by scanning `projects/*.json` for matching `parent_pid` — no double-write.

Status / report rules:
- **Parent's status-bar renders multiple lines** when it has at least one child with `cost > 0`. Line 1 is the full parent header (🌡️ 🎯 present) + Turn / Sess / Proj (family aggregate). Each subsequent line is a compact child row: `  › ChildName  Sub: $cost (↑↓«, N sess, M turns, T time)` — no 🌡️/🎯 (those are per-session metrics for the active parent session, meaningless for the child's historical aggregate). Children with `cost == 0` are hidden. Sorted by cost descending.
- **Parent's status-bar `Proj` segment** shows the *family aggregate* (parent's own totals + every child's totals: cost, tokens, sessions, turns, active minutes). Aggregation happens at write-time in the Stop hook so render stays a fast read; a child's Stop hook also calls `_refresh_parent_status()` so the parent's bar reflects fresh family numbers without waiting for the parent to fire its own Stop. `--set-parent` and `--unset-parent` also trigger a re-aggregate.
- **`token_status.sh` pid routing** uses `d.get("cwd")` (session startup directory, fixed) before `workspace.current_dir` (may track shell cwd). This guarantees the status bar always identifies with the project where Claude Code was launched, even if `workspace.current_dir` drifts.
- **Child's status-bar segment is relabelled `Sub`** (instead of `Proj`) so the column reads as "this sub-project's own usage" rather than "this whole project's usage". Header reads `{gray}parent{R} › {bold-orange}CHILD` (U+203A breadcrumb chevron).
- **`token_report` parent block** keeps `Total cost` as the parent's own cost; lists `Sub-projects:` filtering out children with `project_total_cost == 0` (zero-spend children add noise without information); ends with `Project total : <family> (parent + N sub-project[s])` where `N` is the visible count. Family-total math itself includes all children — zeros contribute zero, so visible vs. all is mathematically identical. **Sess and Turn segments are NEVER family-aggregated** — by definition you're in one session in one project at a time, so those stay per-project.

## Storage Layout

```
~/.claude/token_usage/
  projects/{pid}.json     # Full session history, project totals, active_days
  status/{pid}.json       # Current display state for this project
  status/current.json     # Mirror of the active project's status file
  config.json             # Optional global price overrides
```

Per-project price overrides live in `.claude/claudecount.json` in the project root.

## Adding a New Model

In `src/token_tracker.py`, add **one** entry to the `MODELS` dict:

```python
"claude-NEW-id": {
    "input":          ...,  # USD per million tokens
    "output":         ...,
    "cache_write_5m": ...,  # 1.25× input
    "cache_write_1h": ...,  # 2×    input
    "cache_read":     ...,  # 0.1×  input
    "context":        ...,  # max context window in tokens
    "name":           "Display Name",
},
```

`_resolve_model()` matches `key in model` with keys sorted longest-first, so a future `claude-opus-5-3` won't accidentally collide with `claude-opus-5`, and a variant suffix like `claude-opus-4-7[1m]` resolves to the `claude-opus-4-7` entry.

**Always verify rates against the official pricing page before adding:** [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing). The defaults shipped today were verified there on 2026-04-26.
