"""Emerging-playbook detection.

A campaign is *emerging* when
  1. its journey fingerprint is far from every playbook in the library
     (farther than that playbook's 95th-percentile spread), and
  2. it is bursting: more victims in the last 6 h than its own recent baseline
     predicts (Poisson tail test), or it has already reached case size.

HDBSCAN additionally clusters high-risk journeys that are NOT linked to any
campaign, so a new playbook with fully rotated infrastructure still surfaces.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
from sklearn.cluster import HDBSCAN

from core import config
from models.registry import ModelBundle
from pipeline.engine import Engine
from pipeline.state import DAY, HOUR


def poisson_tail(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    lam = max(lam, 1e-6)
    cdf = 0.0
    term = math.exp(-lam)
    for i in range(k):
        cdf += term
        term *= lam / (i + 1)
    return max(0.0, 1.0 - cdf)


def is_bursting(join_times: List[float], now: float) -> bool:
    recent = sum(1 for t in join_times if t >= now - 6 * HOUR)
    prior = sum(1 for t in join_times if now - 30 * HOUR <= t < now - 6 * HOUR)
    lam = max(prior / 4.0, 0.5)          # expected per 6 h from the previous 24 h
    return recent >= 2 and poisson_tail(recent, lam) < 0.05


def novelty(models: ModelBundle, centroid: Optional[np.ndarray]):
    if centroid is None or not models.library:
        return None, 1.0, False
    return models.nearest_playbook(centroid)


def unlinked_clusters(engine: Engine, now: float, linked: set) -> List[List[str]]:
    """HDBSCAN over high-risk journeys of the last 7 days that no campaign holds."""
    cand = [k for k, r in engine.journeys.items()
            if r.max_score >= config.RISKY_SCORE and r.fp is not None
            and r.last_t >= now - 7 * DAY and k not in linked]
    if len(cand) < 5:
        return []
    X = np.stack([engine.journeys[k].fp for k in cand])
    X = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    labels = HDBSCAN(min_cluster_size=3, min_samples=2, copy=True).fit_predict(X)
    groups: Dict[int, List[str]] = {}
    for k, lab in zip(cand, labels):
        if lab >= 0:
            groups.setdefault(int(lab), []).append(k)
    return [g for g in groups.values() if len(g) >= config.CASE_MIN_MEMBERS]
