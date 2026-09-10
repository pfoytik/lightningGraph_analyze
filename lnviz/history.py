"""
history.py
----------
A local time-series store for repeated lightningGraph.json snapshots
("this node's view of the network, taken at time T"). Each ingested
snapshot's raw JSON is archived, its full metric report is computed once
and cached as JSON, and a flat row of headline numbers is written to a
SQLite table so trends can be queried cheaply without re-parsing every
archived graph.

Layout under `history_dir` (default: ./snapshots):
    raw/<snapshot_id>__<label>.json      -- archived copy of the input file
    reports/<snapshot_id>.json           -- full analyze_snapshot()+resilience_summary() output
    history.db                           -- SQLite: one row per snapshot with headline metrics
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from .loader import load_snapshot
from . import metrics as metrics_mod
from . import resilience as resilience_mod
from . import report as report_mod

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT,
    captured_at INTEGER NOT NULL,
    ingested_at INTEGER NOT NULL,
    source_path TEXT,
    raw_path TEXT,
    report_path TEXT,
    report_html_path TEXT,
    num_nodes INTEGER,
    num_node_pairs INTEGER,
    num_channels INTEGER,
    total_capacity_sat INTEGER,
    isolated_nodes INTEGER,
    density REAL,
    average_degree REAL,
    gini_degree REAL,
    hhi_degree REAL,
    gini_capacity REAL,
    hhi_capacity REAL,
    betweenness_gini REAL,
    num_connected_components INTEGER,
    largest_component_fraction REAL,
    average_clustering REAL,
    transitivity REAL,
    degree_assortativity REAL,
    main_core_number INTEGER,
    approx_diameter_lcc INTEGER,
    approx_avg_shortest_path_length_lcc REAL,
    articulation_point_count INTEGER,
    bridge_count INTEGER,
    robustness_random REAL,
    robustness_degree REAL,
    robustness_capacity REAL,
    robustness_betweenness REAL,
    algebraic_connectivity REAL,
    UNIQUE(captured_at, source_path)
);
"""


