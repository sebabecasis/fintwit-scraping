#!/usr/bin/env python3
"""
graph.py — build a knowledge graph from fintwit DB and write dashboard/graph.html

Nodes : accounts · tickers · themes
Edges : account -[MENTIONS {sentiment, conviction, count}]-> ticker
        ticker  -[BELONGS_TO]->                               theme
        account -[DISCUSSES]->                                theme
"""

import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent
_cfg = yaml.safe_load((_ROOT / "config" / "config.yaml").read_text())
_DB_PATH = _ROOT / _cfg["paths"]["db"]
_DASH = _ROOT / _cfg["paths"]["dashboard"]


# ── data helpers ──────────────────────────────────────────────────────────────

def _available_weeks():
    with sqlite3.connect(_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT DISTINCT week_start FROM mentions ORDER BY week_start DESC"
        ).fetchall()
    return [r[0] for r in rows]


def build_graph(week_start=None, min_count=2):
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row

    where = "WHERE m.sentiment IS NOT NULL AND m.sentiment != 'unclear'"
    params = []
    if week_start:
        where += " AND m.week_start = ?"
        params = [week_start]

    rows = conn.execute(f"""
        SELECT a.username, m.ticker, m.sentiment, m.conviction
        FROM   mentions  m
        JOIN   tweets    t ON t.id      = m.tweet_id
        JOIN   accounts  a ON a.user_id = t.user_id
        {where}
    """, params).fetchall()

    # aggregate per (username, ticker)
    agg = defaultdict(lambda: {
        "count": 0, "bullish": 0, "bearish": 0, "neutral": 0,
        "conv_sum": 0, "conv_n": 0,
    })
    for r in rows:
        key = (r["username"], r["ticker"])
        d = agg[key]
        d["count"] += 1
        d[r["sentiment"]] += 1
        if r["conviction"]:
            d["conv_sum"] += r["conviction"]
            d["conv_n"]   += 1

    # filter and emit mention edges
    mention_edges = []
    account_counts = defaultdict(int)
    ticker_counts  = defaultdict(int)

    for (username, ticker), d in agg.items():
        if d["count"] < min_count:
            continue
        dom = max(["bullish", "bearish", "neutral"], key=lambda s: d[s])
        conv_avg = round(d["conv_sum"] / d["conv_n"], 1) if d["conv_n"] else 3.0
        mention_edges.append({
            "source":       f"account:{username}",
            "target":       f"ticker:{ticker}",
            "type":         "MENTIONS",
            "sentiment":    dom,
            "conviction":   conv_avg,
            "count":        d["count"],
            "bull":         d["bullish"],
            "bear":         d["bearish"],
            "neutral":      d["neutral"],
        })
        account_counts[username] += d["count"]
        ticker_counts[ticker]    += d["count"]

    # theme edges (gracefully empty if themes table has no tickers/accounts)
    theme_rows = conn.execute(
        "SELECT theme, tickers, accounts FROM themes "
        "WHERE (tickers IS NOT NULL AND tickers != '') "
        "   OR (accounts IS NOT NULL AND accounts != '')"
    ).fetchall()
    conn.close()

    ticker_set  = set(ticker_counts)
    account_set = set(account_counts)
    theme_tickers  = defaultdict(set)
    theme_accounts = defaultdict(set)

    for tr in theme_rows:
        theme = tr["theme"]
        for t in [x.strip() for x in (tr["tickers"] or "").split(",") if x.strip()]:
            if t in ticker_set:
                theme_tickers[theme].add(t)
        for a in [x.strip() for x in (tr["accounts"] or "").split(",") if x.strip()]:
            if a in account_set:
                theme_accounts[theme].add(a)

    theme_edges = []
    for theme, tickers in theme_tickers.items():
        for t in tickers:
            theme_edges.append({"source": f"ticker:{t}",  "target": f"theme:{theme}", "type": "BELONGS_TO"})
    for theme, accs in theme_accounts.items():
        for a in accs:
            theme_edges.append({"source": f"account:{a}", "target": f"theme:{theme}", "type": "DISCUSSES"})

    # ── nodes ─────────────────────────────────────────────────────────────────
    active_themes = {
        e["target"][6:]
        for e in theme_edges
        if e["target"].startswith("theme:")
    }
    nodes = []
    for username in account_counts:
        cnt = account_counts[username]
        nodes.append({"id": f"account:{username}", "type": "account",
                      "label": username, "count": cnt,
                      "radius": round(8 + math.log1p(cnt) * 3, 1)})
    for ticker in ticker_counts:
        cnt = ticker_counts[ticker]
        nodes.append({"id": f"ticker:{ticker}", "type": "ticker",
                      "label": ticker, "count": cnt,
                      "radius": round(8 + math.log1p(cnt) * 3, 1)})
    for theme in active_themes:
        nodes.append({"id": f"theme:{theme}", "type": "theme",
                      "label": theme, "count": 0, "radius": 10})

    return {"nodes": nodes, "edges": mention_edges + theme_edges}


