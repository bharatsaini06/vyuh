"""Train all Vyuh models on the TRAINING period only (days 1-30).

Run from the project root:
    python -m models.train

Steps
  A. Collector model   - is this receiving account a scam collector?
  B. Journey model     - is this payment part of a scam journey? (+ isotonic calibration)
     Baseline B1       - same rows, per-transaction features only (for evaluation)
     Baseline B2       - B1 + the payee's collector (mule) score: "today's best practice",
                         a per-payment model that also knows mule accounts (MuleHunter-style)
  C. Campaign confidence model + playbook library (journey-shape centroids)

The held-out "emerging" playbook starts after the training window, so the
models never see it. Test-period data (days 31-60) is never used here.

Leakage control: early stopping uses the last 15% (by time) of the TRAINING
rows; the validation days (25-30) are used only once, to fit calibration and
fusion and to report metrics.
"""
from __future__ import annotations

import json
import math
import random
import time
from datetime import datetime, timedelta
from typing import Dict, List

import joblib
import lightgbm as lgb
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from cases.manager import CampaignTracker
from core import config
from core.security import sha256_file
from models.registry import ModelBundle, fusion_inputs
from pipeline.engine import Engine
from graph.linking import community_method
from pipeline.events import (TokenMap, load_accounts, load_event_list, load_ground_truth, load_labels,
                             load_registry)
from pipeline.features import (B2_FEATURES, COLLECTOR_FEATURES, JOURNEY_FEATURES, TXN_FEATURES,
                               collector_features, txn_features)
from sim.scams import load_playbooks

LGB_PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=31, min_data_in_leaf=20,
                  feature_fraction=0.9, bagging_fraction=0.9, bagging_freq=1, lambda_l2=1.0,
                  verbose=-1, seed=7, num_threads=0)