class SnapshotStore:
    def __init__(self, history_dir: str = "snapshots"):
        self.history_dir = history_dir
        self.raw_dir = os.path.join(history_dir, "raw")
        self.report_dir = os.path.join(history_dir, "reports")
        self.db_path = os.path.join(history_dir, "history.db")
        os.makedirs(self.raw_dir, exist_ok=True)
        os.makedirs(self.report_dir, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Add columns introduced after a store may already have been created
        on disk (SQLite has no ADD COLUMN IF NOT EXISTS)."""
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(snapshots)")}
        if "report_html_path" not in cols:
            self.conn.execute("ALTER TABLE snapshots ADD COLUMN report_html_path TEXT")

    def close(self):
        self.conn.close()

    # -- ingest ------------------------------------------------------------

    def ingest(self, json_path: str, label: Optional[str] = None,
               captured_at: Optional[int] = None, copy_raw: bool = True,
               betweenness_k: Optional[int] = 400, path_samples: int = 200,
               random_trials: int = 3, top_n: int = 20, seed: int = 42,
               compute_algebraic_connectivity: bool = False,
               skip_if_duplicate: bool = True) -> int:
        """Parse+analyze a graph JSON file and record it as a new snapshot.
        Returns the new snapshot's row id."""
        if captured_at is None:
            captured_at = int(os.path.getmtime(json_path))
        ingested_at = int(time.time())
        label = label or os.path.splitext(os.path.basename(json_path))[0]

        if skip_if_duplicate:
            cur = self.conn.execute(
                "SELECT id FROM snapshots WHERE captured_at=? AND source_path=?",
                (captured_at, os.path.abspath(json_path)),
            )
            existing = cur.fetchone()
            if existing:
                return existing[0]

        snap = load_snapshot(json_path, captured_at=captured_at)
        full = metrics_mod.analyze_snapshot(
            snap, betweenness_k=betweenness_k, path_samples=path_samples,
            top_n=top_n, seed=seed,
        )
        res = resilience_mod.resilience_summary(
            snap, betweenness_k=betweenness_k, random_trials=random_trials,
            seed=seed, top_n=top_n,
            compute_algebraic_connectivity=compute_algebraic_connectivity,
        )

        # centrality._raw carries non-JSON-friendly full-size dicts; drop
        # before persisting (top-N tables already carry what reports need).
        full["centrality"].pop("_raw", None)
        full["resilience"] = res

        raw_dest = None
        if copy_raw:
            fname = f"{captured_at}__{label}.json"
            raw_dest = os.path.join(self.raw_dir, fname)
            if os.path.abspath(raw_dest) != os.path.abspath(json_path):
                shutil.copy2(json_path, raw_dest)

        report_dest = os.path.join(self.report_dir, f"{captured_at}__{label}.json")
        with open(report_dest, "w") as f:
            json.dump(full, f)

        # Persist a standalone visual report for this snapshot too, so every
        # ingested point in history has its own report.html to look back at,
        # not just the headline numbers in the trend charts.
        html_dest = os.path.join(self.report_dir, f"{captured_at}__{label}.html")
        report_mod.render_snapshot_report(full, html_dest, title=f"Lightning Graph Snapshot -- {label}")

        b = full["basic"]
        deg = full["degree"]
        cap = full["capacity"]
        struct = full["structure"]
        cen = full["centrality"]
        spof = res["single_points_of_failure"]
        att = res["attack_simulation"]["robustness_index"]

        row = (
            label, captured_at, ingested_at, os.path.abspath(json_path), raw_dest, report_dest, html_dest,
            b["num_nodes"], b["num_node_pairs_with_channel"], b["num_channels"], b["total_capacity_sat"],
            b["isolated_nodes"], b["density"], b["average_degree"],
            deg["gini"], deg["hhi"], cap["gini"], cap["hhi"], cen["betweenness_gini"],
            struct["num_connected_components"], struct["largest_component_fraction"],
            struct["average_clustering"], struct["transitivity"], struct["degree_assortativity"],
            struct["main_core_number"], struct["approx_diameter_lcc"], struct["approx_avg_shortest_path_length_lcc"],
            spof["articulation_point_count"], spof["bridge_count"],
            att.get("random"), att.get("degree"), att.get("capacity"), att.get("betweenness"),
            res.get("algebraic_connectivity_lcc"),
        )
        cur = self.conn.execute(
            """INSERT OR REPLACE INTO snapshots (
                label, captured_at, ingested_at, source_path, raw_path, report_path, report_html_path,
                num_nodes, num_node_pairs, num_channels, total_capacity_sat,
                isolated_nodes, density, average_degree,
                gini_degree, hhi_degree, gini_capacity, hhi_capacity, betweenness_gini,
                num_connected_components, largest_component_fraction,
                average_clustering, transitivity, degree_assortativity,
                main_core_number, approx_diameter_lcc, approx_avg_shortest_path_length_lcc,
                articulation_point_count, bridge_count,
                robustness_random, robustness_degree, robustness_capacity, robustness_betweenness,
                algebraic_connectivity
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            row,
        )
        self.conn.commit()
        return cur.lastrowid

    # -- query ---------------------------------------------------------------

    def list_snapshots(self) -> List[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM snapshots ORDER BY captured_at ASC")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get(self, snapshot_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, row))

    def latest(self) -> Optional[Dict[str, Any]]:
        rows = self.list_snapshots()
        return rows[-1] if rows else None

    def load_full_report(self, snapshot_id: int) -> Optional[Dict[str, Any]]:
        row = self.get(snapshot_id)
        if not row or not row.get("report_path") or not os.path.exists(row["report_path"]):
            return None
        with open(row["report_path"]) as f:
            return json.load(f)

    def ensure_report_html(self, snapshot_id: int) -> Optional[str]:
        """Return the path to this snapshot's standalone HTML report,
        rendering it now (from the already-cached metrics JSON, no
        recomputation needed) if it predates this feature or went missing."""
        row = self.get(snapshot_id)
        if not row:
            return None
        existing = row.get("report_html_path")
        if existing and os.path.exists(existing):
            return existing
        full = self.load_full_report(snapshot_id)
        if full is None:
            return None
        html_dest = os.path.join(self.report_dir, f"{row['captured_at']}__{row['label']}.html")
        report_mod.render_snapshot_report(full, html_dest, title=f"Lightning Graph Snapshot -- {row['label']}")
        self.conn.execute("UPDATE snapshots SET report_html_path=? WHERE id=?", (html_dest, snapshot_id))
        self.conn.commit()
        return html_dest

    # -- diff -----------------------------------------------------------------

    def diff(self, snapshot_id_a: int, snapshot_id_b: int) -> Dict[str, Any]:
        """Node/channel churn between two ingested snapshots. Requires both
        snapshots' raw JSON to still be present (archived under raw/ unless
        copy_raw=False was used at ingest time)."""
        a = self.get(snapshot_id_a)
        b = self.get(snapshot_id_b)
        if not a or not b:
            raise ValueError("snapshot id not found")
        for row in (a, b):
            path = row.get("raw_path") or row.get("source_path")
            if not path or not os.path.exists(path):
                raise FileNotFoundError(
                    f"raw graph JSON for snapshot {row['id']} ({row['label']}) not found on disk; "
                    "cannot compute a structural diff without it."
                )

        snap_a = load_snapshot(a.get("raw_path") or a["source_path"])
        snap_b = load_snapshot(b.get("raw_path") or b["source_path"])

        nodes_a, nodes_b = set(snap_a.G.nodes()), set(snap_b.G.nodes())
        added_nodes = nodes_b - nodes_a
        removed_nodes = nodes_a - nodes_b

        edges_a = {frozenset(e) for e in snap_a.G.edges()}
        edges_b = {frozenset(e) for e in snap_b.G.edges()}
        added_edges = edges_b - edges_a
        removed_edges = edges_a - edges_b

        cap_a = sum(d["capacity"] for _, _, d in snap_a.G.edges(data=True))
        cap_b = sum(d["capacity"] for _, _, d in snap_b.G.edges(data=True))

        def alias(snap, pk):
            return snap.node_meta.get(pk, {}).get("alias") or pk[:10] + "…"

        return {
            "from": {"id": a["id"], "label": a["label"], "captured_at": a["captured_at"]},
            "to": {"id": b["id"], "label": b["label"], "captured_at": b["captured_at"]},
            "nodes": {
                "before": len(nodes_a), "after": len(nodes_b),
                "added": len(added_nodes), "removed": len(removed_nodes),
                "added_sample": [{"pub_key": pk, "alias": alias(snap_b, pk)} for pk in list(added_nodes)[:25]],
                "removed_sample": [{"pub_key": pk, "alias": alias(snap_a, pk)} for pk in list(removed_nodes)[:25]],
            },
            "channels": {
                "before": len(edges_a), "after": len(edges_b),
                "added": len(added_edges), "removed": len(removed_edges),
            },
            "capacity_sat": {
                "before": cap_a, "after": cap_b, "delta": cap_b - cap_a,
                "delta_pct": ((cap_b - cap_a) / cap_a * 100.0) if cap_a else None,
            },
        }
