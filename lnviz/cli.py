"""
cli.py
------
Command-line entry point (see bin/lnviz for the launcher).

Subcommands:
  analyze        one-off analysis of a single graph JSON -> console summary
                 + optional HTML report + optional raw JSON metrics dump.
  ingest         add a graph JSON to the local history store (computes and
                 archives its metrics; safe to re-run on the same file).
  ingest-dir     ingest every JSON file in a directory (batch backfill).
  list           list tracked snapshots with headline metrics.
  diff           node/channel/capacity churn between two tracked snapshots.
  history-report render an HTML report of metric trends across all tracked
                 snapshots, with the latest-vs-previous diff included.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

from . import metrics as metrics_mod
from . import resilience as resilience_mod
from . import report as report_mod
from .loader import load_snapshot
from .history import SnapshotStore


def _parse_captured_at(s):
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        pass
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _print_summary(full):
    b, d, c, s = full["basic"], full["degree"], full["capacity"], full["structure"]
    print(f"Nodes:              {b['num_nodes']:,} ({b['num_ghost_nodes']} ghost, {b['isolated_nodes']} isolated)")
    print(f"Channels:           {b['num_channels']:,} across {b['num_node_pairs_with_channel']:,} node pairs")
    print(f"Total capacity:     {b['total_capacity_btc']:.2f} BTC ({b['total_capacity_sat']:,} sat)")
    print(f"Giant component:    {s['largest_component_fraction']*100:.1f}% of nodes "
          f"({s['num_connected_components']} components total)")
    print(f"Degree Gini/HHI:    {d['gini']:.3f} / {d['hhi']:.0f}   (top 1% of nodes hold "
          f"{d['top_1pct_share']*100:.1f}% of channels)")
    print(f"Capacity Gini/HHI:  {c['gini']:.3f} / {c['hhi']:.0f}   (top 1% of nodes hold "
          f"{c['top_1pct_share']*100:.1f}% of capacity)")
    print(f"Clustering/transitivity: {s['average_clustering']:.3f} / {s['transitivity']:.3f}   "
          f"assortativity: {s['degree_assortativity']:.3f}")
    print(f"Main k-core:        k={s['main_core_number']} ({s['main_core_size']} nodes)")
    print(f"Approx diameter/avg path length (giant component): {s['approx_diameter_lcc']} / "
          f"{s['approx_avg_shortest_path_length_lcc']:.2f} hops")
    if "resilience" in full:
        res = full["resilience"]
        spof = res["single_points_of_failure"]
        att = res["attack_simulation"]["robustness_index"]
        print(f"Articulation points/bridges: {spof['articulation_point_count']:,} / {spof['bridge_count']:,}")
        print("Robustness index (0-0.5, higher=tougher): " +
              ", ".join(f"{k}={v:.3f}" for k, v in att.items()))
    print()
    print("Top nodes by composite dominance (degree+capacity+betweenness+eigenvector):")
    for row in full["dominance"]["top_nodes"][:10]:
        print(f"  {row['alias']:<32} {row['value']:.3f}")


def cmd_analyze(args):
    t0 = time.time()
    snap = load_snapshot(args.graph_json)
    full = metrics_mod.analyze_snapshot(
        snap, betweenness_k=args.betweenness_k, path_samples=args.path_samples,
        top_n=args.top_n,
    )
    if not args.skip_resilience:
        res = resilience_mod.resilience_summary(
            snap, betweenness_k=args.betweenness_k, random_trials=args.random_trials,
            top_n=args.top_n, compute_algebraic_connectivity=args.algebraic_connectivity,
        )
        full["resilience"] = res

    _print_summary(full)
    print(f"\n(analyzed in {time.time()-t0:.1f}s)")

    if args.out:
        report_mod.render_snapshot_report(full, args.out)
        print(f"HTML report written to {args.out}")
    if args.json:
        dump = dict(full)
        dump["degree"] = {k: v for k, v in dump["degree"].items() if k != "_values"}
        dump["capacity"] = {k: v for k, v in dump["capacity"].items() if k != "_values"}
        dump["centrality"] = {k: v for k, v in dump["centrality"].items() if k != "_raw"}
        with open(args.json, "w") as f:
            json.dump(dump, f, indent=2)
        print(f"Raw metrics JSON written to {args.json}")


def cmd_ingest(args):
    store = SnapshotStore(args.history_dir)
    snap_id = store.ingest(
        args.graph_json, label=args.label, captured_at=_parse_captured_at(args.captured_at),
        copy_raw=not args.no_copy, betweenness_k=args.betweenness_k,
        path_samples=args.path_samples, random_trials=args.random_trials,
        top_n=args.top_n, compute_algebraic_connectivity=args.algebraic_connectivity,
    )
    row = store.get(snap_id)
    print(f"Ingested snapshot #{snap_id} ({row['label']}, captured "
          f"{datetime.fromtimestamp(row['captured_at'], tz=timezone.utc).isoformat()}): "
          f"{row['num_nodes']:,} nodes, {row['num_channels']:,} channels.")
    store.close()


def cmd_ingest_dir(args):
    store = SnapshotStore(args.history_dir)
    paths = sorted(glob.glob(os.path.join(args.directory, args.pattern)))
    if not paths:
        print(f"No files matching {args.pattern} in {args.directory}")
        return
    for p in paths:
        t0 = time.time()
        snap_id = store.ingest(
            p, betweenness_k=args.betweenness_k, path_samples=args.path_samples,
            random_trials=args.random_trials, top_n=args.top_n,
            compute_algebraic_connectivity=args.algebraic_connectivity,
        )
        row = store.get(snap_id)
        print(f"[{time.time()-t0:5.1f}s] #{snap_id} {row['label']}: "
              f"{row['num_nodes']:,} nodes, {row['num_channels']:,} channels")
    store.close()


def cmd_list(args):
    store = SnapshotStore(args.history_dir)
    rows = store.list_snapshots()
    if not rows:
        print("No snapshots tracked yet. Use `lnviz ingest <graph.json>` to add one.")
        return
    print(f"{'id':>4} {'captured':<20} {'label':<24} {'nodes':>7} {'channels':>9} "
          f"{'cap.gini':>9} {'giant%':>8}")
    for r in rows:
        when = datetime.fromtimestamp(r["captured_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"{r['id']:>4} {when:<20} {r['label'][:24]:<24} {r['num_nodes']:>7,} "
              f"{r['num_channels']:>9,} {r['gini_capacity']:>9.3f} "
              f"{r['largest_component_fraction']*100:>7.1f}%")
    store.close()


def cmd_diff(args):
    store = SnapshotStore(args.history_dir)
    d = store.diff(args.snapshot_a, args.snapshot_b)
    print(json.dumps(d, indent=2))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(d, f, indent=2)
        print(f"\nWritten to {args.out}")
    store.close()


def cmd_history_report(args):
    store = SnapshotStore(args.history_dir)
    rows = store.list_snapshots()
    if not rows:
        print("No snapshots tracked yet. Use `lnviz ingest <graph.json>` to add one.")
        return
    diff = None
    if len(rows) >= 2:
        try:
            diff = store.diff(rows[-2]["id"], rows[-1]["id"])
        except FileNotFoundError as e:
            print(f"(skipping latest diff: {e})")

    # Backfill a standalone HTML report for any snapshot ingested before this
    # feature existed (or whose report.html went missing), then link to all
    # of them from the trend report's snapshot table.
    out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
    report_links = {}
    for r in rows:
        html_path = store.ensure_report_html(r["id"])
        if html_path:
            report_links[r["id"]] = os.path.relpath(os.path.abspath(html_path), out_dir)

    # Overlay charts (Lorenz-curve-over-time, attack-curve-over-time) need
    # each snapshot's full cached metrics, not just the SQLite headline row.
    # Subsample evenly across time rather than silently plotting everything
    # once history gets long.
    overlay_rows = report_mod.subsample_evenly(rows, args.overlay_limit)
    overlay_reports = []
    for r in overlay_rows:
        full = store.load_full_report(r["id"])
        if full is not None:
            # Include time-of-day, not just date, so same-day snapshots
            # (e.g. testing, or sub-daily polling) still get distinct legend
            # labels in the overlay charts.
            when = datetime.fromtimestamp(r["captured_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            label = f"{when} ({r['label']})" if r["label"] and r["label"] != when else when
            overlay_reports.append((label, full))
    overlay_dropped = len(rows) - len(overlay_rows)

    report_mod.render_history_report(
        rows, args.out, diff=diff, report_links=report_links,
        overlay_reports=overlay_reports, overlay_dropped=overlay_dropped,
        overlay_strategy=args.overlay_strategy,
    )
    print(f"History report written to {args.out} ({len(rows)} snapshots)")
    store.close()


def build_parser():
    p = argparse.ArgumentParser(prog="lnviz", description="Lightning Network graph analytics")
    sub = p.add_subparsers(dest="command", required=True)

    common_perf = argparse.ArgumentParser(add_help=False)
    common_perf.add_argument("--betweenness-k", type=int, default=400,
                              help="approximate betweenness centrality with this many sampled sources "
                                   "(0/None = exact, slow on large graphs). Default 400.")
    common_perf.add_argument("--path-samples", type=int, default=200,
                              help="sample size for approximate avg-shortest-path-length. Default 200.")
    common_perf.add_argument("--random-trials", type=int, default=3,
                              help="number of random-removal trials to average for the attack simulation. Default 3.")
    common_perf.add_argument("--top-n", type=int, default=20, help="rows kept in each top-N table. Default 20.")
    common_perf.add_argument("--algebraic-connectivity", action="store_true",
                              help="also compute the Fiedler value (spectral connectivity) -- extra eigen-solve cost.")

    pa = sub.add_parser("analyze", parents=[common_perf], help="analyze a single graph JSON file")
    pa.add_argument("graph_json")
    pa.add_argument("--out", help="write an HTML report to this path")
    pa.add_argument("--json", help="write the full raw metrics as JSON to this path")
    pa.add_argument("--skip-resilience", action="store_true", help="skip attack-simulation/SPOF (faster)")
    pa.set_defaults(func=cmd_analyze)

    pi = sub.add_parser("ingest", parents=[common_perf], help="add a graph JSON snapshot to the history store")
    pi.add_argument("graph_json")
    pi.add_argument("--history-dir", default="snapshots")
    pi.add_argument("--label", help="human label for this snapshot (default: filename)")
    pi.add_argument("--captured-at", help="ISO8601 timestamp or unix epoch (default: file mtime)")
    pi.add_argument("--no-copy", action="store_true", help="don't archive a copy of the raw JSON (diff/replay won't work later)")
    pi.set_defaults(func=cmd_ingest)

    pid = sub.add_parser("ingest-dir", parents=[common_perf], help="batch-ingest every graph JSON in a directory")
    pid.add_argument("directory")
    pid.add_argument("--pattern", default="*.json")
    pid.add_argument("--history-dir", default="snapshots")
    pid.set_defaults(func=cmd_ingest_dir)

    pl = sub.add_parser("list", help="list tracked snapshots")
    pl.add_argument("--history-dir", default="snapshots")
    pl.set_defaults(func=cmd_list)

    pd = sub.add_parser("diff", help="node/channel/capacity churn between two tracked snapshots")
    pd.add_argument("snapshot_a", type=int)
    pd.add_argument("snapshot_b", type=int)
    pd.add_argument("--history-dir", default="snapshots")
    pd.add_argument("--out", help="also write the diff JSON to this path")
    pd.set_defaults(func=cmd_diff)

    ph = sub.add_parser("history-report", help="render an HTML trend report across all tracked snapshots")
    ph.add_argument("--history-dir", default="snapshots")
    ph.add_argument("--out", default="history_report.html")
    ph.add_argument("--overlay-limit", type=int, default=8,
                     help="max snapshots to overlay in the Lorenz-curve/attack-curve evolution charts "
                          "(evenly sampled across time if history has more than this). Default 8.")
    ph.add_argument("--overlay-strategy", default="degree", choices=["random", "degree", "capacity", "betweenness"],
                     help="which attack strategy's fragmentation curve to trace over time. Default degree.")
    ph.set_defaults(func=cmd_history_report)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
