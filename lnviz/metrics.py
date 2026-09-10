"""
metrics.py
----------
Single-snapshot connectivity & dominance metrics computed on a GraphSnapshot
(see loader.py). Everything here is a pure function of one graph -- no
history/time-series concerns live in this module (see history.py).
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

import networkx as nx

from .loader import GraphSnapshot, alias_or_short


# ---------------------------------------------------------------------------
# Inequality / dominance helpers
# ---------------------------------------------------------------------------

def gini(values: Sequence[float]) -> float:
    """Gini coefficient of a non-negative distribution. 0 = perfectly equal,
    ~1 = maximally concentrated in one entity."""
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return 0.0
    total = sum(vals)
    if total == 0:
        return 0.0
    cum = 0.0
    for i, v in enumerate(vals, start=1):
        cum += i * v
    return (2.0 * cum) / (n * total) - (n + 1.0) / n


def hhi(values: Sequence[float]) -> float:
    """Herfindahl-Hirschman Index on shares of `values`. Returned on the
    conventional 0-10000 scale (10000 = single entity holds everything)."""
    total = sum(values)
    if total <= 0:
        return 0.0
    return 10000.0 * sum((v / total) ** 2 for v in values)


def top_k_share(values: Sequence[float], fractions: Sequence[float] = (0.01, 0.05, 0.10)) -> Dict[str, float]:
    """Share of total held by the top `fraction` of entities, for each
    fraction in `fractions` (e.g. top 1%, top 5%, top 10%)."""
    vals = sorted(values, reverse=True)
    n = len(vals)
    total = sum(vals)
    out = {}
    for frac in fractions:
        k = max(1, math.ceil(n * frac))
        share = sum(vals[:k]) / total if total > 0 else 0.0
        out[f"top_{int(frac * 100)}pct_share"] = share
    return out


def _ranked_table(scores: Dict[str, float], node_meta: Dict[str, dict], n: int = 20) -> List[Dict[str, Any]]:
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:n]
    out = []
    for pk, score in ranked:
        alias = node_meta.get(pk, {}).get("alias", "")
        out.append({"pub_key": pk, "alias": alias_or_short(pk, alias), "value": score})
    return out


# ---------------------------------------------------------------------------
# Basic / structural stats
# ---------------------------------------------------------------------------

def basic_stats(snap: GraphSnapshot) -> Dict[str, Any]:
    G = snap.G
    n_nodes = G.number_of_nodes()
    n_pairs = G.number_of_edges()
    n_channels = len(snap.raw_edges)
    caps = [e["capacity"] for e in snap.raw_edges]
    total_cap = sum(caps)
    isolated = sum(1 for _, d in G.degree() if d == 0)
    no_addr = sum(1 for _, meta in G.nodes(data=True) if meta.get("num_addresses", 0) == 0)

    return {
        "num_nodes": n_nodes,
        "num_ghost_nodes": len(snap.ghost_nodes),
        "num_node_pairs_with_channel": n_pairs,
        "num_channels": n_channels,
        "num_multi_channel_pairs": snap.duplicate_channel_pairs,
        "total_capacity_sat": total_cap,
        "total_capacity_btc": total_cap / 1e8,
        "mean_channel_capacity_sat": statistics.mean(caps) if caps else 0,
        "median_channel_capacity_sat": statistics.median(caps) if caps else 0,
        "max_channel_capacity_sat": max(caps) if caps else 0,
        "isolated_nodes": isolated,
        "nodes_without_public_address": no_addr,
        "density": nx.density(G) if n_nodes > 1 else 0.0,
        "average_degree": (2.0 * n_pairs / n_nodes) if n_nodes else 0.0,
    }


def degree_distribution(snap: GraphSnapshot, top_n: int = 20) -> Dict[str, Any]:
    G = snap.G
    degrees = dict(G.degree())
    vals = list(degrees.values())
    out = {
        "gini": gini(vals),
        "hhi": hhi(vals),
        "mean": statistics.mean(vals) if vals else 0,
        "median": statistics.median(vals) if vals else 0,
        "max": max(vals) if vals else 0,
        "top_nodes": _ranked_table(degrees, snap.node_meta, top_n),
        "_values": vals,
    }
    out.update(top_k_share(vals))
    return out


def capacity_distribution(snap: GraphSnapshot, top_n: int = 20) -> Dict[str, Any]:
    G = snap.G
    node_cap = {pk: sum(d["capacity"] for _, _, d in G.edges(pk, data=True)) for pk in G.nodes()}
    vals = list(node_cap.values())
    out = {
        "gini": gini(vals),
        "hhi": hhi(vals),
        "mean_sat": statistics.mean(vals) if vals else 0,
        "median_sat": statistics.median(vals) if vals else 0,
        "max_sat": max(vals) if vals else 0,
        "top_nodes": _ranked_table(node_cap, snap.node_meta, top_n),
        "_values": vals,
    }
    out.update(top_k_share(vals))
    return out


def _largest_cc(G: nx.Graph) -> nx.Graph:
    if G.number_of_nodes() == 0:
        return G
    largest = max(nx.connected_components(G), key=len)
    return G.subgraph(largest).copy()


def _approx_avg_shortest_path_length(G: nx.Graph, samples: int = 200, seed: int = 42) -> Optional[float]:
    """Estimate average shortest path length on a (connected) graph by BFS
    from a random sample of source nodes, rather than all-pairs Dijkstra."""
    import random
    n = G.number_of_nodes()
    if n < 2:
        return None
    rng = random.Random(seed)
    nodes = list(G.nodes())
    k = min(samples, n)
    sources = rng.sample(nodes, k)
    total = 0
    count = 0
    for s in sources:
        lengths = nx.single_source_shortest_path_length(G, s)
        for t, d in lengths.items():
            if t != s:
                total += d
                count += 1
    return (total / count) if count else None


def structure_stats(snap: GraphSnapshot, path_samples: int = 200, seed: int = 42) -> Dict[str, Any]:
    G = snap.G
    n_nodes = G.number_of_nodes()
    components = sorted((len(c) for c in nx.connected_components(G)), reverse=True)
    lcc = _largest_cc(G)
    lcc_frac = (len(lcc) / n_nodes) if n_nodes else 0.0

    core_numbers = nx.core_number(lcc) if lcc.number_of_nodes() else {}
    main_core = max(core_numbers.values()) if core_numbers else 0
    main_core_size = sum(1 for v in core_numbers.values() if v == main_core)

    try:
        approx_diam = nx.approximation.diameter(lcc) if lcc.number_of_nodes() > 1 else 0
    except Exception:
        approx_diam = None

    avg_path_len = _approx_avg_shortest_path_length(lcc, samples=path_samples, seed=seed)

    try:
        assortativity = nx.degree_assortativity_coefficient(G)
    except Exception:
        assortativity = None

    return {
        "num_connected_components": len(components),
        "component_size_histogram_top10": components[:10],
        "largest_component_size": components[0] if components else 0,
        "largest_component_fraction": lcc_frac,
        "average_clustering": nx.average_clustering(G) if n_nodes else 0.0,
        "transitivity": nx.transitivity(G) if n_nodes else 0.0,
        "degree_assortativity": assortativity,
        "main_core_number": main_core,
        "main_core_size": main_core_size,
        "approx_diameter_lcc": approx_diam,
        "approx_avg_shortest_path_length_lcc": avg_path_len,
        "path_length_samples": min(path_samples, lcc.number_of_nodes()) if lcc.number_of_nodes() else 0,
    }


# ---------------------------------------------------------------------------
# Centrality / hub-dominance
# ---------------------------------------------------------------------------

def centrality_stats(snap: GraphSnapshot, betweenness_k: Optional[int] = 400,
                      seed: int = 42, top_n: int = 20) -> Dict[str, Any]:
    G = snap.G
    n = G.number_of_nodes()
    k = min(betweenness_k, n) if betweenness_k else None

    betweenness = nx.betweenness_centrality(G, k=k, seed=seed, normalized=True) if n > 2 else {pk: 0.0 for pk in G.nodes()}

    try:
        eigen = nx.eigenvector_centrality(G, max_iter=1000, tol=1e-08)
    except nx.PowerIterationFailedConvergence:
        eigen = {pk: 0.0 for pk in G.nodes()}

    cap_weighted_eigen = None
    try:
        cap_weighted_eigen = nx.eigenvector_centrality(G, max_iter=1000, tol=1e-08, weight="capacity")
    except Exception:
        cap_weighted_eigen = None

    return {
        "betweenness_sampled_k": k,
        "betweenness_gini": gini(list(betweenness.values())),
        "betweenness_top_nodes": _ranked_table(betweenness, snap.node_meta, top_n),
        "eigenvector_top_nodes": _ranked_table(eigen, snap.node_meta, top_n),
        "capacity_weighted_eigenvector_top_nodes": (
            _ranked_table(cap_weighted_eigen, snap.node_meta, top_n) if cap_weighted_eigen else None
        ),
        "_raw": {"betweenness": betweenness, "eigenvector": eigen},
    }


def dominance_summary(snap: GraphSnapshot, degree_d: Dict[str, Any], capacity_d: Dict[str, Any],
                       centrality_d: Dict[str, Any], top_n: int = 20) -> Dict[str, Any]:
    """Composite hub ranking: average percentile rank across degree, capacity,
    betweenness and eigenvector centrality. Surfaces the nodes that dominate
    the network from more than one angle at once."""
    G = snap.G
    nodes = list(G.nodes())
    n = len(nodes)
    if n == 0:
        return {"top_nodes": [], "note": "empty graph"}

    def pctrank(d: Dict[str, float]) -> Dict[str, float]:
        order = sorted(nodes, key=lambda pk: d.get(pk, 0.0))
        rank = {pk: i / (n - 1) if n > 1 else 1.0 for i, pk in enumerate(order)}
        return rank

    degree_scores = dict(G.degree())
    cap_scores = {pk: sum(dd["capacity"] for _, _, dd in G.edges(pk, data=True)) for pk in nodes}
    bet_scores = centrality_d["_raw"]["betweenness"]
    eig_scores = centrality_d["_raw"]["eigenvector"]

    pr_degree = pctrank(degree_scores)
    pr_cap = pctrank(cap_scores)
    pr_bet = pctrank(bet_scores)
    pr_eig = pctrank(eig_scores)

    composite = {
        pk: (pr_degree[pk] + pr_cap[pk] + pr_bet[pk] + pr_eig[pk]) / 4.0
        for pk in nodes
    }
    return {
        "top_nodes": _ranked_table(composite, snap.node_meta, top_n),
        "method": "mean percentile rank of degree, capacity, betweenness, eigenvector centrality",
    }


def analyze_snapshot(snap: GraphSnapshot, betweenness_k: Optional[int] = 400,
                      path_samples: int = 200, top_n: int = 20, seed: int = 42) -> Dict[str, Any]:
    """Run the full single-snapshot metric suite and return a plain-dict
    report (JSON-serializable except for the `_raw` centrality arrays, which
    callers should pop before serializing if not needed)."""
    basic = basic_stats(snap)
    degree_d = degree_distribution(snap, top_n=top_n)
    capacity_d = capacity_distribution(snap, top_n=top_n)
    structure_d = structure_stats(snap, path_samples=path_samples, seed=seed)
    centrality_d = centrality_stats(snap, betweenness_k=betweenness_k, seed=seed, top_n=top_n)
    dominance_d = dominance_summary(snap, degree_d, capacity_d, centrality_d, top_n=top_n)

    return {
        "source_path": snap.source_path,
        "captured_at": snap.captured_at,
        "basic": basic,
        "degree": degree_d,
        "capacity": capacity_d,
        "structure": structure_d,
        "centrality": centrality_d,
        "dominance": dominance_d,
    }
