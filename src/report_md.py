from datetime import datetime, timezone
from pathlib import Path

import yaml

from src import analyze_compute

_ROOT = Path(__file__).resolve().parent.parent
_cfg = yaml.safe_load((_ROOT / "config" / "config.yaml").read_text())
_REPORTS_DIR = _ROOT / _cfg["paths"]["reports"]


def generate_report(week_start, week_end):
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    top       = analyze_compute.top_mentioned(week_start, week_end)
    unique    = analyze_compute.unique_mentioners(week_start, week_end)
    wow       = analyze_compute.wow_movers(week_start, week_end)
    mom       = analyze_compute.mom_movers(week_start, week_end)
    new       = analyze_compute.new_tickers(week_start, week_end)
    diverge   = analyze_compute.divergence_candidates(week_start, week_end)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _row(*cols):
        return "| " + " | ".join(str(c) for c in cols) + " |"

    lines = [
        "# Fintwit Weekly Report",
        f"**Week:** {week_start} → {week_end}  ",
        f"**Generated:** {ts}  ",
        "",
        "---",
        "",
        "## Top Mentioned",
        "",
        _row("Ticker", "Mentions"),
        _row("------", "-------"),
    ]
    lines += [_row(f"${r['ticker']}", r["count"]) for r in top] or ["*no data*"]

    lines += [
        "",
        "## Unique Mentioners",
        "",
        _row("Ticker", "Accounts"),
        _row("------", "--------"),
    ]
    lines += [_row(f"${r['ticker']}", r["mentioners"]) for r in unique] or ["*no data*"]

    lines += [
        "",
        "## WoW Movers",
        "",
        _row("Ticker", "This Week", "Prior Week", "Δ", "Δ%", ""),
        _row("------", "---------", "----------", "-", "--", ""),
    ]
    for r in wow:
        pct    = f"{r['pct_change']:+.1f}%" if r["pct_change"] is not None else "new"
        signal = "⚠ low signal" if r["low_signal"] else ""
        lines.append(_row(f"${r['ticker']}", r["this_week"], r["prior_week"],
                          f"{r['abs_change']:+d}", pct, signal))
    if not wow:
        lines.append("*no data*")

    lines += [
        "",
        "## MoM Movers",
        "",
        _row("Ticker", "Current 4W", "Prior 4W", "Δ", "Δ%"),
        _row("------", "----------", "--------", "-", "--"),
    ]
    for r in mom:
        pct = f"{r['pct_change']:+.1f}%" if r["pct_change"] is not None else "new"
        lines.append(_row(f"${r['ticker']}", r["current_4w"], r["prior_4w"],
                          f"{r['abs_change']:+d}", pct))
    if not mom:
        lines.append("*no data*")

    lines += [
        "",
        "## New Tickers This Week",
        "*(not seen in prior 8 weeks)*",
        "",
        (", ".join(f"${t}" for t in new) if new else "*none*"),
        "",
        "## Divergence Candidates",
        "*(sentiment unscored — run analyze_claude.py first)*",
        "",
        _row("Ticker", "Stdev", "Mentions", "Mean Score"),
        _row("------", "-----", "--------", "----------"),
    ]
    lines += [
        _row(f"${r['ticker']}", r["stdev"], r["mention_count"], r["mean_score"])
        for r in diverge
    ] or [_row("—", "unscored", "—", "unscored")]

    out = "\n".join(lines) + "\n"
    path = _REPORTS_DIR / f"{week_start}.md"
    path.write_text(out)
    return str(path)
