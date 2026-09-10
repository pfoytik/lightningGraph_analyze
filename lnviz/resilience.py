"""
resilience.py
-------------
Robustness / attack-resilience metrics: single points of failure
(articulation points & bridges), and simulated node-removal attacks
(random vs. targeted-by-degree/capacity/betweenness) that trace out how
fast the network fragments.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

import networkx as nx

from .loader import GraphSnapshot, alias_or_short


def _largest_cc_subgraph(G: nx.Graph) -> nx.Graph:
    if G.number_of_nodes() == 0:
        return G
    largest = max(nx.connected_components(G), key=len)
    return G.subgraph(largest).copy()


def single_points_of_failure(snap: GraphSnapshot, top_n: int = 20) -> Dict[str, Any]:
    """Articulation points (cut vertices) and bridges (cut edges) computed on
    the largest connected component -- these are nodes/channels whose loss
    would split the network into disconnected pieces."""
    G = snap.G
    lcc = _largest_cc_subgraph(G)
    if lcc.number_of_nodes() < 3:
        return {
            "articulation_point_count": 0,
            "articulation_points": [],
            "bridge_count": 0,
            "bridge_fraction_of_channels": 0.0,
        }

    aps = list(nx.articulation_points(lcc))
    bridges = list(nx.bridges(lcc))

    # Rank articulation points by how large a component they would strand,
    # i.e. remove each and look at the size of the *second*-largest fragment.
    # Mutate `lcc` in place and restore afterwards rather than deep-copying
    # the whole (multi-thousand node) graph on every iteration -- deep copy
    # dominates cost far more than the connectivity check itself does.
    ranked = []
    for pk in aps:
        nbrs = list(lcc.adj[pk])
        edge_data = {nb: dict(lcc[pk][nb]) for nb in nbrs}
        node_data = dict(lcc.nodes[pk])
        lcc.remove_node(pk)
        comps = sorted((len(c) for c in nx.connected_components(lcc)), reverse=True)
        stranded = sum(comps[1:]) if len(comps) > 1 else 0
        lcc.add_node(pk, **node_data)
        for nb, data in edge_data.items():
            lcc.add_edge(pk, nb, **data)
        ranked.append((pk, stranded))
    ranked.sort(key=lambda kv: kv[1], reverse=True)
    top = [
        {"pub_key": pk, "alias": alias_or_short(pk, snap.node_meta.get(pk, {}).get("alias")),
         "nodes_stranded_if_removed": stranded}
        for pk, stranded in ranked[:top_n]
    ]

    return {
        "articulation_point_count": len(aps),
        "articulation_points": top,
        "bridge_count": len(bridges),
        "bridge_fraction_of_channels": (len(bridges) / lcc.number_of_edges()) if lcc.number_of_edges() else 0.0,
    }


def _removal_order(G: nx.Graph, strategy: str, betweenness_k: Optional[int], seed: int) -> List[str]:
    nodes = list(G.nodes())
    if strategy == "random":
        rng = random.Random(seed)
        order = nodes[:]
        rng.shuffle(order)
        return order
    if strategy == "degree":
        scores = dict(G.degree())
    elif strategy == "capacity":
        scores = {pk: sum(d["capacity"] for _, _, d in G.edges(pk, data=True)) for pk in nodes}
    elif strategy == "betweenness":
        k = min(betweenness_k, len(nodes)) if betweenness_k else None
        scores = nx.betweenness_centrality(G, k=k, seed=seed, normalized=True)
    else:
        raise ValueError(f"unknown strategy: {strategy}")
    return sorted(nodes, key=lambda pk: scores.get(pk, 0), reverse=True)


def attack_curve(G: nx.Graph, order: List[str], fractions: Sequence[float]) -> List[Tuple[float, float]]:
    """Remove nodes in `order` up to each fraction checkpoint and record the
    largest-connected-component fraction remaining. Static ranking (computed
    once up front), not recomputed after each removal -- an approximation
    that is far cheaper than adaptive re-ranking and standard practice for
    this kind of robustness curve."""
    n = G.number_of_nodes()
    H = G.copy()
    curve = []
    removed_so_far = 0
    for frac in fractions:
        target_removed = int(round(frac * n))
        to_remove = order[removed_so_far:target_removed]
        H.remove_nodes_from(to_remove)
        removed_so_far = target_removed
        remaining = H.number_of_nodes()
        if remaining == 0:
            curve.append((frac, 0.0))
            continue
        largest = max((len(c) for c in nx.connected_components(H)), default=0)
        curve.append((frac, largest / n))
    return curve


def robustness_index(curve: List[Tuple[float, float]]) -> float:
    """Area under the LCC-fraction-vs-fraction-removed curve (trapezoidal),
    i.e. the Schneider et al. R-index. Ranges ~0 (collapses immediately) to
    0.5 (stays fully connected until almost everyone is removed)."""
    if len(curve) < 2:
        return 0.0
    area = 0.0
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        area += (x1 - x0) * (y0 + y1) / 2.0
    return area


DEFAULT_FRACTIONS = [round(x * 0.02, 2) for x in range(0, 26)]  # 0%..50% in 2% steps


def attack_simulation(snap: GraphSnapshot, strategies: Sequence[str] = ("random", "degree", "capacity", "betweenness"),
                       fractions: Sequence[float] = DEFAULT_FRACTIONS, betweenness_k: Optional[int] = 400,
                       random_trials: int = 3, seed: int = 42) -> Dict[str, Any]:
    G = snap.G
    lcc = _largest_cc_subgraph(G)  # attacks are most meaningful measured against the routable core
    out: Dict[str, Any] = {"base_population": lcc.number_of_nodes(), "curves": {}, "robustness_index": {}}

    for strat in strategies:
        if strat == "random":
            curves = []
            for t in range(random_trials):
                order = _removal_order(lcc, "random", betweenness_k, seed + t)
                curves.append(attack_curve(lcc, order, fractions))
            avg_curve = [
                (fractions[i], sum(c[i][1] for c in curves) / len(curves))
                for i in range(len(fractions))
            ]
            out["curves"][strat] = avg_curve
            out["robustness_index"][strat] = robustness_index(avg_curve)
        else:
            order = _removal_order(lcc, strat, betweenness_k, seed)
            curve = attack_curve(lcc, order, fractions)
            out["curves"][strat] = curve
            out["robustness_index"][strat] = robustness_index(curve)

    return out


def algebraic_connectivity(snap: GraphSnapshot) -> Optional[float]:
    """Fiedler value of the largest connected component -- a spectral
    measure of overall connectivity/robustness (higher = harder to cut into
    two large pieces). Optional: eigen-solve cost grows with graph size."""
    lcc = _largest_cc_subgraph(snap.G)
    if lcc.number_of_nodes() < 3:
        return None
    try:
        return nx.algebraic_connectivity(lcc, method="lanczos")
    except Exception:
        return None


def resilience_summary(snap: GraphSnapshot, betweenness_k: Optional[int] = 400,
                        random_trials: int = 3, seed: int = 42, top_n: int = 20,
                        compute_algebraic_connectivity: bool = False) -> Dict[str, Any]:
    spof = single_points_of_failure(snap, top_n=top_n)
    attack = attack_simulation(snap, betweenness_k=betweenness_k, random_trials=random_trials, seed=seed)
    alg_conn = algebraic_connectivity(snap) if compute_algebraic_connectivity else None
    return {
        "single_points_of_failure": spof,
        "attack_simulation": attack,
        "algebraic_connectivity_lcc": alg_conn,
    }