# ── HTML template ─────────────────────────────────────────────────────────────

def _render_html(graph_data, week_label, min_count):
    graph_json = json.dumps(graph_data, separators=(",", ":"))
    n_nodes = len(graph_data["nodes"])
    n_edges = len(graph_data["edges"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fintwit Knowledge Graph</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
:root {{
  --bg:     #0a0a0d;
  --bg2:    #101014;
  --bg3:    #16161b;
  --border: #202026;
  --accent: #f59e0b;
  --text:   #d4d4dc;
  --muted:  #55556a;
  --dim:    #38384a;
  --bull:   #22c55e;
  --bear:   #ef4444;
  --neut:   #6b7280;
  --acct:   #3b82f6;
  --theme:  #22c55e;
  --mono:   'Courier New', Consolas, monospace;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--text); font-family: var(--mono); display: flex; height: 100vh; overflow: hidden; }}

/* sidebar */
#sidebar {{
  width: 260px; min-width: 260px; background: var(--bg2);
  border-right: 1px solid var(--border); display: flex; flex-direction: column;
  padding: 16px; gap: 16px; overflow-y: auto;
}}
#sidebar h1 {{ font-size: 13px; color: var(--accent); letter-spacing: .08em; text-transform: uppercase; }}
#sidebar .meta {{ font-size: 11px; color: var(--muted); line-height: 1.6; }}
.section-label {{ font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 6px; }}
.toggle-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; cursor: pointer; font-size: 12px; }}
.toggle-row input {{ cursor: pointer; accent-color: var(--accent); }}
.dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
.dot-account {{ background: var(--acct); }}
.dot-ticker  {{ background: var(--accent); }}
.dot-theme   {{ background: var(--theme); }}
.dot-bull    {{ background: var(--bull); }}
.dot-bear    {{ background: var(--bear); }}
.dot-neut    {{ background: var(--neut); }}
.dot-struct  {{ background: var(--dim); border: 1px solid var(--muted); }}
#search {{
  width: 100%; background: var(--bg3); border: 1px solid var(--border);
  color: var(--text); font-family: var(--mono); font-size: 12px;
  padding: 6px 8px; outline: none; border-radius: 2px;
}}
#search:focus {{ border-color: var(--accent); }}
#search-results {{ font-size: 11px; color: var(--muted); min-height: 14px; }}
label[for=conv-min] {{ font-size: 12px; }}
#conv-min {{ width: 100%; accent-color: var(--accent); }}
#conv-val {{ font-size: 11px; color: var(--accent); }}
.divider {{ border: none; border-top: 1px solid var(--border); }}
#back-link {{ font-size: 11px; color: var(--muted); text-decoration: none; }}
#back-link:hover {{ color: var(--accent); }}

/* graph area */
#graph {{ flex: 1; position: relative; overflow: hidden; }}
svg {{ width: 100%; height: 100%; }}
.link {{ stroke-opacity: .55; }}
.link-MENTIONS-bullish {{ stroke: var(--bull); }}
.link-MENTIONS-bearish {{ stroke: var(--bear); }}
.link-MENTIONS-neutral {{ stroke: var(--neut); }}
.link-BELONGS_TO, .link-DISCUSSES {{ stroke: var(--dim); stroke-dasharray: 4 3; }}
.node {{ cursor: pointer; }}
.node circle, .node rect {{ stroke-width: 1.5; transition: opacity .15s; }}
.node text {{ pointer-events: none; user-select: none; }}
.dimmed {{ opacity: .08 !important; }}

