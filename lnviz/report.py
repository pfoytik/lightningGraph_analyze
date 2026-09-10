"""
report.py
---------
Renders analyze_snapshot()/resilience_summary() output (and, separately,
SnapshotStore history rows) into a single self-contained static HTML report:
matplotlib charts embedded as base64 PNGs, plus plain HTML tables for the
top-node rankings. No JS, no external assets -- safe to open offline or email.

Colors follow the project's validated categorical/sequential palette (see
the dataviz skill): fixed hue order for categorical series, single blue hue
for magnitude, never a rainbow.
"""
from __future__ import annotations

import base64
import html
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# -- palette (light-mode static report; see dataviz skill references/palette.md) --
SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SEQ_BLUE = "#2a78d6"
CATEGORICAL = {
    "random": "#2a78d6",       # slot 1 blue
    "degree": "#eb6834",       # slot 2 orange
    "capacity": "#1baf7a",     # slot 3 aqua
    "betweenness": "#eda100",  # slot 4 yellow
}
LORENZ_COLORS = {"Degree": "#2a78d6", "Capacity": "#eb6834"}
# Ordinal blue ramp for "one line per snapshot, ordered by time" overlays --
# lightest allowed step on the light surface is 250 for an ordinal encoding
# (see palette.md); darkest is the sequential ramp's own 700.
SEQUENTIAL_ORDINAL = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
                       "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 11,
    "text.color": INK_PRIMARY,
    "axes.edgecolor": BASELINE,
    "axes.labelcolor": INK_SECONDARY,
    "xtick.color": INK_MUTED,
    "ytick.color": INK_MUTED,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _clean_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(BASELINE)
    ax.spines["bottom"].set_color(BASELINE)
    ax.grid(axis="y", alpha=0.7)
    ax.grid(axis="x", visible=False)


def _time_colors(n: int) -> List[str]:
    """n colors spanning the ordinal ramp light->dark, oldest first, evenly
    spaced across the full ramp regardless of how many snapshots there are."""
    if n <= 1:
        return [SEQUENTIAL_ORDINAL[-1]]
    last = len(SEQUENTIAL_ORDINAL) - 1
    return [SEQUENTIAL_ORDINAL[round(i * last / (n - 1))] for i in range(n)]


def subsample_evenly(items: Sequence[Any], cap: int) -> List[Any]:
    """Evenly-spaced subsample of `items` down to at most `cap` entries,
    always keeping the first and last. Order-preserving. Used to keep
    multi-snapshot overlay charts legible when history has grown long --
    callers should log what got dropped rather than truncate silently."""
    n = len(items)
    if n <= cap:
        return list(items)
    if cap <= 1:
        return [items[-1]]
    idxs = sorted({round(i * (n - 1) / (cap - 1)) for i in range(cap)})
    return [items[i] for i in idxs]


def fig_histogram(values: Sequence[float], title: str, xlabel: str, log_x: bool = True) -> str:
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    vals = [v for v in values if v is not None]
    if log_x:
        vals_pos = [v for v in vals if v > 0]
        bins = 30
        if vals_pos:
            import numpy as np
            bins = np.logspace(np.log10(max(min(vals_pos), 1)), np.log10(max(vals_pos)), 30)
        ax.hist(vals_pos, bins=bins, color=SEQ_BLUE, edgecolor=SURFACE, linewidth=0.5)
        ax.set_xscale("log")
    else:
        ax.hist(vals, bins=30, color=SEQ_BLUE, edgecolor=SURFACE, linewidth=0.5)
    ax.set_title(title, color=INK_PRIMARY, fontsize=12, loc="left")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("node count")
    _clean_axes(ax)
    return _fig_to_b64(fig)


def _lorenz_points(values: Sequence[float]) -> Tuple[List[float], List[float]]:
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    total = sum(vals)
    xs = [0.0]
    ys = [0.0]
    cum = 0.0
    for i, v in enumerate(vals, start=1):
        cum += v
        xs.append(i / n)
        ys.append(cum / total if total else 0.0)
    return xs, ys


