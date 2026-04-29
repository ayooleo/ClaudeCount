#!/usr/bin/env python3
"""ClaudeCount — detailed usage report.

Usage:
  python3 token_report.py            # current project (verbose)
  python3 token_report.py --all      # all projects
  python3 token_report.py -v         # all projects, verbose session list
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime

# Reuse model resolution / project-id logic from the tracker (deployed alongside)
sys.path.insert(0, str(Path(__file__).parent))
from token_tracker import fmt_model, project_id, _legacy_view, recompute_project_totals  # noqa: E402


def fmt_cost(c: float) -> str:
    if c == 0:    return "$0.00"
    if c < 0.01:  return f"${c:.4f}"
    if c < 1:     return f"${c:.3f}"
    return f"${c:.2f}"


def fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def load_all_projects(data_dir: Path) -> list:
    projects = []
    for f in sorted(data_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            recompute_project_totals(data)
            projects.append(data)
        except Exception:
            pass
    return projects


def build_children_index(projects: list) -> dict:
    """parent_pid -> list of child project dicts (sorted by cost desc)."""
    idx = {}
    for p in projects:
        pp = p.get("parent_pid")
        if pp:
            idx.setdefault(pp, []).append(p)
    for k in idx:
        idx[k].sort(key=lambda c: c.get("project_total_cost", 0), reverse=True)
    return idx


def _fmt_active(active_minutes: int) -> str:
    if not active_minutes:
        return ""
    h = active_minutes / 60
    if h >= 1:
        return f"{h:.1f} hr"
    return f"{active_minutes} min"


def print_project(p: dict, verbose: bool = False, children: list = None):
    name = p.get("name", p.get("pid", "unknown"))
    cwd = p.get("cwd", "")
    total_cost = p.get("project_total_cost", 0)
    total_tok = p.get("project_total_tokens", {})
    session_count = p.get("session_count", 0)
    active_days = p.get("active_days", 0)
    last_updated = p.get("last_updated", "")
    model = p.get("last_model", "")

    print(f"\n{'='*60}")
    print(f"  Project : {name}")
    parent_name = p.get("parent_name")
    if parent_name:
        print(f"  Parent  : {parent_name}")
    print(f"  Path    : {cwd}")
    print(f"  Model   : {fmt_model(model) or '--'}")
    active_minutes = p.get("project_active_minutes", 0)
    active_hours = active_minutes / 60
    if active_hours >= 1:
        time_display = f"{active_hours:.1f} hr"
    elif active_minutes >= 1:
        time_display = f"{active_minutes} min"
    else:
        time_display = "--"
    legacy = _legacy_view(p)
    legacy_tag = "  +legacy" if legacy else ""
    print(f"  Sessions: {session_count}  Active: {time_display}  "
          f"Last seen: {last_updated[:19] if last_updated else '--'}")
    print(f"  {'─'*41}")
    print(f"  Total cost    : {fmt_cost(total_cost)}{legacy_tag}")
    print(f"  Input tokens  : {fmt_tok(total_tok.get('input_tokens', 0))}")
    print(f"  Output tokens : {fmt_tok(total_tok.get('output_tokens', 0))}")
    cache_read = total_tok.get("cache_read_input_tokens", 0)
    if cache_read:
        print(f"  Cache reads   : {fmt_tok(cache_read)}  (cost saved)")
    if legacy:
        period = ""
        ps, pe = legacy.get("period_start", ""), legacy.get("period_end", "")
        if ps and pe:
            period = f"  [{ps[:10]} → {pe[:10]}]"
        sc = legacy.get("session_count") or len(legacy.get("session_ids", []))
        note = legacy.get("note", "")
        print(f"  Legacy        : {fmt_cost(float(legacy.get('cost_usd', 0)))}  "
              f"{sc} sess{period}")
        if note:
            print(f"                  {note}")

    if children:
        # Hide $0.00 children from the listing — they add noise without info.
        # Family total math is unaffected (zero contributes zero).
        visible = [c for c in children if c.get("project_total_cost", 0) > 0]
        family_total = total_cost + sum(c.get("project_total_cost", 0) for c in children)
        if visible:
            print(f"  {'─'*41}")
            print(f"  Sub-projects:")
            max_name = max(len(c.get("name", "")) for c in visible)
            for c in visible:
                cname = c.get("name", c.get("pid", "?"))
                ccost = c.get("project_total_cost", 0)
                csess = c.get("session_count", 0)
                cmin  = c.get("project_active_minutes", 0)
                extras = [f"{csess} sess"]
                active_str = _fmt_active(cmin)
                if active_str:
                    extras.append(active_str)
                extras_str = ", ".join(extras)
                print(f"    {cname:<{max_name}}  {fmt_cost(ccost)}  ({extras_str})")
            print(f"  {'─'*41}")
            print(f"  Project total : {fmt_cost(family_total)}  "
                  f"(parent + {len(visible)} sub-project{'s' if len(visible) != 1 else ''})")

    if verbose:
        sessions = p.get("sessions", {})
        if sessions:
            print(f"\n  Session breakdown:")
            for sid, s in sorted(sessions.items(), key=lambda x: x[1].get("started", x[1].get("updated", ""))):
                ts = (s.get("started") or s.get("updated", ""))[:19]
                toks = s.get("tokens", {})
                sess_model = fmt_model(s.get("model", ""))
                model_str = f"  {sess_model}" if sess_model else ""
                print(f"    [{ts}]  {fmt_cost(s.get('cost', 0))}"
                      f"  in: {fmt_tok(toks.get('input_tokens', 0))}"
                      f"  out: {fmt_tok(toks.get('output_tokens', 0))}"
                      f"{model_str}")


def main():
    data_dir = Path.home() / ".claude" / "token_usage" / "projects"

    if not data_dir.exists():
        print("No data yet. Usage is recorded automatically after each Claude Code session.")
        return

    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    # Match README: no args (or `-v` only) → current project; `--all` → every project.
    show_all = "--all" in args

    projects = load_all_projects(data_dir)
    if not projects:
        print("No data yet.")
        return

    children_idx = build_children_index(projects)
    grand_total = sum(p.get("project_total_cost", 0) for p in projects)

    print(f"\n{'='*60}")
    print(f"  ClaudeCount — Usage Report")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if show_all:
        for p in sorted(projects, key=lambda x: x.get("project_total_cost", 0), reverse=True):
            print_project(p, verbose, children=children_idx.get(p.get("pid")))
    else:
        cwd = os.getcwd()
        pid = project_id(cwd)
        matched = [p for p in projects if p.get("pid") == pid or p.get("cwd") == cwd]
        if matched:
            print_project(matched[0], verbose=True, children=children_idx.get(matched[0].get("pid")))
        else:
            print(f"\nNo data for current directory ({cwd}).")

    print(f"\n{'='*60}")
    print(f"  All projects total: {fmt_cost(grand_total)}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