/* tooltip */
#tooltip {{
  position: absolute; background: var(--bg2); border: 1px solid var(--border);
  padding: 10px 12px; font-size: 11px; line-height: 1.7; pointer-events: none;
  max-width: 220px; display: none; z-index: 10;
}}
#tooltip .tt-label {{ font-size: 13px; color: var(--accent); margin-bottom: 4px; }}
#tooltip .tt-type  {{ color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .06em; }}
#tooltip .bull {{ color: var(--bull); }}
#tooltip .bear {{ color: var(--bear); }}
#tooltip .neut {{ color: var(--neut); }}
</style>
</head>
<body>

<div id="sidebar">
  <div>
    <h1>Knowledge Graph</h1>
    <div class="meta">
      Week: <span style="color:var(--accent)">{week_label}</span><br>
      Min mentions: <span style="color:var(--accent)">{min_count}</span><br>
      Nodes: <span style="color:var(--text)">{n_nodes}</span> &nbsp; Edges: <span style="color:var(--text)">{n_edges}</span>
    </div>
  </div>

  <div>
    <div class="section-label">Search</div>
    <input id="search" type="text" placeholder="account / ticker / theme…">
    <div id="search-results"></div>
  </div>

  <div>
    <div class="section-label">Node types</div>
    <label class="toggle-row"><input type="checkbox" id="show-account" checked> <span class="dot dot-account"></span> Accounts</label>
    <label class="toggle-row"><input type="checkbox" id="show-ticker"  checked> <span class="dot dot-ticker"></span>  Tickers</label>
    <label class="toggle-row"><input type="checkbox" id="show-theme"   checked> <span class="dot dot-theme"></span>   Themes</label>
  </div>

  <div>
    <div class="section-label">Edge sentiment</div>
    <label class="toggle-row"><input type="checkbox" id="show-bull" checked> <span class="dot dot-bull"></span> Bullish</label>
    <label class="toggle-row"><input type="checkbox" id="show-bear" checked> <span class="dot dot-bear"></span> Bearish</label>
    <label class="toggle-row"><input type="checkbox" id="show-neut" checked> <span class="dot dot-neut"></span> Neutral</label>
    <label class="toggle-row"><input type="checkbox" id="show-struct" checked> <span class="dot dot-struct"></span> Structural</label>
  </div>

  <div>
    <div class="section-label">Min conviction</div>
    <label for="conv-min" style="display:flex;justify-content:space-between;align-items:center">
      <span>Threshold</span> <span id="conv-val">1</span>
    </label>
    <input id="conv-min" type="range" min="1" max="5" step="0.5" value="1">
  </div>

  <hr class="divider">
  <div class="meta" style="font-size:10px;color:var(--muted)">
    Click node → highlight neighbours<br>
    Double-click canvas → reset<br>
    Scroll → zoom · Drag → pan
  </div>
  <a id="back-link" href="index.html">← dashboard</a>
</div>

<div id="graph">
  <svg id="svg"></svg>
  <div id="tooltip"></div>
</div>

<script>
const GRAPH_DATA = {graph_json};

// ── state ────────────────────────────────────────────────────────────────────
let activeNode = null;
const filters = {{
  account: true, ticker: true, theme: true,
  bull: true, bear: true, neut: true, struct: true,
  convMin: 1,
}};

// ── svg setup ────────────────────────────────────────────────────────────────
const svg = d3.select("#svg");
const g   = svg.append("g");

const zoom = d3.zoom()
  .scaleExtent([.05, 4])
  .on("zoom", e => g.attr("transform", e.transform));
svg.call(zoom);
svg.on("dblclick.zoom", null);
svg.on("dblclick", () => resetHighlight());

// ── visible data ─────────────────────────────────────────────────────────────
function visibleEdges() {{
  return GRAPH_DATA.edges.filter(e => {{
    if (e.type === "MENTIONS") {{
      if (e.sentiment === "bullish" && !filters.bull) return false;
      if (e.sentiment === "bearish" && !filters.bear) return false;
      if (e.sentiment === "neutral" && !filters.neut) return false;
      if (e.conviction < filters.convMin)             return false;
    }} else {{
      if (!filters.struct) return false;
    }}
    return true;
  }});
}}

function visibleNodeIds(edges) {{
  const ids = new Set();
  edges.forEach(e => {{ ids.add(e.source.id || e.source); ids.add(e.target.id || e.target); }});
  return ids;
}}