def fig_lorenz(series: Dict[str, Sequence[float]]) -> str:
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    ax.plot([0, 1], [0, 1], linestyle="--", linewidth=1.4, color=BASELINE, label="Perfect equality")
    for name, values in series.items():
        xs, ys = _lorenz_points(values)
        color = LORENZ_COLORS.get(name, SEQ_BLUE)
        ax.plot(xs, ys, linewidth=2.2, color=color, label=name)
    ax.set_title("Concentration (Lorenz curve)", color=INK_PRIMARY, fontsize=12, loc="left")
    ax.set_xlabel("cumulative share of nodes")
    ax.set_ylabel("cumulative share of value")
    ax.legend(frameon=False, loc="upper left")
    _clean_axes(ax)
    return _fig_to_b64(fig)


def fig_attack_curves(curves: Dict[str, List[Tuple[float, float]]]) -> str:
    fig, ax = plt.subplots(figsize=(5.6, 4.2))
    order = ["random", "degree", "capacity", "betweenness"]
    for strat in order:
        if strat not in curves:
            continue
        pts = curves[strat]
        xs = [p[0] * 100 for p in pts]
        ys = [p[1] * 100 for p in pts]
        color = CATEGORICAL.get(strat, SEQ_BLUE)
        ax.plot(xs, ys, linewidth=2.2, color=color, label=strat.capitalize())
    ax.set_title("Network fragmentation under node removal", color=INK_PRIMARY, fontsize=12, loc="left")
    ax.set_xlabel("% of nodes removed")
    ax.set_ylabel("% of nodes still in giant component")
    ax.set_ylim(0, 100)
    ax.legend(frameon=False, loc="upper right")
    _clean_axes(ax)
    return _fig_to_b64(fig)


def fig_trend(rows: List[Dict[str, Any]], field: str, title: str, ylabel: str,
              color: str = SEQ_BLUE, as_pct: bool = False) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    xs = [datetime.fromtimestamp(r["captured_at"], tz=timezone.utc) for r in rows]
    ys = [r[field] for r in rows]
    if as_pct:
        ys = [y * 100 if y is not None else None for y in ys]
    ax.plot(xs, ys, marker="o", markersize=4, linewidth=2.0, color=color)
    ax.set_title(title, color=INK_PRIMARY, fontsize=12, loc="left")
    ax.set_ylabel(ylabel)
    fig.autofmt_xdate()
    _clean_axes(ax)
    return _fig_to_b64(fig)


def fig_robustness_trend(rows: List[Dict[str, Any]]) -> str:
    """All four attack-simulation robustness indices over time on one chart
    -- same unit (R-index, 0-0.5), so one axis with a fixed-order categorical
    line per strategy is the right form (not four separate small multiples)."""
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    xs = [datetime.fromtimestamp(r["captured_at"], tz=timezone.utc) for r in rows]
    any_series = False
    for strat in ("random", "degree", "capacity", "betweenness"):
        field = f"robustness_{strat}"
        ys = [r.get(field) for r in rows]
        if all(y is None for y in ys):
            continue
        any_series = True
        ax.plot(xs, ys, marker="o", markersize=4, linewidth=2.0, color=CATEGORICAL[strat], label=strat.capitalize())
    ax.set_title("Robustness index over time, by attack strategy", color=INK_PRIMARY, fontsize=12, loc="left")
    ax.set_ylabel("R-index (0-0.5)")
    if any_series:
        ax.legend(frameon=False, loc="best", fontsize=9)
    fig.autofmt_xdate()
    _clean_axes(ax)
    return _fig_to_b64(fig)


