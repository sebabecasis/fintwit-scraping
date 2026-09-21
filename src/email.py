import os
from datetime import datetime, timezone

import resend
from dotenv import load_dotenv

load_dotenv(override=True)


def _api_key():
    k = os.getenv("RESEND_API_KEY")
    if not k:
        raise RuntimeError("RESEND_API_KEY not set")
    return k


def _from_addr():
    return os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")


def _subject(top, week_start):
    leaders = " + ".join(f"${r['ticker']}" for r in top[:2]) if top else "—"
    return f"Fintwit Weekly: {leaders} leading | {week_start}"


def _html_body(week_start, week_end, metrics, narrative=None,
               new_ticker_summaries=None, dashboard_url=None):
    top      = metrics.get("top",  [])
    wow      = metrics.get("wow",  [])
    new      = metrics.get("new",  [])
    summaries = new_ticker_summaries or metrics.get("new_ticker_summaries") or []
    nar      = narrative or metrics.get("narrative") or \
               "Narrative analysis pending."

    css = """
body{margin:0;padding:0;background:#0a0a0d;font-family:'Courier New',monospace;color:#e0e0e0}
.wrap{max-width:620px;margin:0 auto;background:#0a0a0d;border:1px solid #1e1e1e}
.hdr{background:#0a0a0d;padding:20px 24px;border-bottom:3px solid #f59e0b}
.hdr h1{color:#f59e0b;font-size:15px;font-weight:bold;margin:0;letter-spacing:.15em}
.hdr .meta{color:#555;font-size:11px;margin-top:5px}
.body{padding:24px}
.nar{border-left:3px solid #f59e0b;padding:10px 14px;background:#111;
  color:#aaa;font-style:italic;font-size:12px;line-height:1.7;margin-bottom:24px}
h2{font-size:9px;font-weight:bold;text-transform:uppercase;letter-spacing:.15em;
  color:#f59e0b;border-bottom:1px solid #1e1e1e;padding-bottom:6px;margin:24px 0 10px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:4px 8px;font-size:9px;color:#555;text-transform:uppercase;
  letter-spacing:.08em;border-bottom:1px solid #1e1e1e}
td{padding:6px 8px;border-bottom:1px solid #111;vertical-align:top}
.tk{font-weight:bold;font-size:13px;color:#f59e0b;white-space:nowrap}
.num{text-align:right;color:#aaa}
.up{color:#22c55e;font-weight:bold}
.dn{color:#ef4444;font-weight:bold}
.rat{font-size:10px;color:#666;margin-top:3px;line-height:1.4}
.accs{font-size:10px;color:#555;margin-top:2px}
.theme{font-size:11px;color:#ccc}
.subtheme{font-size:10px;color:#666}
.foot{padding:14px 24px;border-top:1px solid #1e1e1e;font-size:10px;color:#444;text-align:center}
.foot a{color:#f59e0b;text-decoration:none}
"""

    lines = [f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{css}</style></head>
<body><div class="wrap">
<div class="hdr">
  <h1>FINTWIT WEEKLY</h1>
  <div class="meta">{week_start} → {week_end} &nbsp;·&nbsp; {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>
</div>
<div class="body">
"""]

    # ── narrative ─────────────────────────────────────────────────────────────
    lines.append(f'<div class="nar">{nar}</div>\n')

    # ── top 10 mentioned ──────────────────────────────────────────────────────
    lines.append('<h2>Top Mentioned</h2>')
    lines.append('<table><thead><tr><th>#</th><th>Ticker</th>'
                 '<th class="num">Mentions</th><th class="num">Accounts</th>'
                 '</tr></thead><tbody>')
    for i, r in enumerate(top[:10], 1):
        lines.append(
            f'<tr><td class="num" style="color:#333">{i}</td>'
            f'<td class="tk">${r["ticker"]}</td>'
            f'<td class="num">{r["count"]}</td>'
            f'<td class="num">{r.get("mentioners", "—")}</td></tr>'
        )
    lines.append('</tbody></table>\n')

    # ── WoW movers ────────────────────────────────────────────────────────────
    lines.append('<h2>WoW Movers</h2>')
    lines.append('<table><thead><tr><th>Ticker</th><th class="num">This Wk</th>'
                 '<th class="num">Prior Wk</th><th class="num">Δ</th>'
                 '</tr></thead><tbody>')
    for r in wow[:5]:
        sign = "↑" if r["abs_change"] > 0 else "↓"
        cls  = "up" if r["abs_change"] > 0 else "dn"
        pct  = f'{r["pct_change"]:+.0f}%' if r.get("pct_change") is not None else "new"
        lines.append(
            f'<tr><td class="tk">${r["ticker"]}</td>'
            f'<td class="num">{r["this_week"]}</td>'
            f'<td class="num" style="color:#333">{r["prior_week"]}</td>'
            f'<td class="{cls}">{sign} {pct}</td></tr>'
        )
    lines.append('</tbody></table>\n')

    # ── new tickers ───────────────────────────────────────────────────────────
    total_new = len(new)
    lines.append(f'<h2>New This Week ({total_new})</h2>')
    if summaries:
        lines.append('<table><thead><tr><th>Ticker</th><th>Theme / Signal</th>'
                     '<th class="num">Tweets</th></tr></thead><tbody>')
        for s in summaries:
            accs = " &nbsp;".join(f'@{a}' for a in s["accounts"][:6])
            if len(s["accounts"]) > 6:
                accs += f' <span style="color:#444">+{len(s["accounts"]) - 6}</span>'
            rats = s.get("rationales", [])
            rat_html = ""
            if rats:
                rat_html = '<div class="rat">' + \
                    " &nbsp;·&nbsp; ".join(rats[:2]) + '</div>'
            lines.append(
                f'<tr>'
                f'<td class="tk" style="padding-top:8px">${s["ticker"]}</td>'
                f'<td style="padding-top:8px">'
                f'  <div class="theme">{s["theme"]}'
                f'  <span class="subtheme"> · {s["sub_theme"]}</span></div>'
                f'  {rat_html}'
                f'  <div class="accs">{accs}</div>'
                f'</td>'
                f'<td class="num" style="padding-top:8px">{s["tweet_count"]}</td>'
                f'</tr>'
            )
        lines.append('</tbody></table>')
        remainder = total_new - len(summaries)
        if remainder > 0:
            lines.append(f'<p style="font-size:10px;color:#444;margin-top:6px">'
                         f'+ {remainder} more below 3-mention threshold</p>')
        lines.append('\n')
    elif new:
        lines.append('<p style="font-size:12px;line-height:2;color:#aaa">')
        lines.append("  ".join(f'<span style="color:#f59e0b">${t}</span>' for t in new))
        lines.append('</p>\n')
    else:
        lines.append('<p style="color:#444;font-size:12px">None this week</p>\n')

    lines.append('</div>\n')

    # ── footer ────────────────────────────────────────────────────────────────
    if dashboard_url:
        footer = f'<a href="{dashboard_url}">View full dashboard →</a>'
    else:
        footer = 'Fintwit Weekly'
    lines.append(f'<div class="foot">{footer}</div>\n')
    lines.append('</div></body></html>')

    return "".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def send_weekly_email(week_start, week_end, metrics, new_following=None,
                      narrative=None, new_ticker_summaries=None, dashboard_url=None):
    resend.api_key = _api_key()

    html    = _html_body(week_start, week_end, metrics, narrative,
                         new_ticker_summaries, dashboard_url)
    subject = _subject(metrics.get("top", []), week_start)

    r = resend.Emails.send({
        "from":    _from_addr(),
        "to":      [os.environ["EMAIL_TO"]],
        "subject": subject,
        "html":    html,
    })
    return r


def send_alert_email(service, week_start, week_end, command, reason):
    """
    Send a STALL alert when the weekly run aborts because an upstream API
    (GetXAPI or Claude/Anthropic) rejected a call — typically credits exhausted.
    The body contains the exact terminal command to resume after topping up.
    """
    resend.api_key = _api_key()

    label = {"claude": "Claude / Anthropic", "getxapi": "GetXAPI"}.get(service, service)
    subject = f"⚠ Fintwit Weekly STALLED — {label} ({week_start})"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:24px;background:#0a0a0d;font-family:'Courier New',monospace;color:#e0e0e0">
  <div style="max-width:620px;margin:0 auto;border:1px solid #1e1e1e;background:#0a0a0d">
    <div style="padding:20px 24px;border-bottom:3px solid #ef4444">
      <h1 style="color:#ef4444;font-size:15px;margin:0;letter-spacing:.15em">FINTWIT WEEKLY — RUN STALLED</h1>
      <div style="color:#555;font-size:11px;margin-top:5px">{week_start} → {week_end}</div>
    </div>
    <div style="padding:24px;font-size:13px;line-height:1.7">
      <p>The weekly run stopped because a <b style="color:#f59e0b">{label}</b> API call was rejected.</p>
      <p style="color:#aaa"><b>Likely cause:</b> {reason}</p>
      <p>Top up <b>{label}</b> credits, then run this exact command to resume the week:</p>
      <pre style="background:#111;border-left:3px solid #f59e0b;padding:12px 14px;color:#22c55e;white-space:pre-wrap;font-size:13px;overflow-x:auto">{command}</pre>
      <p style="color:#666;font-size:11px;margin-top:18px">
        This week is logged as an error, so it will also auto-retry the next time you log in
        (the launchd catch-up guard) once credits are restored.
      </p>
    </div>
  </div>
</body></html>"""

    return resend.Emails.send({
        "from":    _from_addr(),
        "to":      [os.environ["EMAIL_TO"]],
        "subject": subject,
        "html":    html,
    })
