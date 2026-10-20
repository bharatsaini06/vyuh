"""Model drift monitoring with the Population Stability Index (PSI).

PSI compares how a feature is distributed now versus during training.
Rule of thumb: < 0.1 stable, 0.1-0.25 watch, > 0.25 investigate / retrain.
Drift alarms go to the ML owner, not to investigators.
"""
from __future__ import annotations

import math
from typing import Dict

import numpy as np

from pipeline.features import JOURNEY_FEATURES


def psi(live: np.ndarray, cutpoints, expected_shares=None) -> float:
    cuts = np.asarray(cutpoints, dtype=float)
    if expected_shares is not None and len(expected_shares) == len(cuts) + 1:
        expected = np.clip(np.asarray(expected_shares, dtype=float), 1e-4, None)
    else:
        expected = np.full(len(cuts) + 1, 1.0 / (len(cuts) + 1))
    idx = np.searchsorted(cuts, live, side="right")
    actual = np.bincount(idx, minlength=len(cuts) + 1) / max(len(live), 1)
    actual = np.clip(actual, 1e-4, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


MIN_SAMPLES = 1000
# Grows with calendar time by construction (days since the account was first seen),
# so it always "drifts"; it is reported but not alarmed on.
TIME_TREND = {"tenure_log"}


def psi_report(rt) -> Dict:
    q = rt.models.metadata.get("journey_feature_quantiles", {})
    shares = rt.models.metadata.get("journey_feature_bin_shares", {})
    rows = list(rt.recent_features)
    if len(rows) < MIN_SAMPLES or not q:
        return {"status": "collecting", "samples": len(rows), "features": []}
    X = np.asarray(rows)
    out = []
    for i, name in enumerate(JOURNEY_FEATURES):
        cuts = q.get(name)
        if not cuts or (name not in shares and len(set(round(c, 9) for c in cuts)) < len(cuts)):
            out.append({"feature": name, "psi": None, "note": "discrete feature - PSI not meaningful"})
            continue
        v = psi(X[:, i], cuts, shares.get(name))
        if name in TIME_TREND:
            out.append({"feature": name, "psi": None, "note": "grows with time by design - not alarmed"})
            continue
        out.append({"feature": name, "psi": round(v, 4),
                    "state": "stable" if v < 0.1 else "watch" if v < 0.25 else "investigate"})
    scored = [f["psi"] for f in out if f["psi"] is not None]
    worst = max(scored) if scored else 0.0
    return {"status": "ok", "samples": len(rows), "worst_psi": round(worst, 4),
            "overall": "stable" if worst < 0.1 else "watch" if worst < 0.25 else "investigate",
            "features": out}