def fig_concentration_evolution(snapshots: List[Tuple[str, Sequence[float]]], metric_label: str) -> str:
    """One Lorenz curve per snapshot, colored oldest(light)->newest(dark), to
    show whether concentration of `metric_label` is tightening or loosening
    over time. `snapshots` is [(short date label, per-node value list), ...]
    in chronological order."""
    fig, ax = plt.subplots(figsize=(5.6, 4.6))
    ax.plot([0, 1], [0, 1], linestyle="--", linewidth=1.4, color=BASELINE)
    colors = _time_colors(len(snapshots))
    for (label, values), color in zip(snapshots, colors):
        xs, ys = _lorenz_points(values)
        ax.plot(xs, ys, linewidth=2.0, color=color, label=label)
    ax.set_title(f"{metric_label} concentration over time", color=INK_PRIMARY, fontsize=12, loc="left")
    ax.set_xlabel("cumulative share of nodes")
    ax.set_ylabel(f"cumulative share of {metric_label.lower()}")
    ax.legend(frameon=False, loc="upper left", fontsize=8, ncol=2 if len(snapshots) > 5 else 1)
    _clean_axes(ax)
    return _fig_to_b64(fig)


def fig_attack_evolution(snapshots: List[Tuple[str, List[Tuple[float, float]]]], strategy_label: str) -> str:
    """One fragmentation curve per snapshot for a single attack strategy,
    colored oldest(light)->newest(dark), to show whether the network is
    getting more or less resilient to that kind of attack over time."""
    fig, ax = plt.subplots(figsize=(5.6, 4.6))
    colors = _time_colors(len(snapshots))
    for (label, curve), color in zip(snapshots, colors):
        xs = [p[0] * 100 for p in curve]
        ys = [p[1] * 100 for p in curve]
        ax.plot(xs, ys, linewidth=2.0, color=color, label=label)
    ax.set_title(f"Fragmentation under {strategy_label} attack, over time", color=INK_PRIMARY, fontsize=12, loc="left")
    ax.set_xlabel("% of nodes removed")
    ax.set_ylabel("% of nodes still in giant component")
    ax.set_ylim(0, 100)
    ax.legend(frameon=False, loc="upper right", fontsize=8, ncol=2 if len(snapshots) > 5 else 1)
    _clean_axes(ax)
    return _fig_to_b64(fig)


