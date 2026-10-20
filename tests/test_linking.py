"""Campaign linking safeguards: one case = one gang."""
import networkx as nx

from graph.linking import _is_pass_through, detect_communities, split_on_money
from pipeline.engine import Engine
from pipeline.state import AcctState


def test_money_groups_joined_only_by_lookalike_links_are_split():
    G = nx.Graph()
    for a, b in [("a1", "a2"), ("a2", "a3"), ("b1", "b2"), ("b2", "b3")]:
        G.add_edge(a, b, weight=1.0, types={"shared_collector"})
    G.add_edge("a3", "b1", weight=0.45, types={"fingerprint"})     # two gangs, same script
    parts = split_on_money(G, [set(G.nodes)])
    assert sorted(sorted(p) for p in parts) == [["a1", "a2", "a3"], ["b1", "b2", "b3"]]


def test_communities_are_connected():
    G = nx.Graph()
    G.add_edge("x1", "x2", weight=1.0, types={"shared_collector"})
    G.add_edge("y1", "y2", weight=1.0, types={"shared_collector"})
    for c in detect_communities(G):
        assert nx.is_connected(G.subgraph(c))


def test_pass_through_ignores_small_ordinary_payments():
    s = AcctState(0.0)
    s.inb.append((1000.0, "victim", 20000.0, True))
    assert _is_pass_through(s, 1600.0, 19500.0)          # forwards what just arrived
    assert not _is_pass_through(s, 1600.0, 500.0)        # pays a friend: not forwarding
    assert not _is_pass_through(s, 1000.0 + 3 * 3600, 19500.0)   # too late


def test_threshold_jitter_is_deterministic_and_small():
    a = Engine.jitter("E00000123", "r1")
    assert a == Engine.jitter("E00000123", "r1")
    assert abs(a) <= 0.02