function visibleNodes(nodeIds) {{
  return GRAPH_DATA.nodes.filter(n => {{
    if (!nodeIds.has(n.id)) return false;
    if (n.type === "account" && !filters.account) return false;
    if (n.type === "ticker"  && !filters.ticker)  return false;
    if (n.type === "theme"   && !filters.theme)   return false;
    return true;
  }});
}}

// ── simulation ────────────────────────────────────────────────────────────────
let simulation, linkSel, nodeSel;

function buildGraph() {{
  g.selectAll("*").remove();
  activeNode = null;

  const edges = visibleEdges();
  const nodeIds = visibleNodeIds(edges);
  const nodes = visibleNodes(nodeIds);
  const nodeMap = new Map(nodes.map(n => [n.id, n]));
  const edges2 = edges.filter(e => nodeMap.has(e.source.id || e.source) && nodeMap.has(e.target.id || e.target));

  simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(edges2)
      .id(d => d.id)
      .distance(d => d.type === "MENTIONS" ? 80 : 120)
      .strength(.4))
    .force("charge", d3.forceManyBody().strength(d => -80 - d.radius * 6))
    .force("center", d3.forceCenter(
      parseFloat(svg.style("width"))  / 2 || window.innerWidth  / 2,
      parseFloat(svg.style("height")) / 2 || window.innerHeight / 2))
    .force("collide", d3.forceCollide(d => d.radius + 4))
    .alphaDecay(.025);

  // edges
  linkSel = g.append("g").attr("class", "links")
    .selectAll("line").data(edges2).join("line")
    .attr("class", d => `link link-${{d.type}}${{d.type === "MENTIONS" ? "-" + d.sentiment : ""}}`)
    .attr("stroke-width", d => d.type === "MENTIONS" ? Math.sqrt(d.count) * .8 + .5 : .8);

  // nodes
  const nodeG = g.append("g").attr("class", "nodes")
    .selectAll("g").data(nodes).join("g")
    .attr("class", "node")
    .call(d3.drag()
      .on("start", dragStart)
      .on("drag",  dragged)
      .on("end",   dragEnd))
    .on("click",     (e, d) => {{ e.stopPropagation(); highlightNode(d, edges2); }})
    .on("mouseover", (e, d) => showTooltip(e, d, edges2))
    .on("mousemove", moveTooltip)
    .on("mouseout",  hideTooltip);

  nodeG.each(function(d) {{
    const sel = d3.select(this);
    if (d.type === "theme") {{
      sel.append("rect")
        .attr("rx", 3).attr("ry", 3)
        .attr("width",  d.radius * 2)
        .attr("height", d.radius * 1.4)
        .attr("x", -d.radius).attr("y", -d.radius * .7)
        .attr("fill", "#22c55e22").attr("stroke", "#22c55e");
    }} else {{
      sel.append("circle")
        .attr("r", d.radius)
        .attr("fill", d.type === "account" ? "#3b82f620" : "#f59e0b20")
        .attr("stroke", d.type === "account" ? "#3b82f6" : "#f59e0b");
    }}
    sel.append("text")
      .attr("dy", d.type === "theme" ? ".35em" : ".35em")
      .attr("text-anchor", "middle")
      .attr("font-size", d.type === "theme" ? "9px" : d.radius > 14 ? "10px" : "9px")
      .attr("fill", "#d4d4dc")
      .text(d.label.length > 18 ? d.label.slice(0, 16) + "…" : d.label);
  }});

  nodeSel = nodeG;

  simulation.on("tick", () => {{
    linkSel
      .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
      .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
    nodeSel.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
  }});
}}

// ── drag ──────────────────────────────────────────────────────────────────────
function dragStart(e, d) {{
  if (!e.active) simulation.alphaTarget(.1).restart();
  d.fx = d.x; d.fy = d.y;
}}
function dragged(e, d)  {{ d.fx = e.x; d.fy = e.y; }}
function dragEnd(e, d)  {{ if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }}