def _table(rows: List[Dict[str, Any]], columns: List[Tuple[str, str]]) -> str:
    """columns: list of (key, header). Renders a plain HTML table."""
    if not rows:
        return "<p class='muted'>No data.</p>"
    head = "".join(f"<th>{html.escape(h)}</th>" for _, h in columns)
    body_rows = []
    for r in rows:
        cells = []
        for key, _ in columns:
            v = r.get(key, "")
            if isinstance(v, float):
                v = f"{v:,.4f}" if abs(v) < 1 else f"{v:,.2f}"
            elif isinstance(v, int):
                v = f"{v:,}"
            cells.append(f"<td>{html.escape(str(v))}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


STYLE = f"""
<style>
  body {{ background:{PAGE}; color:{INK_PRIMARY}; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
          margin:0; padding:32px; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  h2 {{ font-size: 16px; margin-top: 40px; border-bottom: 1px solid {GRID}; padding-bottom: 6px; }}
  .subtitle {{ color:{INK_SECONDARY}; font-size: 13px; margin-top:0; }}
  .tiles {{ display:flex; flex-wrap:wrap; gap:12px; margin: 18px 0; }}
  .tile {{ background:{SURFACE}; border:1px solid {GRID}; border-radius:8px; padding:14px 18px; min-width:160px; }}
  .tile .value {{ font-size: 22px; font-weight:600; font-variant-numeric: tabular-nums; }}
  .tile .label {{ font-size: 12px; color:{INK_MUTED}; margin-top:2px; }}
  .charts {{ display:flex; flex-wrap:wrap; gap:20px; }}
  .charts figure {{ margin:0; background:{SURFACE}; border:1px solid {GRID}; border-radius:8px; padding:10px; }}
  .charts img {{ display:block; max-width:100%; }}
  table {{ border-collapse: collapse; width:100%; font-size: 13px; background:{SURFACE}; }}
  th, td {{ text-align:left; padding:6px 10px; border-bottom: 1px solid {GRID}; }}
  th {{ color:{INK_MUTED}; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:0.03em; }}
  td {{ font-variant-numeric: tabular-nums; }}
  .muted {{ color:{INK_MUTED}; }}
  .grid2 {{ display:grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  @media (max-width: 900px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
</style>
"""


def _tile(value: str, label: str) -> str:
    return f"<div class='tile'><div class='value'>{html.escape(str(value))}</div><div class='label'>{html.escape(label)}</div></div>"


def render_snapshot_report(full: Dict[str, Any], out_path: str, title: str = "Lightning Graph Snapshot Report") -> None:
    basic = full["basic"]
    degree = full["degree"]
    capacity = full["capacity"]
    structure = full["structure"]
    centrality = full["centrality"]
    dominance = full["dominance"]
    res = full.get("resilience")

    captured = full.get("captured_at")
    captured_str = datetime.fromtimestamp(captured, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if captured else "unknown"

    # Full per-node value lists (not just the top-N tables) are needed for the
    # histograms/Lorenz curve -- analyze_snapshot() stashes them under '_values'.
    deg_values = degree.get("_values")
    cap_values = capacity.get("_values")

    charts = []
    if deg_values:
        charts.append(("Degree distribution", fig_histogram(deg_values, "Channel count per node", "channels (log scale)")))
    if cap_values:
        charts.append(("Capacity distribution", fig_histogram(cap_values, "Capacity per node (sat)", "sat (log scale)")))
    if deg_values and cap_values:
        charts.append(("Concentration", fig_lorenz({"Degree": deg_values, "Capacity": cap_values})))
    if res:
        charts.append(("Attack simulation", fig_attack_curves(res["attack_simulation"]["curves"])))

    def rank_table(rows, value_label):
        return _table(rows, [("alias", "Node"), ("value", value_label)])

    spof = res["single_points_of_failure"] if res else None
    att_r = res["attack_simulation"]["robustness_index"] if res else {}

    html_parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title>", STYLE, "</head><body><div class='wrap'>",
        f"<h1>{html.escape(title)}</h1>",
        f"<p class='subtitle'>Captured: {captured_str} &middot; Source: {html.escape(str(full.get('source_path') or ''))}</p>",
        "<div class='tiles'>",
        _tile(f"{basic['num_nodes']:,}", "Nodes"),
        _tile(f"{basic['num_channels']:,}", "Channels"),
        _tile(f"{basic['total_capacity_btc']:.2f} BTC", "Total capacity"),
        _tile(f"{structure['largest_component_fraction']*100:.1f}%", "In giant component"),
        _tile(f"{degree['gini']:.3f}", "Degree Gini"),
        _tile(f"{capacity['gini']:.3f}", "Capacity Gini"),
        _tile(f"{structure['main_core_number']}", "Main k-core"),
        _tile(f"{structure.get('approx_diameter_lcc')}", "Approx. diameter"),
        "</div>",
    ]

    if charts:
        html_parts.append("<h2>Distributions &amp; resilience</h2><div class='charts'>")
        for cap_title, b64 in charts:
            html_parts.append(f"<figure><figcaption class='muted' style='margin-bottom:6px'>{html.escape(cap_title)}</figcaption>"
                               f"<img src='data:image/png;base64,{b64}'/></figure>")
        html_parts.append("</div>")

    if att_r:
        html_parts.append("<h2>Robustness index (area under fragmentation curve, 0&ndash;0.5, higher = tougher)</h2><div class='tiles'>")
        for strat in ("random", "degree", "capacity", "betweenness"):
            if strat in att_r:
                html_parts.append(_tile(f"{att_r[strat]:.3f}", strat.capitalize()))
        html_parts.append("</div>")

    html_parts.append("<div class='grid2'>")
    html_parts.append("<div><h2>Top nodes by degree</h2>" + rank_table(degree["top_nodes"][:15], "Channels") + "</div>")
    html_parts.append("<div><h2>Top nodes by capacity</h2>" + rank_table(capacity["top_nodes"][:15], "Sat") + "</div>")
    html_parts.append("<div><h2>Top nodes by betweenness</h2>" + rank_table(centrality["betweenness_top_nodes"][:15], "Betweenness") + "</div>")
    html_parts.append("<div><h2>Composite hub dominance</h2>" + rank_table(dominance["top_nodes"][:15], "Percentile score") + "</div>")
    html_parts.append("</div>")

    if spof:
        html_parts.append("<h2>Single points of failure (articulation points)</h2>")
        html_parts.append(f"<p class='muted'>{spof['articulation_point_count']:,} articulation points &middot; "
                           f"{spof['bridge_count']:,} bridge channels on the giant component.</p>")
        html_parts.append(_table(spof["articulation_points"][:15], [("alias", "Node"), ("nodes_stranded_if_removed", "Nodes stranded if removed")]))

    html_parts.append("</div></body></html>")

    with open(out_path, "w") as f:
        f.write("".join(html_parts))


def render_history_report(rows: List[Dict[str, Any]], out_path: str,
                           diff: Optional[Dict[str, Any]] = None,
                           title: str = "Lightning Graph History Report",
                           report_links: Optional[Dict[int, str]] = None,
                           overlay_reports: Optional[List[Tuple[str, Dict[str, Any]]]] = None,
                           overlay_dropped: int = 0,
                           overlay_strategy: str = "degree") -> None:
    """
    report_links: {snapshot_id: relative path to that snapshot's own HTML
        report}, if you want a "view report" link per row in the table.
    overlay_reports: [(short label, full analyze_snapshot()+resilience dict), ...]
        in chronological order, already subsampled to a legible count -- drives
        the Lorenz-curve-over-time and attack-curve-over-time overlay charts.
    overlay_dropped: how many snapshots were left out of `overlay_reports` by
        subsampling (surfaced in the report so truncation is never silent).
    """
    charts = []
    if len(rows) >= 2:
        charts = [
            ("Node & channel count", fig_trend(rows, "num_nodes", "Node count over time", "nodes")),
            ("Channels", fig_trend(rows, "num_channels", "Channel count over time", "channels", color=CATEGORICAL["degree"])),
            ("Total capacity (sat)", fig_trend(rows, "total_capacity_sat", "Total network capacity over time", "sat", color=CATEGORICAL["capacity"])),
            ("Giant component", fig_trend(rows, "largest_component_fraction", "Giant component share over time", "% of nodes", as_pct=True)),
            ("Degree Gini", fig_trend(rows, "gini_degree", "Degree concentration (Gini) over time", "gini", color=CATEGORICAL["degree"])),
            ("Capacity Gini", fig_trend(rows, "gini_capacity", "Capacity concentration (Gini) over time", "gini", color=CATEGORICAL["capacity"])),
            ("Avg. shortest path (giant component)", fig_trend(rows, "approx_avg_shortest_path_length_lcc", "Average path length over time", "hops", color=CATEGORICAL["capacity"])),
            ("Degree assortativity", fig_trend(rows, "degree_assortativity", "Degree assortativity over time", "coefficient", color=CATEGORICAL["betweenness"])),
            ("Articulation points", fig_trend(rows, "articulation_point_count", "Single points of failure over time", "count", color=CATEGORICAL["betweenness"])),
            ("Robustness, all strategies", fig_robustness_trend(rows)),
        ]

    evolution_charts = []
    if overlay_reports and len(overlay_reports) >= 2:
        cap_series = [(lbl, f["capacity"]["_values"]) for lbl, f in overlay_reports if f.get("capacity", {}).get("_values")]
        deg_series = [(lbl, f["degree"]["_values"]) for lbl, f in overlay_reports if f.get("degree", {}).get("_values")]
        attack_series = [
            (lbl, f["resilience"]["attack_simulation"]["curves"][overlay_strategy])
            for lbl, f in overlay_reports
            if f.get("resilience", {}).get("attack_simulation", {}).get("curves", {}).get(overlay_strategy)
        ]
        if len(cap_series) >= 2:
            evolution_charts.append(("Capacity concentration, over time", fig_concentration_evolution(cap_series, "Capacity")))
        if len(deg_series) >= 2:
            evolution_charts.append(("Degree concentration, over time", fig_concentration_evolution(deg_series, "Degree")))
        if len(attack_series) >= 2:
            evolution_charts.append((f"{overlay_strategy.capitalize()}-attack fragmentation, over time",
                                      fig_attack_evolution(attack_series, overlay_strategy)))

    html_parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title>", STYLE, "</head><body><div class='wrap'>",
        f"<h1>{html.escape(title)}</h1>",
        f"<p class='subtitle'>{len(rows)} snapshot(s) tracked.</p>",
    ]

    if rows:
        latest = rows[-1]
        html_parts.append("<div class='tiles'>")
        html_parts.append(_tile(f"{latest['num_nodes']:,}", "Nodes (latest)"))
        html_parts.append(_tile(f"{latest['num_channels']:,}", "Channels (latest)"))
        html_parts.append(_tile(f"{latest['gini_capacity']:.3f}", "Capacity Gini (latest)"))
        html_parts.append(_tile(f"{latest['largest_component_fraction']*100:.1f}%", "Giant component (latest)"))
        html_parts.append("</div>")

    if charts:
        html_parts.append("<h2>Trends</h2><div class='charts'>")
        for cap_title, b64 in charts:
            html_parts.append(f"<figure><figcaption class='muted' style='margin-bottom:6px'>{html.escape(cap_title)}</figcaption>"
                               f"<img src='data:image/png;base64,{b64}'/></figure>")
        html_parts.append("</div>")
    elif rows:
        html_parts.append("<p class='muted'>Need at least 2 snapshots to plot trends -- keep ingesting.</p>")

    if evolution_charts:
        html_parts.append("<h2>How the network's shape has changed</h2>")
        note = f"Overlaying {len(overlay_reports)} of {len(overlay_reports) + overlay_dropped} tracked snapshots"
        if overlay_dropped:
            note += " (evenly sampled across time to keep the chart legible)"
        note += ", oldest = lightest line, newest = darkest."
        html_parts.append(f"<p class='muted'>{html.escape(note)}</p>")
        html_parts.append("<div class='charts'>")
        for cap_title, b64 in evolution_charts:
            html_parts.append(f"<figure><figcaption class='muted' style='margin-bottom:6px'>{html.escape(cap_title)}</figcaption>"
                               f"<img src='data:image/png;base64,{b64}'/></figure>")
        html_parts.append("</div>")

    html_parts.append("<h2>Snapshots</h2>")
    cols = [("when", "Captured"), ("label", "Label"), ("num_nodes", "Nodes"),
            ("num_channels", "Channels"), ("gini_capacity", "Capacity Gini"),
            ("largest_component_fraction", "Giant component")]
    if report_links:
        cols = cols + [("report", "Report")]
    head = "".join(f"<th>{h}</th>" for _, h in cols)
    body_rows = []
    for r in rows:
        vals = {
            "when": datetime.fromtimestamp(r["captured_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "label": r["label"], "num_nodes": f"{r['num_nodes']:,}", "num_channels": f"{r['num_channels']:,}",
            "gini_capacity": f"{r['gini_capacity']:.3f}",
            "largest_component_fraction": f"{r['largest_component_fraction']*100:.1f}%",
        }
        cells = [f"<td>{html.escape(str(vals[key]))}</td>" for key, _ in cols if key != "report"]
        if report_links:
            link = report_links.get(r["id"])
            cell = f"<a href='{html.escape(link)}'>view</a>" if link else "<span class='muted'>&ndash;</span>"
            cells.append(f"<td>{cell}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    html_parts.append(f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>")

    if diff:
        html_parts.append(f"<h2>Latest change: {html.escape(diff['from']['label'])} &rarr; {html.escape(diff['to']['label'])}</h2>")
        n, c, cap = diff["nodes"], diff["channels"], diff["capacity_sat"]
        html_parts.append("<div class='tiles'>")
        html_parts.append(_tile(f"+{n['added']} / -{n['removed']}", "Node churn"))
        html_parts.append(_tile(f"+{c['added']} / -{c['removed']}", "Channel churn"))
        delta_pct = f"{cap['delta_pct']:.2f}%" if cap.get("delta_pct") is not None else "n/a"
        html_parts.append(_tile(f"{cap['delta']:+,} sat ({delta_pct})", "Capacity change"))
        html_parts.append("</div>")

    html_parts.append("</div></body></html>")
    with open(out_path, "w") as f:
        f.write("".join(html_parts))
