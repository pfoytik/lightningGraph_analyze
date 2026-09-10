"""
loader.py
---------
Parse an LND `describegraph` JSON dump (the format produced by
`lncli describegraph` / the LND REST `/v1/graph` endpoint) into:

  * a networkx.Graph  -- one node per pubkey, one (aggregated) edge per
    node-pair, suitable for connectivity / resilience / centrality analysis.
  * a list of "raw" per-channel records -- since two nodes can have more than
    one channel between them, per-channel capacity statistics need the
    un-aggregated view.

This module intentionally knows nothing about history/snapshots or plotting;
it just turns JSON -> in-memory graph.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx


def _to_int(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class GraphSnapshot:
    """Container for a parsed graph plus useful side-metadata."""

    G: "nx.Graph"
    raw_edges: List[Dict[str, Any]]
    node_meta: Dict[str, Dict[str, Any]]
    source_path: Optional[str] = None
    captured_at: Optional[int] = None  # unix epoch seconds, if known
    ghost_nodes: set = field(default_factory=set)  # pubkeys seen only in edges
    duplicate_channel_pairs: int = 0  # node-pairs with >1 channel between them


def load_graph_json(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)


def alias_or_short(pubkey: str, alias: Optional[str]) -> str:
    if alias:
        return alias
    return pubkey[:10] + "…" if pubkey else "unknown"


def build_graph(data: Dict[str, Any], source_path: Optional[str] = None,
                captured_at: Optional[int] = None) -> GraphSnapshot:
    """Build a networkx.Graph (aggregated, undirected, simple) from a
    describegraph-shaped dict with 'nodes' and 'edges' lists."""

    nodes = data.get("nodes", []) or []
    edges = data.get("edges", []) or []

    node_meta: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        pk = n.get("pub_key")
        if not pk:
            continue
        node_meta[pk] = {
            "alias": n.get("alias") or "",
            "color": n.get("color") or "",
            "last_update": _to_int(n.get("last_update")),
            "num_addresses": len(n.get("addresses") or []),
            "features": list((n.get("features") or {}).values()),
        }

    G = nx.Graph()
    for pk, meta in node_meta.items():
        G.add_node(pk, **meta)

    ghost_nodes = set()
    raw_edges: List[Dict[str, Any]] = []
    duplicate_channel_pairs = 0

    for e in edges:
        n1 = e.get("node1_pub")
        n2 = e.get("node2_pub")
        if not n1 or not n2:
            continue
        cap = _to_int(e.get("capacity"))
        rec = {
            "channel_id": e.get("channel_id"),
            "chan_point": e.get("chan_point"),
            "node1_pub": n1,
            "node2_pub": n2,
            "capacity": cap,
            "last_update": _to_int(e.get("last_update")),
            "node1_policy": e.get("node1_policy"),
            "node2_policy": e.get("node2_policy"),
        }
        raw_edges.append(rec)

        for pk in (n1, n2):
            if pk not in node_meta:
                ghost_nodes.add(pk)
                if pk not in G:
                    G.add_node(pk, alias="", color="", last_update=0,
                               num_addresses=0, features=[], ghost=True)

        if G.has_edge(n1, n2):
            duplicate_channel_pairs += 1 if G[n1][n2].get("channel_count", 1) == 1 else 0
            d = G[n1][n2]
            d["capacity"] += cap
            d["channel_count"] += 1
            d["channel_ids"].append(rec["channel_id"])
        else:
            G.add_edge(n1, n2, capacity=cap, channel_count=1,
                       channel_ids=[rec["channel_id"]])

    return GraphSnapshot(
        G=G,
        raw_edges=raw_edges,
        node_meta=node_meta,
        source_path=source_path,
        captured_at=captured_at,
        ghost_nodes=ghost_nodes,
        duplicate_channel_pairs=duplicate_channel_pairs,
    )


def load_snapshot(path: str, captured_at: Optional[int] = None) -> GraphSnapshot:
    data = load_graph_json(path)
    return build_graph(data, source_path=path, captured_at=captured_at)