// ── highlight ────────────────────────────────────────────────────────────────
function highlightNode(d, edges) {{
  if (activeNode === d.id) {{ resetHighlight(); return; }}
  activeNode = d.id;
  const neighbours = new Set([d.id]);
  edges.forEach(e => {{
    const s = e.source.id || e.source, t = e.target.id || e.target;
    if (s === d.id) neighbours.add(t);
    if (t === d.id) neighbours.add(s);
  }});
  nodeSel.classed("dimmed", n => !neighbours.has(n.id));
  linkSel.classed("dimmed", e => {{
    const s = e.source.id || e.source, t = e.target.id || e.target;
    return !neighbours.has(s) || !neighbours.has(t);
  }});
}}
function resetHighlight() {{
  activeNode = null;
  nodeSel?.classed("dimmed", false);
  linkSel?.classed("dimmed", false);
}}

// ── tooltip ───────────────────────────────────────────────────────────────────
const tip = document.getElementById("tooltip");
function showTooltip(event, d, edges) {{
  const neighbours = [];
  edges.forEach(e => {{
    const s = e.source.id || e.source, t = e.target.id || e.target;
    if (s === d.id) neighbours.push({{id: t,  type: e.type, sent: e.sentiment}});
    if (t === d.id) neighbours.push({{id: s,  type: e.type, sent: e.sentiment}});
  }});
  const topN = neighbours.slice(0, 5).map(n => {{
    const lbl = n.id.split(":").slice(1).join(":");
    const cls = n.sent === "bullish" ? "bull" : n.sent === "bearish" ? "bear" : "neut";
    return `<span class="${{cls}}">${{lbl}}</span>`;
  }}).join(", ");

  let body = `<div class="tt-type">${{d.type}}</div><div class="tt-label">${{d.label}}</div>`;
  if (d.count) body += `Mentions: ${{d.count}}<br>`;
  if (topN)    body += `Links: ${{topN}}`;
  tip.innerHTML = body;
  tip.style.display = "block";
  moveTooltip(event);
}}
function moveTooltip(event) {{
  const x = event.offsetX, y = event.offsetY;
  tip.style.left = (x + 14) + "px";
  tip.style.top  = (y - 10) + "px";
}}
function hideTooltip() {{ tip.style.display = "none"; }}

// ── search ────────────────────────────────────────────────────────────────────
document.getElementById("search").addEventListener("input", function() {{
  const q = this.value.toLowerCase().trim();
  const res = document.getElementById("search-results");
  if (!q) {{ res.textContent = ""; resetHighlight(); return; }}
  const match = GRAPH_DATA.nodes.find(n => n.label.toLowerCase().includes(q));
  if (match) {{
    res.textContent = "→ " + match.label;
    const edges = visibleEdges();
    const nodeIds = visibleNodeIds(edges);
    if (nodeIds.has(match.id)) highlightNode(match, edges);
  }} else {{
    res.textContent = "no match";
    resetHighlight();
  }}
}});

// ── filter controls ───────────────────────────────────────────────────────────
["account","ticker","theme"].forEach(t => {{
  document.getElementById("show-" + t).addEventListener("change", function() {{
    filters[t] = this.checked; buildGraph();
  }});
}});
[["show-bull","bull"],["show-bear","bear"],["show-neut","neut"],["show-struct","struct"]].forEach(([id,k]) => {{
  document.getElementById(id).addEventListener("change", function() {{
    filters[k] = this.checked; buildGraph();
  }});
}});
const convSlider = document.getElementById("conv-min");
const convVal    = document.getElementById("conv-val");
convSlider.addEventListener("input", function() {{
  filters.convMin = +this.value;
  convVal.textContent = this.value;
  buildGraph();
}});

// ── init ──────────────────────────────────────────────────────────────────────
buildGraph();
</script>
</body>
</html>"""


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    weeks = _available_weeks()
    print(f"\nAvailable weeks: {', '.join(weeks)}")
    print("Enter a week (YYYY-MM-DD) or press Enter for all data: ", end="")
    raw = input().strip()
    week_start = raw if raw in weeks else None
    week_label = week_start or "all"

    print("Min mention count per account→ticker edge (default 2): ", end="")
    raw2 = input().strip()
    min_count = int(raw2) if raw2.isdigit() else 2

    print("\nBuilding graph...", end="", flush=True)
    graph_data = build_graph(week_start=week_start, min_count=min_count)
    n_nodes = len(graph_data["nodes"])
    n_edges = len(graph_data["edges"])
    print(f" {n_nodes} nodes · {n_edges} edges")

    html = _render_html(graph_data, week_label, min_count)
    out = _DASH / "graph.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\nWritten → {out}")


if __name__ == "__main__":
    main()
