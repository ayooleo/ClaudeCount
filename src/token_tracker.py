#!/usr/bin/env python3
"""ClaudeCount — token usage and cost tracker for Claude Code."""

import json
import sys
import os
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

__version__ = "1.1.0"

BASE_DIR   = Path.home() / ".claude" / "token_usage"
STATUS_DIR = BASE_DIR / "status"
DATA_DIR   = BASE_DIR / "projects"


def _read_hook_input():
    """Parse hook stdin JSON. Returns dict or None on missing/invalid input."""
    raw = sys.stdin.read().strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _is_user(msg: dict) -> bool:
    return ((msg.get("role") or msg.get("type")) == "user"
            or msg.get("message", {}).get("role") == "user")


def _is_assistant(msg: dict) -> bool:
    return ((msg.get("role") or msg.get("type")) == "assistant"
            or msg.get("message", {}).get("role") == "assistant")

# Per-million-token rates and per-model metadata. Verified 2026-04-29 against
# https://platform.claude.com/docs/en/about-claude/models/overview and
# https://platform.claude.com/docs/en/about-claude/pricing.
#   cache_write_5m = ephemeral 5-min tier (1.25× input)
#   cache_write_1h = ephemeral 1-hr tier   (2×    input)
#   cache_read     =                       (0.1×  input)
# 1M context: Opus 4.7 / 4.6, Sonnet 4.6. All other models 200K.
MODELS = {
    # Claude 4 family
    "claude-opus-4-7":   {"input":  5.00, "output": 25.00, "cache_write_5m":  6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "context": 1_000_000, "name": "Opus 4.7"},
    "claude-opus-4-6":   {"input":  5.00, "output": 25.00, "cache_write_5m":  6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "context": 1_000_000, "name": "Opus 4.6"},
    "claude-opus-4-5":   {"input":  5.00, "output": 25.00, "cache_write_5m":  6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "context":   200_000, "name": "Opus 4.5"},
    "claude-opus-4-1":   {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read": 1.50, "context":   200_000, "name": "Opus 4.1"},
    "claude-opus-4":     {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read": 1.50, "context":   200_000, "name": "Opus 4"},
    "claude-sonnet-4-6": {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context": 1_000_000, "name": "Sonnet 4.6"},
    "claude-sonnet-4-5": {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 4.5"},
    "claude-sonnet-4":   {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 4"},
    "claude-haiku-4-5":  {"input":  1.00, "output":  5.00, "cache_write_5m":  1.25, "cache_write_1h":  2.00, "cache_read": 0.10, "context":   200_000, "name": "Haiku 4.5"},
    # Claude 3.x family — kept for historical session pricing.
    # 3.7-sonnet deprecated; 3.5-sonnet / 3-sonnet no longer on official model list
    # but retained here so old transcripts reprice correctly.
    "claude-3-7-sonnet": {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 3.7"},
    "claude-3-5-sonnet": {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 3.5"},
    "claude-3-5-haiku":  {"input":  0.80, "output":  4.00, "cache_write_5m":  1.00, "cache_write_1h":  1.60, "cache_read": 0.08, "context":   200_000, "name": "Haiku 3.5"},
    "claude-3-opus":     {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read": 1.50, "context":   200_000, "name": "Opus 3"},
    "claude-3-sonnet":   {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 3"},
    "claude-3-haiku":    {"input":  0.25, "output":  1.25, "cache_write_5m":  0.30, "cache_write_1h":  0.50, "cache_read": 0.03, "context":   200_000, "name": "Haiku 3"},
}

_DEFAULT_MODEL = {
    "input": 3.00, "output": 15.00,
    "cache_write_5m": 3.75, "cache_write_1h": 6.00, "cache_read": 0.30,
    "context": 200_000, "name": "",
}

# Cached order: longest key first so e.g. "claude-opus-4-7" wins over "claude-opus-4".
_MODEL_KEYS_BY_LEN = sorted(MODELS, key=len, reverse=True)


def _resolve_model(model: str) -> dict:
    """Find the most specific MODELS entry whose key is a substring of `model`.
    Handles variant suffixes like `claude-opus-4-7[1m]`."""
    if not model:
        return _DEFAULT_MODEL
    for key in _MODEL_KEYS_BY_LEN:
        if key in model:
            return MODELS[key]
    return _DEFAULT_MODEL


def fmt_model(model: str) -> str:
    if not model:
        return ""
    m = _resolve_model(model)
    if m is not _DEFAULT_MODEL:
        return m["name"]
    return model.replace("claude-", "")[:15]


_PRICE_KEYS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")


def get_pricing(model: str, overrides: dict = None) -> dict:
    m = _resolve_model(model)
    base = {k: m[k] for k in _PRICE_KEYS}
    if not overrides:
        return base
    p = {**base, **overrides}
    # Back-compat: if caller provides old "cache_write" key, split into tiers
    if "cache_write" in overrides and "cache_write_5m" not in overrides:
        p["cache_write_5m"] = overrides["cache_write"]
        p["cache_write_1h"] = overrides["cache_write"] * 1.6
    return p


def get_context_window(model: str) -> int:
    """Last-resort lookup. Live runtime data (persisted by render_mode) is preferred."""
    return _resolve_model(model)["context"]


def calc_cost(usage: dict, model: str, price_override: dict = None) -> float:
    p = get_pricing(model, price_override)
    # Two-tier cache write: prefer detailed breakdown when available
    cc = usage.get("cache_creation", {})
    tokens_5m = cc.get("ephemeral_5m_input_tokens", 0)
    tokens_1h  = cc.get("ephemeral_1h_input_tokens", 0)
    if not (tokens_5m or tokens_1h):
        # Fallback: treat whole cache_creation as 5m tier (conservative estimate)
        tokens_5m = usage.get("cache_creation_input_tokens", 0)
    return (
        usage.get("input_tokens", 0)            / 1e6 * p["input"]
        + usage.get("output_tokens", 0)         / 1e6 * p["output"]
        + tokens_5m                             / 1e6 * p["cache_write_5m"]
        + tokens_1h                             / 1e6 * p["cache_write_1h"]
        + usage.get("cache_read_input_tokens", 0) / 1e6 * p["cache_read"]
    )


def project_id(cwd: str) -> str:
    return hashlib.md5(cwd.encode()).hexdigest()[:12]


def read_transcript(path: str) -> list:
    msgs = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        msgs.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except (OSError, IOError):
        pass
    return msgs


def extract_usage_from_msg(msg: dict) -> tuple:
    inner = msg.get("message", {})
    usage = inner.get("usage") or msg.get("usage") or {}
    model = inner.get("model") or msg.get("model") or ""
    return usage, model


def get_session_start(messages: list) -> str:
    for msg in messages:
        ts = msg.get("timestamp") or msg.get("created_at") or ""
        if ts:
            return ts
    return ""


# Slash-command and local-command wrappers that Claude Code emits as user-role messages.
# They are not real human prompts: e.g. /init produces a <command-message> marker plus the
# actual expanded prompt; /model produces only markers (no Claude turn at all).
_COMMAND_TAG_PREFIXES = (
    "<command-name>", "<command-message>", "<command-args>",
    "<local-command-stdout>", "<local-command-caveat>",
)


def _starts_with_command_tag(text) -> bool:
    return isinstance(text, str) and text.lstrip().startswith(_COMMAND_TAG_PREFIXES)


def is_human_message(msg: dict) -> bool:
    """True if this user message is a real human prompt sent to Claude.

    Filters out (a) tool results fed back to Claude, and (b) slash-command markers /
    local-command stdout/caveat strings emitted by Claude Code's UI layer — whether
    delivered as a top-level string or as a {"type":"text","text":"<command-…>"} block
    inside a list-of-blocks content payload.
    """
    for content in (msg.get("content"), msg.get("message", {}).get("content")):
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_result":
                    return False
                if item.get("type") == "text" and _starts_with_command_tag(item.get("text")):
                    return False
        elif _starts_with_command_tag(content):
            return False
    return True


def _usage_key(usage: dict) -> tuple:
    return (
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("cache_read_input_tokens", 0),
        usage.get("cache_creation_input_tokens", 0),
    )


def deduplicate_api_calls(pairs: list) -> list:
    """Remove consecutive assistant messages with identical usage (same API call, multiple content blocks)."""
    result = []
    for usage, model in pairs:
        if result and _usage_key(usage) == _usage_key(result[-1][0]):
            continue
        result.append((usage, model))
    return result


def count_turns(messages: list) -> int:
    """Count TUI turns = number of real human messages in the transcript."""
    return sum(1 for m in messages if _is_user(m) and is_human_message(m))


def _collect_assistant_usages(messages) -> list:
    results = []
    for msg in messages:
        if not _is_assistant(msg):
            continue
        usage, model = extract_usage_from_msg(msg)
        if usage and (usage.get("output_tokens", 0) > 0 or usage.get("input_tokens", 0) > 0):
            results.append((usage, model))
    return deduplicate_api_calls(results)


def get_last_turn_usages(messages: list) -> list:
    """Return usages for all assistant messages in the last TUI turn (since last human message)."""
    last_human_idx = -1
    for i, msg in enumerate(messages):
        if _is_user(msg) and is_human_message(msg):
            last_human_idx = i
    source = messages[last_human_idx + 1:] if last_human_idx >= 0 else messages
    return _collect_assistant_usages(source)


def get_all_assistant_usages(messages: list) -> list:
    return _collect_assistant_usages(messages)


def sum_usages(usages: list, price_override: dict = None) -> tuple:
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    total_cost = 0.0
    last_model = ""
    for usage, model in usages:
        for key in total:
            total[key] += usage.get(key, 0)
        total_cost += calc_cost(usage, model, price_override)
        if model:
            last_model = model
    return total, total_cost, last_model


def calc_active_minutes(messages: list, idle_threshold_min: int = 3) -> int:
    """Sum inter-message gaps; gaps beyond threshold are excluded as idle.

    Counts both human and AI messages so time spent watching Claude work is included.
    Appends a "now" sentinel so activity since the last logged message is captured —
    this matters because Stop fires before the final assistant text is flushed to the
    transcript. For after-the-fact reports the now-gap exceeds the threshold and is
    excluded naturally, so adding the sentinel is safe in both paths.
    """
    timestamps = []
    for msg in messages:
        ts = msg.get("timestamp") or msg.get("created_at") or ""
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                timestamps.append(dt)
            except Exception:
                pass

    if not timestamps:
        return 0

    timestamps.sort()
    timestamps.append(datetime.now(timezone.utc))
    threshold_s = timedelta(minutes=idle_threshold_min).total_seconds()

    active_seconds = 0.0
    for i in range(1, len(timestamps)):
        gap = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if gap <= threshold_s:
            active_seconds += gap
    return max(0, round(active_seconds / 60))


def load_project_data(data_dir: Path, pid: str) -> dict:
    f = data_dir / f"{pid}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"pid": pid, "sessions": {}}


def save_project_data(data_dir: Path, pid: str, data: dict):
    f = data_dir / f"{pid}.json"
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _legacy_view(project: dict) -> dict:
    """Return project['legacy'], migrating an old `benchmark` field on first read.
    The `benchmark` field is kept alongside as audit redundancy — users can drop it manually."""
    legacy = project.get("legacy")
    if legacy is None:
        bench = project.get("benchmark")
        if isinstance(bench, dict):
            legacy = dict(bench)
            project["legacy"] = legacy
    return legacy or {}


def _legacy_period_dates(legacy: dict) -> set:
    """Inclusive ISO-date set for legacy.period_start..period_end, or empty if missing/malformed."""
    ps, pe = legacy.get("period_start"), legacy.get("period_end")
    if not (ps and pe):
        return set()
    try:
        d1 = datetime.fromisoformat(ps.replace("Z", "+00:00")).date()
        d2 = datetime.fromisoformat(pe.replace("Z", "+00:00")).date()
    except Exception:
        return set()
    out = set()
    cur = d1
    while cur <= d2:
        out.add(cur.isoformat())
        cur += timedelta(days=1)
    return out


def recompute_project_totals(project: dict) -> None:
    """Recompute project_total_* / session_count / active_days / etc. from sessions,
    then merge `legacy` (if present). Idempotent — safe to call any time."""
    all_sessions = list(project.get("sessions", {}).values())
    project["project_total_cost"] = sum(s.get("cost", 0) for s in all_sessions)
    project["project_total_tokens"] = {
        k: sum(s.get("tokens", {}).get(k, 0) for s in all_sessions)
        for k in ("input_tokens", "output_tokens",
                  "cache_creation_input_tokens", "cache_read_input_tokens")
    }
    project["session_count"] = len(all_sessions)
    session_dates = {
        (s.get("started") or s.get("updated", ""))[:10]
        for s in all_sessions
        if (s.get("started") or s.get("updated", ""))
    }
    project["active_days"] = len(session_dates)
    project["project_active_minutes"] = sum(s.get("active_minutes", 0) for s in all_sessions)
    project["project_total_turns"] = sum(s.get("turn_count", 0) for s in all_sessions)

    legacy = _legacy_view(project)
    if not legacy:
        return

    project["project_total_cost"] += float(legacy.get("cost_usd", 0))
    ltoks = legacy.get("tokens", {})
    for k in project["project_total_tokens"]:
        project["project_total_tokens"][k] += int(ltoks.get(k, 0))

    project["session_count"] += int(
        legacy.get("session_count") or len(legacy.get("session_ids", []))
    )
    project["project_active_minutes"] += int(legacy.get("active_minutes", 0))
    project["project_total_turns"] += int(legacy.get("turn_count", 0))

    legacy_dates = _legacy_period_dates(legacy)
    if legacy_dates:
        project["active_days"] = len(session_dates | legacy_dates)
    elif "active_days" in legacy:
        project["active_days"] += int(legacy["active_days"])


def load_project_config(cwd: str) -> dict:
    """Load per-project config for custom pricing or disabling tracking."""
    for config_path in [
        Path(cwd) / ".claude" / "claudecount.json",
        Path.home() / ".claude" / "token_usage" / "config.json",
    ]:
        if config_path.exists():
            try:
                return json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


# ANSI color constants for status line rendering
_R    = "\033[0m"
_BOLD = "\033[1m"
_ORG  = "\033[38;5;214m"
_YLW  = "\033[38;5;220m"
_GRN  = "\033[38;5;120m"
_BLU  = "\033[38;5;111m"
_GRY  = "\033[38;5;183m"
_DIM  = "\033[38;5;242m"   # darker neutral gray — secondary / auxiliary stats
_RED  = "\033[38;5;203m"
_SEP  = f"{_GRY} │ {_R}"


def _fmt_cost(c) -> str:
    if c == 0:
        return f"{_YLW}$0.00{_R}"
    return f"{_YLW}${c:.2f}{_R}"


def _fmt_tok(n) -> str:
    if n >= 1_000_000:
        s = f"{n/1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{_GRY}{s}M{_R}"
    if n >= 1_000:
        s = f"{n/1_000:.1f}".rstrip("0").rstrip(".")
        return f"{_GRY}{s}k{_R}"
    return f"{_GRY}{n}{_R}"


def _fmt_win(size) -> str:
    if size >= 1_000_000:
        return f"{int(size/1_000_000)}M"
    if size >= 1_000:
        return f"{int(size/1_000)}k"
    return str(size)


def _parens(*parts) -> str:
    return f"{_GRY}({_R}" + f"{_GRY}, {_R}".join(parts) + f"{_GRY}){_R}"


def _cache_hit_rate(sess: dict):
    """(pct, color) for session-level cache hit rate, or (None, None) if no input.
    Definition matches industry convention: cache_read / (input + cache_creation + cache_read).
    Color tiers are inverted from context-window pressure — high hit rate is *good*."""
    total_in = (sess.get("input_tokens", 0)
                + sess.get("cache_creation", 0)
                + sess.get("cache_read", 0))
    if total_in <= 0:
        return None, None
    pct = round(sess.get("cache_read", 0) / total_in * 100)
    color = (_GRN if pct >= 90 else
             _BLU if pct >= 75 else
             _YLW if pct >= 50 else
             _ORG)
    return pct, color


def _tok_triplet(d: dict) -> str:
    """`↑<total_in> ↓<out>[ <cache_read> «]` — shared by Turn/Sess/Proj segments.
    ↑↓ are prefix; « ('rewind') is suffix on the cache-read portion of ↑ — semantically
    'replayed from past context'."""
    total_in = d.get("input_tokens", 0) + d.get("cache_creation", 0) + d.get("cache_read", 0)
    cache_r  = d.get("cache_read", 0)
    cache_str = f" {_fmt_tok(cache_r)} {_GRY}«{_R}" if cache_r else ""
    return f"{_GRY}↑{_R}{_fmt_tok(total_in)} {_GRY}↓{_R}{_fmt_tok(d.get('output_tokens', 0))}{cache_str}"


def _render_header(status: dict) -> str:
    name = (status.get("project_name") or status.get("cwd", "").split("/")[-1] or "PROJ").upper()
    model = fmt_model(status.get("model", ""))
    parent_name = status.get("parent_name", "")
    if parent_name:
        # Sub-project: dim parent prefix + breadcrumb chevron + bright child name.
        header = f"{_GRY}{parent_name}{_R} {_GRY}›{_R} {_BOLD}{_ORG}{name}{_R}"
    else:
        header = f"{_BOLD}{_ORG}{name}{_R}"
    if not model:
        return header
    ctx          = status.get("context", {})
    ctx_pct      = ctx.get("pct", 0)
    ctx_win_size = ctx.get("window_size", 0)
    win_str = f" {_GRY}{_fmt_win(ctx_win_size)}{_R}" if ctx_win_size else ""
    if ctx_pct:
        c = (_RED if ctx_pct >= 90 else
             _YLW if ctx_pct >= 75 else
             _BLU if ctx_pct >= 50 else
             _GRN)
        cw_str = f" 🌡️ {c}{ctx_pct}%{_R}"
    else:
        cw_str = ""
    hit_pct, hit_color = _cache_hit_rate(status.get("session", {}))
    hit_str = f" 🎯 {hit_color}{hit_pct}%{_R}" if hit_pct is not None else ""
    return header + f" {_GRY}{model}{_R}{win_str}{cw_str}{hit_str}"


def _render_turn_segment(t: dict) -> str:
    return f"{_BLU}Turn{_GRY}: {_R}{_fmt_cost(t.get('cost', 0))} {_parens(_tok_triplet(t))}"


def _render_sess_segment(sess: dict) -> str:
    parts = [_tok_triplet(sess)]
    turns = sess.get("turn_count", 0)
    if turns:
        parts.append(f"{_GRY}{turns} turns{_R}")
    active_min = sess.get("active_minutes")
    if active_min:
        h, m = divmod(int(active_min), 60)
        parts.append(f"{_GRY}{h} hr {m} min{_R}" if h else f"{_GRY}{m} min{_R}")
    return f"{_BLU}Sess{_GRY}: {_R}{_fmt_cost(sess.get('cost', 0))} {_parens(*parts)}"


def _live_family_totals(own: dict, children: list) -> dict:
    """Build a family aggregate proj-segment dict from live child project files.
    Uses ALL children (not just visible) so session/turn counts are complete.
    Zero-cost children contribute 0 to cost but may have sessions/turns."""
    tk = lambda c, k: c.get("project_total_tokens", {}).get(k, 0)
    return {
        "cost":           own.get("cost", 0)           + sum(c.get("project_total_cost", 0) for c in children),
        "session_count":  own.get("session_count", 0)  + sum(c.get("session_count", 0) for c in children),
        "turn_count":     own.get("turn_count", 0)     + sum(c.get("project_total_turns", 0) for c in children),
        "active_minutes": own.get("active_minutes", 0) + sum(c.get("project_active_minutes", 0) for c in children),
        "input_tokens":   own.get("input_tokens", 0)   + sum(tk(c, "input_tokens") for c in children),
        "cache_creation": own.get("cache_creation", 0) + sum(tk(c, "cache_creation_input_tokens") for c in children),
        "cache_read":     own.get("cache_read", 0)     + sum(tk(c, "cache_read_input_tokens") for c in children),
        "output_tokens":  own.get("output_tokens", 0)  + sum(tk(c, "output_tokens") for c in children),
    }


def _render_child_row(child_project: dict) -> str:
    """Compact status-bar row rendered below the parent line.
    Shows only the child's own aggregate — no 🌡️/🎯 (those are per-session
    metrics for the active parent session, not the child)."""
    name = child_project.get("name") or child_project.get("pid", "?")
    pt = child_project.get("project_total_tokens", {})
    proj_dict = {
        "cost": child_project.get("project_total_cost", 0),
        "session_count": child_project.get("session_count", 0),
        "turn_count": child_project.get("project_total_turns", 0),
        "active_minutes": child_project.get("project_active_minutes", 0),
        "input_tokens":   pt.get("input_tokens", 0),
        "cache_creation": pt.get("cache_creation_input_tokens", 0),
        "cache_read":     pt.get("cache_read_input_tokens", 0),
        "output_tokens":  pt.get("output_tokens", 0),
    }
    seg = _render_proj_segment(proj_dict, "Sub")
    return f"  {_GRY}›{_R} {_GRY}{name}{_R}  {seg}"


def _render_proj_segment(proj: dict, label: str = "Proj", extra_parts: list = None) -> str:
    parts = [_tok_triplet(proj), f"{_GRY}{proj.get('session_count', 0)} sess{_R}"]
    proj_turns = proj.get("turn_count", 0)
    if proj_turns:
        parts.append(f"{_GRY}{proj_turns} turns{_R}")
    active_min = int(proj.get("active_minutes") or 0)
    if active_min:
        proj_total_hr = round(active_min / 60)
        if proj_total_hr >= 24:
            d, h = divmod(proj_total_hr, 24)
            parts.append(f"{_GRY}{d}d {h}hr{_R}" if h else f"{_GRY}{d}d{_R}")
        elif proj_total_hr >= 1:
            parts.append(f"{_GRY}{proj_total_hr}hr{_R}")
        else:
            parts.append(f"{_GRY}{active_min} min{_R}")
    if extra_parts:
        parts.extend(extra_parts)
    return f"{_BLU}{label}{_GRY}: {_R}{_fmt_cost(proj.get('cost', 0))} {_parens(*parts)}"


def render_status_line(status: dict) -> str:
    """Render the status bar. Output varies by project role:

    Standalone / child  →  single line (existing behaviour).

    Parent with ≥1 cost>0 child  →  multi-line:
      LINE 1: HEADER 🌡️ 🎯 │ Turn │ Sess │ Self: $own │ Sub: $children │ Total: $family (full params)
      LINE 2+: one compact child row per cost>0 child, sorted by cost desc.

    Self / Sub are cost-only (no token details).
    Total reuses the full Proj segment format (token triplet, sess, turns, time).
    project_own must be present in status for the Self/Sub split; if absent (legacy
    status file not yet refreshed), falls back to the plain Proj label.
    """
    is_child = bool(status.get("parent_name"))
    pid = status.get("pid")

    all_children = []
    visible_children = []
    if not is_child and pid:
        all_children = _scan_children(pid)
        visible_children = sorted(
            [c for c in all_children if c.get("project_total_cost", 0) > 0],
            key=lambda c: c.get("project_total_cost", 0),
            reverse=True,
        )

    header   = _render_header(status)
    turn_seg = _render_turn_segment(status.get("last_turn", {}))
    sess_seg = _render_sess_segment(status.get("session", {}))

    if visible_children and "project_own" in status:
        own = status["project_own"]
        sub_cost   = sum(c.get("project_total_cost", 0) for c in all_children)
        live_total = _live_family_totals(own, all_children)
        # self / sub are metadata appended inside Proj's parenthetical,
        # not separated by │ — they're stats of the same Proj segment.
        extra = [
            f"{_GRY}self ${own.get('cost', 0):.2f}{_R}",
            f"{_GRY}sub ${sub_cost:.2f}{_R}",
        ]
        proj_seg = _render_proj_segment(live_total, "Proj", extra)
        line1 = _SEP.join([header, turn_seg, sess_seg, proj_seg])
        child_lines = [_render_child_row(c) for c in visible_children]
        return line1 + "\n" + "\n".join(child_lines)

    proj_label = "Sub" if is_child else "Proj"
    return _SEP.join([
        header, turn_seg, sess_seg,
        _render_proj_segment(status.get("project", {}), proj_label),
    ])


def _reset_session_state(session_id: str, cwd: str, model_hint: str = ""):
    """Reset turn/sess/context for a new session. Idempotent — only acts on new session_id.
    Used by both SessionStart and UserPromptSubmit hooks. SessionStart's `model` hook field
    seeds the model display before the first status-line render arrives."""
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    pid = project_id(cwd)
    status_file = STATUS_DIR / f"{pid}.json"

    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        status = {}

    if session_id == status.get("session_id", ""):
        return

    # Preserve window_size from live runtime data (written by render_mode from Claude Code stdin).
    live_win_size = status.get("context", {}).get("window_size", 0)

    status["session_id"] = session_id
    status["project_name"] = Path(cwd).name
    status["cwd"] = cwd
    status["pid"] = pid
    status["model"] = model_hint
    status["last_turn"] = {
        "cost": 0, "input_tokens": 0, "cache_creation": 0,
        "cache_read": 0, "output_tokens": 0,
    }
    status["session"] = {
        "cost": 0, "input_tokens": 0, "cache_creation": 0,
        "cache_read": 0, "output_tokens": 0,
        "started": datetime.now(timezone.utc).isoformat(),
        "active_minutes": 0,
        "turn_count": 0,
    }
    status["context"] = {"tokens": 0, "window_size": live_win_size, "pct": 0}

    # Refresh parent_pid / parent_name from project file in case the link
    # changed since this status was last written.
    try:
        project = json.loads((DATA_DIR / f"{pid}.json").read_text(encoding="utf-8"))
        _attach_parent(status, project)
    except Exception:
        pass

    status_json = json.dumps(status, ensure_ascii=False)
    status_file.write_text(status_json, encoding="utf-8")
    (STATUS_DIR / "current.json").write_text(status_json, encoding="utf-8")


def _hook_reset():
    """Shared body for SessionStart and UserPromptSubmit hooks. SessionStart includes
    a `model` field (per Claude Code hooks docs); UserPromptSubmit does not."""
    hook = _read_hook_input()
    if hook is None:
        return
    _reset_session_state(
        hook.get("session_id", "unknown"),
        hook.get("cwd", os.getcwd()),
        hook.get("model", ""),
    )


def session_start_mode():
    """SessionStart hook: reset display the moment a session opens, before any prompt."""
    _hook_reset()


def pre_turn_mode():
    """UserPromptSubmit hook: fallback reset in case SessionStart didn't fire."""
    _hook_reset()


def render_mode(argv: list):
    """StatusLine mode: merge live stdin data with stored current.json.

    Persists live model/window back to current.json so a mid-session /model switch
    is reflected for other consumers (token_report, next Stop hook, etc.) without
    waiting for the next assistant message.

    Self-heals on session change: if the live session_id from Claude Code differs
    from what's stored, run the same reset that SessionStart/UserPromptSubmit hooks
    do. Guarantees a fresh Turn/Sess display the moment a new session opens, even
    if those hooks aren't wired.
    """
    if len(argv) < 1:
        print("💰 --")
        return
    status_path = argv[0]
    raw_live = sys.stdin.read().strip()
    live = json.loads(raw_live) if raw_live else {}

    try:
        status = json.loads(Path(status_path).read_text(encoding="utf-8"))
    except Exception:
        status = {}

    live_session_id = live.get("session_id")
    live_cwd = (live.get("cwd")
                or (live.get("workspace") or {}).get("current_dir")
                or "")
    stored_sid = status.get("session_id", "")
    if (live_session_id and live_cwd
            and stored_sid and stored_sid != live_session_id):
        _reset_session_state(live_session_id, live_cwd)
        try:
            status = json.loads(Path(status_path).read_text(encoding="utf-8"))
        except Exception:
            status = {}

    if not status:
        print("💰 --")
        return

    # Live data from Claude Code is authoritative. Our model dict is fallback only.
    # Brief transient on mid-session /model switch self-heals in one render cycle.
    live_model = live.get("model") or {}
    live_ctx   = live.get("context_window") or {}
    changed = False

    current_model = status.get("model", "")
    new_model = live_model.get("id") if isinstance(live_model, dict) else None
    if new_model and current_model != new_model:
        status["model"] = new_model
        current_model = new_model
        changed = True

    ctx = status.setdefault("context", {})

    # Trust live window size (handles 1M variants like claude-sonnet-4-6 with 1M opt-in
    # that the dict can't represent). Fall back to dict for unknown / missing live data.
    live_size = (live_ctx.get("context_window_size")
                 if isinstance(live_ctx, dict) else None)
    new_size = live_size or get_context_window(current_model)
    if new_size and ctx.get("window_size") != new_size:
        ctx["window_size"] = new_size
        changed = True

    if isinstance(live_ctx, dict):
        # Per docs, used_percentage = (input + cache_creation + cache_read) / size.
        # Recompute from current_usage so pct/tokens stay consistent with our window_size.
        cu = live_ctx.get("current_usage") if isinstance(live_ctx.get("current_usage"), dict) else None
        if cu:
            live_tokens = (cu.get("input_tokens", 0)
                           + cu.get("cache_creation_input_tokens", 0)
                           + cu.get("cache_read_input_tokens", 0))
            if ctx.get("tokens") != live_tokens:
                ctx["tokens"] = live_tokens
                changed = True
            new_pct = round(live_tokens / new_size * 100) if (new_size and live_tokens) else 0
            if ctx.get("pct") != new_pct:
                ctx["pct"] = new_pct
                changed = True
        elif live_ctx.get("used_percentage") is not None:
            # Fallback: pre-API-call render or older Claude Code without current_usage.
            new_pct = live_ctx["used_percentage"]
            if ctx.get("pct") != new_pct:
                ctx["pct"] = new_pct
                changed = True

    if changed:
        try:
            status_json = json.dumps(status, ensure_ascii=False)
            Path(status_path).write_text(status_json, encoding="utf-8")
            # Mirror to global current.json so cross-project tools see fresh state
            current_json = Path(status_path).parent / "current.json"
            if Path(status_path).resolve() != current_json.resolve():
                current_json.write_text(status_json, encoding="utf-8")
        except Exception:
            pass

    print(render_status_line(status), end="")


def backfill_mode():
    """One-shot: recompute missing turn_count / active_minutes for legacy sessions.
    Idempotent — sessions that already have turn_count are skipped."""
    transcripts_root = Path.home() / ".claude" / "projects"
    if not DATA_DIR.exists() or not transcripts_root.exists():
        print("nothing to backfill")
        return

    sid_to_path = {p.stem: p for p in transcripts_root.glob("*/*.jsonl")}

    backfilled = 0
    for proj_file in sorted(DATA_DIR.glob("*.json")):
        try:
            data = json.loads(proj_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        sessions = data.get("sessions", {})
        proj_changed = False
        for sid, sess in sessions.items():
            if "turn_count" in sess and "active_minutes" in sess:
                continue
            transcript = sid_to_path.get(sid)
            if not transcript:
                continue
            msgs = read_transcript(str(transcript))
            if "turn_count" not in sess:
                sess["turn_count"] = count_turns(msgs)
            if "active_minutes" not in sess:
                sess["active_minutes"] = calc_active_minutes(msgs)
            proj_changed = True
            backfilled += 1
        if proj_changed:
            recompute_project_totals(data)
            save_project_data(DATA_DIR, data["pid"], data)
    print(f"backfilled {backfilled} session(s)")


def _scan_children(parent_pid: str) -> list:
    """Return all project dicts whose parent_pid matches. Disk-scoped scan; cheap
    enough at write-time (typical project counts < 100, files small)."""
    children = []
    if not parent_pid or not DATA_DIR.exists():
        return children
    for f in DATA_DIR.glob("*.json"):
        try:
            other = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if other.get("parent_pid") == parent_pid and other.get("pid") != parent_pid:
            children.append(other)
    return children


def _own_proj_segment(project: dict) -> dict:
    """Single-project view, used when this project has no children."""
    pt = project.get("project_total_tokens", {})
    return {
        "cost": project.get("project_total_cost", 0),
        "session_count": project.get("session_count", 0),
        "turn_count": project.get("project_total_turns", 0),
        "active_minutes": project.get("project_active_minutes", 0),
        "input_tokens":  pt.get("input_tokens", 0),
        "cache_creation": pt.get("cache_creation_input_tokens", 0),
        "cache_read":    pt.get("cache_read_input_tokens", 0),
        "output_tokens": pt.get("output_tokens", 0),
    }


def _refresh_parent_status(parent_pid: str):
    """Re-aggregate family totals into the parent's status file. Called from a
    child's Stop hook (and from --set-parent / --unset-parent) so the parent's
    status bar reflects fresh family numbers without having to fire its own
    Stop hook first. No-op if parent's status / project files don't exist."""
    if not parent_pid:
        return
    parent_proj_file   = DATA_DIR / f"{parent_pid}.json"
    parent_status_file = STATUS_DIR / f"{parent_pid}.json"
    if not parent_proj_file.exists() or not parent_status_file.exists():
        return
    try:
        parent_project = json.loads(parent_proj_file.read_text(encoding="utf-8"))
        children = _scan_children(parent_pid)
        own = _own_proj_segment(parent_project)
        family = _live_family_totals(own, children) if children else own
        status = json.loads(parent_status_file.read_text(encoding="utf-8"))
        status["project"] = family
        status["project_own"] = own
        parent_status_file.write_text(json.dumps(status, ensure_ascii=False),
                                      encoding="utf-8")
    except Exception:
        pass


def _attach_parent(status: dict, project: dict):
    """Copy parent_pid / parent_name from a project file into a status payload.
    Renames propagate on next update — staleness is harmless cosmetic."""
    parent_pid = project.get("parent_pid")
    if parent_pid:
        status["parent_pid"] = parent_pid
        status["parent_name"] = project.get("parent_name", "")
    else:
        status.pop("parent_pid", None)
        status.pop("parent_name", None)


def set_parent_mode(argv: list):
    """Explicit, user-driven sub-project link: mark `child` as a sub-project of
    `parent`. Both must already exist as ClaudeCount projects (i.e. have run at
    least one Stop hook). Single-level only — a parent cannot itself have a
    parent. Idempotent.

    Usage:
      --set-parent <parent_path>             # child defaults to cwd
      --set-parent <parent_path> <child_path>
    """
    if not argv:
        print("usage: --set-parent <parent_path> [child_path]")
        return
    parent_cwd = os.path.abspath(argv[0])
    child_cwd  = os.path.abspath(argv[1] if len(argv) > 1 else os.getcwd())
    parent_pid = project_id(parent_cwd)
    child_pid  = project_id(child_cwd)

    if parent_pid == child_pid:
        print(f"refusing to link a project to itself ({parent_cwd})")
        return

    parent_file = DATA_DIR / f"{parent_pid}.json"
    child_file  = DATA_DIR / f"{child_pid}.json"
    if not parent_file.exists():
        print(f"parent has no ClaudeCount data yet: {parent_cwd}")
        return

    parent = json.loads(parent_file.read_text(encoding="utf-8"))
    if child_file.exists():
        child = json.loads(child_file.read_text(encoding="utf-8"))
    else:
        # Pre-link: materialize an empty project file so future Stop hooks
        # find an existing record (and inherit the parent link).
        child = {
            "pid": child_pid,
            "name": Path(child_cwd).name,
            "cwd": child_cwd,
            "created": datetime.now(timezone.utc).isoformat(),
            "sessions": {},
        }
        recompute_project_totals(child)
        print(f"created empty project record for {child['name']} (no sessions yet)")

    if parent.get("parent_pid"):
        print(f"refusing to link under '{parent.get('name')}' — it is already a "
              f"sub-project of '{parent.get('parent_name')}'. Single-level only.")
        return

    child["parent_pid"]  = parent_pid
    child["parent_name"] = parent.get("name", "")
    save_project_data(DATA_DIR, child_pid, child)

    # Mirror into status so the next render shows the parent prefix immediately.
    status_file = STATUS_DIR / f"{child_pid}.json"
    if status_file.exists():
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
            _attach_parent(status, child)
            status_file.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # Re-aggregate the parent's family totals so the parent's status bar
    # immediately reflects the new child without waiting for a Stop hook.
    _refresh_parent_status(parent_pid)

    print(f"linked '{child.get('name')}' → parent '{parent.get('name')}'")


def unset_parent_mode(argv: list):
    """Remove the parent link from a child project. Defaults to cwd."""
    child_cwd = os.path.abspath(argv[0] if argv else os.getcwd())
    child_pid = project_id(child_cwd)
    child_file = DATA_DIR / f"{child_pid}.json"
    if not child_file.exists():
        print(f"no ClaudeCount data for {child_cwd}")
        return
    child = json.loads(child_file.read_text(encoding="utf-8"))
    if not child.get("parent_pid"):
        print(f"'{child.get('name')}' has no parent — nothing to unset")
        return
    prev = child.get("parent_name") or child.get("parent_pid")
    ex_parent_pid = child.get("parent_pid")
    child.pop("parent_pid", None)
    child.pop("parent_name", None)
    save_project_data(DATA_DIR, child_pid, child)

    status_file = STATUS_DIR / f"{child_pid}.json"
    if status_file.exists():
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
            status.pop("parent_pid", None)
            status.pop("parent_name", None)
            status_file.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # Re-aggregate the ex-parent's family totals so its status bar drops this
    # child immediately rather than waiting for its next Stop hook.
    _refresh_parent_status(ex_parent_pid)

    print(f"unlinked '{child.get('name')}' from parent '{prev}'")


def merge_into_parent_mode(argv: list):
    """Absorb a sub-project's history into its parent and delete the
    sub-project record. **Destructive** — without `--yes`, prints an impact
    preview and exits without writing. With `--yes`, performs:

      1. merges every session in the child's `sessions` dict into the parent's
         (collisions resolved by latest `updated`; merged entries get a
         `merged_from` audit field)
      2. recomputes parent totals
      3. deletes the child's project file and status file
      4. refreshes the parent's status (drops the child from family aggregate)

    Refuses outright when the child carries a `legacy` block: that combines
    with the parent's own legacy in non-trivial ways (potentially overlapping
    period_start/end ranges, conflicting notes), so we leave it to the user
    to consolidate manually rather than silently double-count or clobber.

    Usage:
      --merge-into-parent              # child = cwd, preview only
      --merge-into-parent /path        # explicit child, preview only
      --merge-into-parent --yes        # child = cwd, actually merge
      --merge-into-parent /path --yes  # explicit child, actually merge
    """
    args = list(argv)
    yes = ("--yes" in args) or ("-y" in args)
    args = [a for a in args if a not in ("--yes", "-y")]
    child_cwd = os.path.abspath(args[0] if args else os.getcwd())
    child_pid = project_id(child_cwd)
    child_file = DATA_DIR / f"{child_pid}.json"
    if not child_file.exists():
        print(f"no ClaudeCount data for {child_cwd}")
        return

    child = json.loads(child_file.read_text(encoding="utf-8"))
    parent_pid = child.get("parent_pid")
    if not parent_pid:
        print(f"'{child.get('name')}' is not a sub-project — "
              f"link it first with --set-parent before merging")
        return

    parent_file = DATA_DIR / f"{parent_pid}.json"
    if not parent_file.exists():
        print(f"parent project file missing (parent_pid={parent_pid}) — "
              f"cannot merge; consider --unset-parent and removing the orphan")
        return
    parent = json.loads(parent_file.read_text(encoding="utf-8"))

    if _legacy_view(child):
        print(f"refusing to merge: child '{child.get('name')}' carries a `legacy` "
              f"block (pre-ClaudeCount usage). Consolidate that into the parent's "
              f"legacy block manually first, then re-run.")
        return

    child_sessions = child.get("sessions", {})

    if not yes:
        merged_sess  = parent.get("session_count", 0) + len(child_sessions)
        merged_cost  = parent.get("project_total_cost", 0) + child.get("project_total_cost", 0)
        merged_turns = parent.get("project_total_turns", 0) + child.get("project_total_turns", 0)
        merged_min   = parent.get("project_active_minutes", 0) + child.get("project_active_minutes", 0)
        print("=" * 60)
        print("PREVIEW — no changes written. Re-run with --yes to apply.")
        print("=" * 60)
        print(f"  child  : {child.get('name')}  ({len(child_sessions)} sessions, "
              f"${child.get('project_total_cost', 0):.2f}, "
              f"{child.get('project_total_turns', 0)} turns, "
              f"{child.get('project_active_minutes', 0)} min)")
        print(f"  parent : {parent.get('name')}  ({parent.get('session_count', 0)} sessions, "
              f"${parent.get('project_total_cost', 0):.2f}, "
              f"{parent.get('project_total_turns', 0)} turns, "
              f"{parent.get('project_active_minutes', 0)} min)")
        print(f"  after  : {parent.get('name')}  ({merged_sess} sessions, "
              f"${merged_cost:.2f}, {merged_turns} turns, {merged_min} min)")
        print()
        print(f"  child's project + status files will be DELETED. This cannot be undone.")
        print(f"  re-run with --yes when ready.")
        return

    # Apply
    parent_sessions = parent.setdefault("sessions", {})
    collisions = []
    for sid, s in child_sessions.items():
        if sid in parent_sessions:
            existing_updated = parent_sessions[sid].get("updated", "")
            new_updated = s.get("updated", "")
            if new_updated > existing_updated:
                merged = dict(s)
                merged["merged_from"] = child.get("name", child_pid)
                parent_sessions[sid] = merged
            collisions.append(sid)
        else:
            merged = dict(s)
            merged["merged_from"] = child.get("name", child_pid)
            parent_sessions[sid] = merged

    recompute_project_totals(parent)
    parent["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_project_data(DATA_DIR, parent_pid, parent)

    child_file.unlink()
    child_status = STATUS_DIR / f"{child_pid}.json"
    if child_status.exists():
        child_status.unlink()

    _refresh_parent_status(parent_pid)

    new_cost = parent.get("project_total_cost", 0)
    new_sess = parent.get("session_count", 0)
    msg = (f"merged {len(child_sessions)} session(s) from '{child.get('name')}' "
           f"into '{parent.get('name')}'; child project + status files deleted\n"
           f"parent new total: ${new_cost:.2f}, {new_sess} session(s)")
    if collisions:
        msg += f"\n{len(collisions)} session-id collision(s) resolved by latest 'updated'"
    print(msg)


def import_mode(argv: list):
    """Adopt sessions that pre-date ClaudeCount: scan
    ~/.claude/projects/<encoded-cwd>/*.jsonl and import any session not yet in
    projects/{pid}.json. Idempotent — already-tracked sessions are skipped.

    Path defaults to the current working directory; pass `--import <path>` to
    backfill a different project. Each session is reconstructed from its
    transcript exactly the way the Stop hook would: tokens / cost / model /
    started / active_minutes / turn_count, with per-message model used for
    pricing so multi-model sessions reprice correctly.
    """
    cwd = os.path.abspath(argv[0] if argv else os.getcwd())
    encoded = cwd.replace("/", "-")
    transcripts_dir = Path.home() / ".claude" / "projects" / encoded
    if not transcripts_dir.exists():
        print(f"no Claude Code transcripts at {transcripts_dir}")
        return

    pid = project_id(cwd)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    project = load_project_data(DATA_DIR, pid)
    project.setdefault("sessions", {})
    project.setdefault("created", datetime.now(timezone.utc).isoformat())
    project["name"] = Path(cwd).name
    project["cwd"] = cwd

    cfg = load_project_config(cwd)
    price_override = cfg.get("pricing", None)

    imported = skipped = empty = 0
    last_model_seen = ""
    for transcript in sorted(transcripts_dir.glob("*.jsonl")):
        sid = transcript.stem
        if sid in project["sessions"]:
            skipped += 1
            continue
        msgs = read_transcript(str(transcript))
        usages = get_all_assistant_usages(msgs) if msgs else []
        if not usages:
            empty += 1
            continue
        session_tokens, session_cost, model = sum_usages(usages, price_override)
        if model:
            last_model_seen = model
        started = get_session_start(msgs) or datetime.now(timezone.utc).isoformat()
        project["sessions"][sid] = {
            "cost": session_cost,
            "tokens": session_tokens,
            "model": model,
            "started": started,
            "active_minutes": calc_active_minutes(msgs),
            "turn_count": count_turns(msgs),
            "updated": datetime.now(timezone.utc).isoformat(),
            "imported": True,
        }
        imported += 1

    if imported:
        project["last_updated"] = datetime.now(timezone.utc).isoformat()
        if not project.get("last_model") and last_model_seen:
            project["last_model"] = last_model_seen
        recompute_project_totals(project)
        save_project_data(DATA_DIR, pid, project)
    print(f"{Path(cwd).name}: imported {imported}, "
          f"skipped {skipped} already-tracked, {empty} empty")


def main():
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"ClaudeCount {__version__}")
        return
    if "--render" in sys.argv:
        render_mode(sys.argv[sys.argv.index("--render") + 1:])
        return
    if "--session-start" in sys.argv:
        session_start_mode()
        return
    if "--pre-turn" in sys.argv:
        pre_turn_mode()
        return
    if "--backfill" in sys.argv:
        backfill_mode()
        return
    if "--import" in sys.argv:
        import_mode(sys.argv[sys.argv.index("--import") + 1:])
        return
    if "--set-parent" in sys.argv:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        set_parent_mode(sys.argv[sys.argv.index("--set-parent") + 1:])
        return
    if "--unset-parent" in sys.argv:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        unset_parent_mode(sys.argv[sys.argv.index("--unset-parent") + 1:])
        return
    if "--merge-into-parent" in sys.argv:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        merge_into_parent_mode(sys.argv[sys.argv.index("--merge-into-parent") + 1:])
        return

    hook = _read_hook_input()
    if hook is None:
        return

    session_id = hook.get("session_id", "unknown")
    transcript_path = hook.get("transcript_path", "")
    cwd = hook.get("cwd", os.getcwd())

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_DIR.mkdir(parents=True, exist_ok=True)

    cfg = load_project_config(cwd)
    price_override = cfg.get("pricing", None)

    if cfg.get("enabled") is False:
        return

    messages = read_transcript(transcript_path) if transcript_path else []
    usages = get_all_assistant_usages(messages)

    if not usages:
        return

    # Current turn: all API calls since the last human message (not tool results)
    turn_usages = get_last_turn_usages(messages) or usages[-1:]
    last_usage, last_cost, last_model = sum_usages(turn_usages, price_override)

    # Full session: all deduplicated assistant messages in transcript
    session_tokens, session_cost, model = sum_usages(usages, price_override)

    # Context window usage (last API call = most recent context snapshot).
    # window_size comes from the stored status written by render_mode with live Claude Code data.
    # The dict is a last-resort fallback only — runtime data is authoritative.
    current_model = last_model or model
    pid = project_id(cwd)
    stored_status = {}
    try:
        stored_status = json.loads((STATUS_DIR / f"{pid}.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    ctx_win_size = (stored_status.get("context", {}).get("window_size")
                    or get_context_window(current_model))
    last_api_usage = turn_usages[-1][0] if turn_usages else {}
    context_tokens = (
        last_api_usage.get("input_tokens", 0)
        + last_api_usage.get("cache_read_input_tokens", 0)
        + last_api_usage.get("cache_creation_input_tokens", 0)
    )
    context_pct = round(context_tokens / ctx_win_size * 100) if context_tokens else 0

    turn_count = count_turns(messages)
    project = load_project_data(DATA_DIR, pid)
    if "created" not in project:
        project["created"] = datetime.now(timezone.utc).isoformat()
    project["name"] = Path(cwd).name
    project["cwd"] = cwd
    project["last_model"] = current_model
    project["last_updated"] = datetime.now(timezone.utc).isoformat()

    session_start = get_session_start(messages)
    prev_session = project.get("sessions", {}).get(session_id, {})
    prev_started = prev_session.get("started", "")
    started = prev_started or session_start or datetime.now(timezone.utc).isoformat()
    active_minutes = calc_active_minutes(messages)
    project.setdefault("sessions", {})[session_id] = {
        "cost": session_cost,
        "tokens": session_tokens,
        "model": model,
        "started": started,
        "active_minutes": active_minutes,
        "turn_count": turn_count,
        "updated": datetime.now(timezone.utc).isoformat(),
    }

    recompute_project_totals(project)

    save_project_data(DATA_DIR, pid, project)

    own_seg = _own_proj_segment(project)
    own_children = _scan_children(pid)
    status = {
        "project_name": Path(cwd).name,
        "cwd": cwd,
        "pid": pid,
        "session_id": session_id,
        "model": current_model,
        "created": project.get("created", ""),
        "last_turn": {
            "cost": last_cost,
            "input_tokens": last_usage.get("input_tokens", 0),
            "cache_creation": last_usage.get("cache_creation_input_tokens", 0),
            "cache_read": last_usage.get("cache_read_input_tokens", 0),
            "output_tokens": last_usage.get("output_tokens", 0),
        },
        "session": {
            "cost": session_cost,
            "input_tokens": session_tokens.get("input_tokens", 0),
            "cache_creation": session_tokens.get("cache_creation_input_tokens", 0),
            "cache_read": session_tokens.get("cache_read_input_tokens", 0),
            "output_tokens": session_tokens.get("output_tokens", 0),
            "started": started,
            "active_minutes": active_minutes,
            "turn_count": turn_count,
        },
        "project": _live_family_totals(own_seg, own_children) if own_children else own_seg,
        "project_own": own_seg,
        "context": {
            "tokens": context_tokens,
            "window_size": ctx_win_size,
            "pct": context_pct,
        },
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    _attach_parent(status, project)

    status_json = json.dumps(status, ensure_ascii=False)
    (STATUS_DIR / f"{pid}.json").write_text(status_json, encoding="utf-8")
    (STATUS_DIR / "current.json").write_text(status_json, encoding="utf-8")

    # If this project is a child, propagate fresh family totals up to the
    # parent's status so the parent's Proj segment stays current without
    # needing its own Stop hook to also fire.
    if project.get("parent_pid"):
        _refresh_parent_status(project["parent_pid"])


if __name__ == "__main__":
    main()