def _log(msg: str) -> None:
    print(f"[train {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _fit_lgb(X, y, names, rounds=600):
    """Fit with early stopping on the LAST 15% of the training rows (rows are in time order)."""
    X, y = np.asarray(X), np.asarray(y)
    cut = int(len(y) * 0.85)
    if y[cut:].sum() == 0 or y[:cut].sum() == 0:      # tiny worlds: fall back to a stratified tail
        cut = len(y) - max(1, len(y) // 7)
    dtr = lgb.Dataset(X[:cut], y[:cut], feature_name=names)
    dva = lgb.Dataset(X[cut:], y[cut:], feature_name=names, reference=dtr)
    booster = lgb.train(LGB_PARAMS, dtr, num_boost_round=rounds, valid_sets=[dva],
                        callbacks=[lgb.early_stopping(50, verbose=False)])
    return booster


def _metrics(y, p) -> dict:
    y = np.asarray(y)
    if y.sum() == 0 or y.sum() == len(y):
        return {"auc": None, "pr_auc": None, "positives": int(y.sum()), "rows": int(len(y))}
    return {"auc": round(float(roc_auc_score(y, p)), 4), "pr_auc": round(float(average_precision_score(y, p)), 4),
            "positives": int(y.sum()), "rows": int(len(y))}


def train(data_dir=config.DATA_DIR, model_dir=config.MODEL_DIR) -> dict:
    config.ensure_dirs()
    model_dir.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(11)
    tokens = TokenMap()
    accounts = load_accounts(data_dir / "accounts.csv", tokens)
    labels = load_labels(data_dir / "event_labels.csv")
    truth = load_ground_truth(data_dir / "ground_truth.csv", tokens)
    registry = load_registry(data_dir / "suspect_registry.csv", tokens)
    playbooks = load_playbooks()
    start = config.SIM_START
    train_end = start + timedelta(days=config.TRAIN_DAYS)
    warm_t = (start + timedelta(days=config.WARMUP_DAYS)).timestamp()
    val_t = (start + timedelta(days=config.TRAIN_DAYS - 5)).timestamp()
    _log("loading training events ...")
    events = load_event_list(data_dir / "events.csv", tokens, start, train_end)
    _log(f"{len(events):,} events in training window")
    data_start = start.timestamp()

    # ---------------------------------------------------------- A. collector model
    _log("pass A: collector snapshots")
    eng = Engine(accounts, None, data_start, playbooks)
    eng.registry = registry
    rows_c: List[list] = []
    y_c: List[int] = []
    t_c: List[float] = []
    collector_roles = {"collector", "layer"}

    def snap(ev, p):
        if ev.t < warm_t:
            return
        lab = labels.get(ev.eid)
        is_pos = lab is not None and lab[3] in ("victim_payment", "mule_forward") \
            and any(r[0] in collector_roles for r in truth.get(ev.dst, []))
        if not is_pos:
            if ev.dst in truth or rnd.random() > 0.04:
                return
        rows_c.append(collector_features(p, accounts.get(ev.dst), ev.t, eng.state.get, registry.get(ev.dst)))
        y_c.append(1 if is_pos else 0)
        t_c.append(ev.t)

    eng.on_collector_snapshot = snap
    for ev in events:
        eng.process(ev, score=False)
    Xc, yc, tc = np.asarray(rows_c), np.asarray(y_c), np.asarray(t_c)
    tr, va = tc < val_t, tc >= val_t
    _log(f"collector rows {len(yc):,} (positives {int(yc.sum())})")
    collector = _fit_lgb(Xc[tr], yc[tr], COLLECTOR_FEATURES)
    collector.save_model(str(model_dir / "collector.txt"))
    m_coll = _metrics(yc[va], collector.predict(Xc[va]))
    _log(f"collector validation: {m_coll}")

    # ---------------------------------------------------------- B. journey model
    _log("pass B: journey rows")
    bundle = ModelBundle(model_dir)
    bundle.collector = collector
    eng = Engine(accounts, bundle, data_start, playbooks)
    eng.registry = registry
    rows_j, y_j, t_j, pb_j, dest_j = [], [], [], [], []

    def jrow(ev, jf, d, dest):
        if ev.t < warm_t:
            return
        lab = labels.get(ev.eid)
        rows_j.append(list(jf))
        dest_j.append(list(dest))
        y_j.append(1 if (lab is not None and lab[3] == "victim_payment") else 0)
        t_j.append(ev.t)
        pb_j.append(lab[1] if lab else "")

    eng.on_journey_row = jrow
    for ev in events:
        d = eng.evaluate(ev, explain=False) if ev.type == "txn" else None
        eng.apply(ev, d)
    Xj, yj, tj = np.asarray(rows_j), np.asarray(y_j), np.asarray(t_j)
    tr, va = tj < val_t, tj >= val_t
    _log(f"journey rows {len(yj):,} (positives {int(yj.sum())})")
    journey = _fit_lgb(Xj[tr], yj[tr], JOURNEY_FEATURES)
    raw_va = journey.predict(Xj[va])
    calib = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw_va, yj[va])
    journey.save_model(str(model_dir / "journey.txt"))
    joblib.dump(calib, model_dir / "journey_calibrator.joblib")
    m_j = _metrics(yj[va], raw_va)
    _log(f"journey (victim-side only) validation: {m_j}")

    # Fusion: victim-side journey probability + destination evidence -> risk.
    # Fitted on the held-back validation days so it is not fooled by in-sample scores.
    Dj = np.asarray(dest_j)
    j_cal_va = calib.predict(raw_va)
    Xf = np.asarray([fusion_inputs(j, d) for j, d in zip(j_cal_va, Dj[va])])
    fusion = LogisticRegression(max_iter=2000, C=1.0, class_weight=None).fit(Xf, yj[va])
    joblib.dump(fusion, model_dir / "fusion.joblib")
    m_fused = _metrics(yj[va], fusion.predict_proba(Xf)[:, 1])
    _log(f"fused risk (journey + destination) validation, fitted on these validation days: {m_fused}")

    Xt = np.asarray([txn_features(r, t) for r, t in zip(rows_j, t_j)])
    b1 = _fit_lgb(Xt[tr], yj[tr], TXN_FEATURES)
    b1.save_model(str(model_dir / "txn_baseline.txt"))
    m_b1 = _metrics(yj[va], b1.predict(Xt[va]))
    _log(f"baseline B1 validation: {m_b1}")
    Xt2 = np.column_stack([Xt, Dj[:, 0]])          # + payee collector (mule) score
    b2 = _fit_lgb(Xt2[tr], yj[tr], B2_FEATURES)
    b2.save_model(str(model_dir / "txn_mule_baseline.txt"))
    m_b2 = _metrics(yj[va], b2.predict(Xt2[va]))
    _log(f"baseline B2 (B1 + mule score) validation: {m_b2}")

    quantiles = {name: [float(q) for q in np.quantile(Xj[:, i], np.linspace(0.1, 0.9, 9))]
                 for i, name in enumerate(JOURNEY_FEATURES)}
    # share of training rows in each quantile bin (features with many equal values,
    # e.g. zeros, do not split into equal tenths; drift must compare against these)
    bin_shares = {}
    for i, name in enumerate(JOURNEY_FEATURES):
        cuts = np.asarray(quantiles[name])
        idx = np.searchsorted(cuts, Xj[:, i], side="right")
        bin_shares[name] = [round(float(x), 6) for x in np.bincount(idx, minlength=len(cuts) + 1) / max(len(Xj), 1)]

    # ---------------------------------------------------------- C. campaigns + library
    _log("pass C: campaign confidence + playbook library")
    bundle.journey, bundle.calibrator, bundle.txn_baseline, bundle.fusion = journey, calib, b1, fusion
    bundle.txn_mule_baseline = b2
    eng = Engine(accounts, bundle, data_start, playbooks)
    eng.registry = registry
    tracker = CampaignTracker(eng, bundle)
    victim_of: Dict[str, tuple] = {}
    for tok, roles in truth.items():
        for role, cid, pb in roles:
            if role == "victim":
                victim_of[tok] = (cid, pb)
    camp_rows, camp_y = [], []
    all_fp: List[tuple] = []          # (victim, fingerprint) at EVERY scam payment: partial + complete journeys
    next_run = warm_t
    next_emerge = warm_t
    seen = {}
    for ev in events:
        d = eng.evaluate(ev, explain=False) if ev.type == "txn" else None
        eng.apply(ev, d)
        lab = labels.get(ev.eid)
        if lab is not None and lab[3] == "victim_payment":
            rec = eng.journeys.get(ev.src)
            if rec is not None and rec.fp is not None and np.linalg.norm(rec.fp) > 0:
                all_fp.append((ev.src, rec.fp.copy()))
        if ev.t >= next_run:
            next_run = ev.t + 30 * 60
            emerge = ev.t >= next_emerge
            if emerge:
                next_emerge = ev.t + 3600
            tracker.run(ev.t, with_emergence=emerge)
            eng.expire_journeys(ev.t)
            hour = int(ev.t // 3600)
            for c in tracker.campaigns.values():
                if c.kind == "lone" or len(c.members) < 2 or not c.conf_features:
                    continue
                key = (c.case_id, hour // 3)
                if key in seen:
                    continue
                seen[key] = True
                ms = list(c.members)
                share = sum(1 for m in ms if m in victim_of) / len(ms)
                camp_rows.append(list(c.conf_features))
                camp_y.append(1 if share >= 0.6 else 0)
    cy = np.asarray(camp_y)
    _log(f"campaign snapshots {len(cy)} (positive {int(cy.sum())})")
    conf_model = None
    stale = model_dir / "campaign_confidence.joblib"
    if stale.exists():
        stale.unlink()     # never keep a model from an older training run
    if len(cy) >= 10 and 0 < cy.sum() < len(cy):
        conf_model = LogisticRegression(max_iter=1000, C=0.5).fit(np.asarray(camp_rows), cy)
        joblib.dump(conf_model, model_dir / "campaign_confidence.joblib")
    else:
        _log("campaign confidence: only one class seen - using the transparent fallback formula")
    # Playbook library = every learned journey stage (k-nearest-neighbour), so
    # playbooks with variants (e.g. digital arrest with or without an FD break)
    # are covered. Each playbook's novelty threshold is the 95th percentile of the
    # distance from a victim's snapshot to the nearest snapshot of a DIFFERENT
    # victim of the same playbook (leave-one-victim-out).
    library = {}
    by_pb: Dict[str, List[tuple]] = {}
    for tok, fp in all_fp:
        if tok in victim_of:
            by_pb.setdefault(victim_of[tok][1], []).append((tok, fp / (np.linalg.norm(fp) or 1.0)))
    for pb, items in by_pb.items():
        toks = [t for t, _f in items]
        F = np.stack([f for _t, f in items])
        S = F @ F.T
        same = np.equal.outer(np.array(toks), np.array(toks))
        S[same] = -1.0
        nn = 1.0 - S.max(axis=1)
        nn = nn[nn < 1.5]
        thr = float(np.quantile(nn, 0.95)) * 1.25 if len(nn) else 0.08
        cen = F.mean(axis=0)
        library[pb] = {"centroid": [round(float(x), 5) for x in cen],
                       "samples": [[round(float(x), 4) for x in f] for f in F],
                       "threshold": round(min(max(thr, 0.02), 0.25), 5),
                       "n": len(items), "victims": len(set(toks)),
                       "title": playbooks.get(pb, {}).get("title", pb),
                       "source": "training-period confirmed victims"}
    (model_dir / "playbook_library.json").write_text(json.dumps(library, indent=2), encoding="utf-8")
    _log(f"playbook library: {sorted(library)}")

    files = ["journey.txt", "collector.txt", "txn_baseline.txt", "txn_mule_baseline.txt", "journey_calibrator.joblib",
             "fusion.joblib", "playbook_library.json"] + (["campaign_confidence.joblib"] if conf_model else [])
    meta = {
        "version": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "training_window": [start.isoformat(), train_end.isoformat()],
        "journey_features": JOURNEY_FEATURES, "collector_features": COLLECTOR_FEATURES,
        "metrics": {"journey": m_j, "fused_risk": m_fused, "collector": m_coll, "txn_baseline_b1": m_b1,
                    "txn_mule_baseline_b2": m_b2, "campaign_snapshots": int(len(cy))},
        "community_method": community_method(),
        "lightgbm_version": getattr(lgb, "__version__", "?"),
        "journey_feature_quantiles": quantiles,
        "journey_feature_bin_shares": bin_shares,
        "hashes": {f: sha256_file(model_dir / f) for f in files},
        "note": "Synthetic data only (VyuhSim). Retrain on authorised data before any real use.",
    }
    (model_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _log("saved models to " + str(model_dir))
    return meta


if __name__ == "__main__":
    t0 = time.time()
    train()
    print(f"Training finished in {time.time() - t0:.0f}s")
