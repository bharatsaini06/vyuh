"""Campaign linking (slow path).

Builds a graph whose nodes are risky journeys and whose edges say "these two
victims are probably being worked by the same operation":

  shared_collector   both paid the same collector-like account          (breaks if mules rotate)
  shared_downstream  their collectors forwarded money to the same account (survives some rotation)
  fingerprint        their journeys have the same shape                  (survives full rotation)

Community detection then splits the graph into campaigns:

  * Leiden (python-igraph + leidenalg) when those optional packages are installed;
  * otherwise Louvain (NetworkX) followed by a Leiden-style *connectivity
    refinement*: every community is split into its connected parts. Louvain can
    return communities whose members are not even connected to each other
    (Traag, Waltman & van Eck 2019, "From Louvain to Leiden"); for an
    investigator that would mean two unrelated victims shown as one gang.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import networkx as nx
import numpy as np

from core import config
from pipeline.engine import Engine, JourneyRec
from pipeline.state import DAY, HOUR


@dataclass
class Community:
    members: List[str]
    edge_types: Dict[str, int] = field(default_factory=dict)
    collectors: Set[str] = field(default_factory=set)
    downstream: Set[str] = field(default_factory=set)
    fp_only_share: float = 0.0
    tightness: float = 0.0
    centroid: Optional[np.ndarray] = None
    strong: Set[str] = field(default_factory=set)    # members with a money link, or 2+ shape links, inside


MAX_COLLECTOR_PAYERS_7D = 60


def is_collector_candidate(engine: Engine, payee: str, min_score: float = config.COLLECTOR_LINK_MIN) -> bool:
    """A payee can link victims only if it looks like a mule, not like infrastructure.

    Payment aggregators, big shops and billers also receive from many strangers,
    but from hundreds of them; mule/collector accounts serve a limited set of victims.
    """
    if payee in engine.suppressed:
        return False
    ps = engine.state.get(payee)
    if ps is None or ps.collector_score < min_score:
        return False
    return len({x[1] for x in ps.inb}) <= MAX_COLLECTOR_PAYERS_7D


def _is_pass_through(ps, t_out: float, amount: float) -> bool:
    """Does this outgoing payment pass on money that just arrived?

    True when it is 50-100% of ONE payment received in the 2 hours before, or
    85-100% of everything received in those 2 hours. A recruited mule who also
    pays a friend Rs 500 or the monthly rent is not forwarding, so those payments
    must not link the mule's victims to someone else's victims.
    """
    recent = [x[2] for x in ps.inb if t_out - 2 * HOUR <= x[0] <= t_out]
    if not recent:
        return False
    if any(0.5 * a <= amount <= a * 1.001 for a in recent):
        return True
    tot = sum(recent)
    return 0.85 * tot <= amount <= tot * 1.001


def active_journeys(engine: Engine, now: float) -> Dict[str, JourneyRec]:
    horizon = now - config.LINK_WINDOW_HOURS * HOUR
    return {k: r for k, r in engine.journeys.items()
            if r.last_t >= horizon and r.max_score >= config.LINK_MIN_SCORE and r.fp is not None}


def build_graph(engine: Engine, now: float, weights: Optional[Dict[str, float]] = None) -> nx.Graph:
    w = dict(config.LINK_WEIGHTS)
    if weights:
        w.update(weights)
    js = active_journeys(engine, now)
    G = nx.Graph()
    for k, r in js.items():
        G.add_node(k)
    if len(js) < 2:
        return G

    def add(a: str, b: str, kind: str, weight: float) -> None:
        if a == b:
            return
        if G.has_edge(a, b):
            e = G[a][b]
            e["weight"] = max(e["weight"], weight)
            e["types"].add(kind)
        else:
            G.add_edge(a, b, weight=weight, types={kind})

    # 1. shared collector
    by_payee: Dict[str, List[str]] = {}
    for k, r in js.items():
        for payee in r.payees:
            by_payee.setdefault(payee, []).append(k)
    downstream_of: Dict[str, Set[str]] = {}
    for payee, members in by_payee.items():
        ps = engine.state.get(payee)
        if not is_collector_candidate(engine, payee, 0.2):
            continue
        if len(members) >= 2 and ps.collector_score >= config.COLLECTOR_LINK_MIN:
            for a, b in zip(members, members[1:]):
                add(a, b, "shared_collector", w["shared_collector"])
        # where did this payee send money? (individual accounts only)
        if ps is not None:
            # Only FORWARDING counts: money sent on within 2 hours of arriving, to an
            # individual account that is not itself a busy shop-like receiver.
            # (Recruited mules also make ordinary payments, e.g. to a local shopkeeper;
            # those must not link unrelated campaigns.)
            outs: Dict[str, None] = {}
            for (_t, dst, _amt, dwell) in ps.outb:
                if dwell > 120 or not _is_pass_through(ps, _t, _amt):
                    continue
                info = engine.accounts.get(dst)
                if info is not None and info.holder_type != "individual":
                    continue
                ds = engine.state.get(dst)
                if ds is not None and len({x[1] for x in ds.inb}) > 40:
                    continue
                outs[dst] = None
            downstream_of[payee] = outs

    # 2. shared downstream account (2-hop)
    by_down: Dict[str, Dict[str, None]] = {}          # insertion-ordered sets (reproducible)
    for payee, outs in downstream_of.items():
        for d in outs:
            by_down.setdefault(d, {}).update(dict.fromkeys(by_payee.get(payee, [])))
    for d, members in by_down.items():
        ml = list(members)
        if 2 <= len(ml) <= 400:
            for a, b in zip(ml, ml[1:]):
                add(a, b, "shared_downstream", w["shared_downstream"])

    # Each journey's known cash-out trail: the collector-like accounts it paid and where
    # those accounts forwarded the money. Two journeys whose trails are BOTH known and
    # share nothing are being cashed out by different operations, so a look-alike shape
    # alone must not merge them (different gangs often run the same public script).
    trail: Dict[str, Set[str]] = {}
    for k, r in js.items():
        down: Set[str] = set()
        coll: Set[str] = set()
        for payee in r.payees:
            if payee in downstream_of:          # collector-like payees (score >= 0.2)
                coll.add(payee)
                down.update(downstream_of[payee])
        if down:
            trail[k] = coll | down

    # 3. fingerprint similarity
    keys = list(js)
    F = np.stack([js[k].fp for k in keys])
    norms = np.linalg.norm(F, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Fn = F / norms
    S = Fn @ Fn.T
    # Only journeys with real substance (risky, 2+ payments to fresh payees) get
    # shape links, and each node keeps only its K most similar neighbours, so the
    # graph stays small even when many ordinary journeys look alike.
    thr = config.FINGERPRINT_SIM
    eligible = np.array([js[k].max_score >= config.RISKY_SCORE and (js[k].n_payments >= 2 or js[k].max_score >= 0.9)
                         for k in keys])
    # shape links only join journeys that resemble the SAME playbook (or are both novel)
    models = engine.models
    labels = []
    for k in keys:
        if models is not None and models.library:
            name, _d, novel = models.nearest_playbook(js[k].fp)
            labels.append("novel" if novel else name)
        else:
            labels.append("any")
    np.fill_diagonal(S, -1.0)
    S[~eligible, :] = -1.0
    S[:, ~eligible] = -1.0
    K = 5
    for i in np.where(eligible)[0].tolist():
        row = S[i]
        cand = np.argpartition(-row, min(K, len(row) - 1))[:K]
        for j in cand.tolist():
            if row[j] >= thr and labels[i] == labels[j]:
                ti, tj = trail.get(keys[i]), trail.get(keys[j])
                if ti and tj and not (ti & tj):
                    G.graph["fp_blocked"] = G.graph.get("fp_blocked", 0) + 1
                    continue                    # different, fully known money trails
                add(keys[i], keys[j], "fingerprint", w["fingerprint"] * float(row[j]))
    G.graph["fp_matrix"] = (keys, Fn)
    return G


try:                                   # optional, faster and guarantees well-connected communities
    import igraph as _ig                # type: ignore
    import leidenalg as _la             # type: ignore
    HAVE_LEIDEN = True
except Exception:                       # pragma: no cover - depends on the machine
    HAVE_LEIDEN = False

COMMUNITY_RESOLUTION = 1.2
COMMUNITY_SEED = 7


def community_method() -> str:
    return "leiden" if HAVE_LEIDEN else "louvain+connectivity-refinement"


def detect_communities(G: nx.Graph) -> List[Set[str]]:
    """Partition the link graph. Deterministic (fixed seed, sorted node order)."""
    if HAVE_LEIDEN:
        nodes = list(G.nodes)
        idx = {n: i for i, n in enumerate(nodes)}
        g = _ig.Graph(n=len(nodes), edges=[(idx[a], idx[b]) for a, b in G.edges])
        g.es["weight"] = [float(G[a][b]["weight"]) for a, b in G.edges]
        part = _la.find_partition(g, _la.RBConfigurationVertexPartition, weights="weight",
                                  resolution_parameter=COMMUNITY_RESOLUTION, seed=COMMUNITY_SEED)
        return [{nodes[i] for i in comm} for comm in part]
    # Node order = order journeys were first seen (event order), so results do not
    # depend on the pseudonymisation key or on Python's per-run string hashing.
    order = {n: i for i, n in enumerate(G.nodes)}
    raw = nx.algorithms.community.louvain_communities(G, weight="weight", resolution=COMMUNITY_RESOLUTION,
                                                      seed=COMMUNITY_SEED)
    out: List[Set[str]] = []
    for c in raw:                       # refinement: never keep a disconnected "community"
        sub = G.subgraph(c)
        for part in nx.connected_components(sub):
            out.append(set(part))
    out.sort(key=lambda c: (-len(c), min(order[n] for n in c)))
    return out


MONEY_TYPES = {"shared_collector", "shared_downstream"}


def split_on_money(G: nx.Graph, comms: List[Set[str]]) -> List[Set[str]]:
    """Second refinement: two groups that are each linked by MONEY but joined to each
    other only through look-alike (fingerprint) links are two operations running the
    same script, not one. Split them; journeys with only look-alike links go to the
    group they resemble most.
    """
    out: List[Set[str]] = []
    for c in comms:
        sub = G.subgraph(c)
        M = nx.Graph()
        M.add_nodes_from(sub.nodes)
        M.add_edges_from((a, b) for a, b, d in sub.edges(data=True) if d["types"] & MONEY_TYPES)
        groups = [set(g) for g in nx.connected_components(M) if len(g) >= 2]
        if len(groups) < 2:
            out.append(set(c))
            continue
        where = {n: i for i, g in enumerate(groups) for n in g}
        rest: Set[str] = set()
        pending = [n for n in sub.nodes if n not in where]
        for _round in range(3):                 # let look-alike-only chains settle
            nxt = []
            pend = set(pending)
            for n in pending:
                score: Dict[int, float] = {}
                own = 0.0                        # links to other look-alike-only journeys
                for nb in sub.neighbors(n):
                    if nb in where:
                        score[where[nb]] = score.get(where[nb], 0.0) + sub[n][nb]["weight"]
                    elif nb in pend:
                        own += sub[n][nb]["weight"]
                if score and max(score.values()) >= own:
                    gi = max(score, key=lambda i: (score[i], -i))
                    groups[gi].add(n)
                    where[n] = gi
                else:
                    nxt.append(n)
            pending = nxt
        rest.update(pending)
        out.extend(groups)
        if len(rest) >= 2:
            out.extend(set(g) for g in nx.connected_components(sub.subgraph(rest)) if len(g) >= 2)
    return out


def find_communities(engine: Engine, now: float, weights: Optional[Dict[str, float]] = None,
                     min_size: int = 2) -> List[Community]:
    G = build_graph(engine, now, weights)
    if G.number_of_edges() == 0:
        return []
    comms = split_on_money(G, detect_communities(G))
    keys, Fn = G.graph.get("fp_matrix", ([], None))
    pos = {k: i for i, k in enumerate(keys)}
    out = []
    for c in comms:
        members = [n for n in G.nodes if n in c]     # stable, key-independent order
        if len(members) < min_size:
            continue
        sub = G.subgraph(members)
        et: Dict[str, int] = {}
        fp_only = 0
        fp_deg: Dict[str, int] = {}
        strong: Set[str] = set()
        for _a, _b, data in sub.edges(data=True):
            for kind in data["types"]:
                et[kind] = et.get(kind, 0) + 1
            if data["types"] == {"fingerprint"}:
                fp_only += 1
                fp_deg[_a] = fp_deg.get(_a, 0) + 1
                fp_deg[_b] = fp_deg.get(_b, 0) + 1
            else:
                strong.update((_a, _b))
        strong.update(k for k, n in fp_deg.items() if n >= 2)
        n_edges = max(1, sub.number_of_edges())
        vecs = Fn[[pos[m] for m in members]] if Fn is not None else None
        centroid = vecs.mean(axis=0) if vecs is not None else None
        tight = float((vecs @ (centroid / (np.linalg.norm(centroid) or 1.0))).mean()) if vecs is not None else 0.0
        colls = set()
        for m in members:
            for payee in engine.journeys[m].payees:
                if is_collector_candidate(engine, payee):
                    colls.add(payee)
        out.append(Community(members, et, colls, set(), fp_only / n_edges, tight,
                             engine_centroid(engine, members), strong))
    return out


def engine_centroid(engine: Engine, members: List[str]) -> Optional[np.ndarray]:
    vs = [engine.journeys[m].fp for m in members if engine.journeys[m].fp is not None]
    if not vs:
        return None
    return np.mean(np.stack(vs), axis=0)
