"""Model bundle: loads trained artifacts, scores, and explains.

Artifacts in models/saved/:
    journey.txt / collector.txt / txn_baseline.txt   LightGBM boosters (text format)
    txn_mule_baseline.txt                             baseline B2 (B1 + payee mule score)
    journey_calibrator.joblib                         isotonic calibration
    fusion.joblib                                     journey + destination evidence -> risk
    campaign_confidence.joblib                        logistic regression
    playbook_library.json                             known playbook centroids
    metadata.json                                     versions, metrics, hashes
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import lightgbm as lgb
import numpy as np

from core import config
from core.security import sha256_file
from pipeline.features import COLLECTOR_FEATURES, JOURNEY_FEATURES, STEP_DIMS, cosine

# Plain-language reasons for the most influential features.
REASON_TEMPLATES = {
    "fresh_debits_72h": "{v:.0f} payments to new payees in the last 72 h",
    "fresh_debits_24h": "{v:.0f} payments to new payees in the last 24 h",
    "fresh_payees_7d": "paid {v:.0f} different new payees this week",
    "ladder_len": "{v:.0f} payments in a row, each as big or bigger than the last",
    "ladder_max_ratio": "a payment grew to {v:.1f}x the previous one",
    "ladder_slope": "amounts keep escalating",
    "stranger_credits_7d": "received {v:.0f} small credit(s) from strangers this week",
    "refund_credits_72h": "money was 'paid back' by a new payee (trust-building trick)",
    "call_active": "payment made during an active phone call",
    "screen_share": "screen-sharing was on during the payment",
    "remote_app": "a remote-access app is present on the device",
    "paste_payee": "payee ID was pasted (often sent by someone else)",
    "call_frac_fresh_72h": "most recent new-payee payments happened during calls",
    "funding_24h_to_baseline": "a loan or fixed deposit was turned into cash in the last 24 h",

    "amount_to_baseline": "amount is {v:.1f}x this customer's normal monthly spending",
    "fresh_sum_72h_to_baseline": "{v:.1f}x normal monthly spending sent to new payees in 72 h",
    "outflow_7d_to_baseline": "spent {v:.1f}x the normal monthly amount this week",
    "is_collect": "payment started by scanning a QR code or answering a request (this SENDS money)",
    "remark_risky": "payment note uses words common in scams",
    "payee_fresh": "payee was first paid within the last 30 days",
    "payee_never_paid": "first ever payment to this payee",
    "log_session": "unusually long, hesitant app session",
    "stage_count": "{v:.0f} different scam stages seen in this journey",
    "night": "payment at night",
    "journey_age_hours": "pattern has continued for {v:.0f} hours",
}


def fusion_inputs(j: float, dest: List[float]) -> List[float]:
    jc = min(max(j, 1e-4), 1 - 1e-4)
    lj = float(np.log(jc / (1 - jc)))
    c, fri, page = dest
    return [lj, c, lj * c, fri, page]


class ModelBundle:
    def __init__(self, model_dir: Path = config.MODEL_DIR):
        self.dir = Path(model_dir)
        self.journey: Optional[lgb.Booster] = None
        self.collector: Optional[lgb.Booster] = None
        self.txn_baseline: Optional[lgb.Booster] = None
        self.txn_mule_baseline: Optional[lgb.Booster] = None
        self.calibrator = None
        self.fusion = None
        self.campaign_conf = None
        self.library: Dict[str, dict] = {}
        self.metadata: dict = {}

    # ------------------------------------------------------------ loading
    @classmethod
    def load(cls, model_dir: Path = config.MODEL_DIR) -> "ModelBundle":
        b = cls(model_dir)
        d = b.dir
        if (d / "journey.txt").exists():
            b.journey = lgb.Booster(model_file=str(d / "journey.txt"))
        if (d / "collector.txt").exists():
            b.collector = lgb.Booster(model_file=str(d / "collector.txt"))
        if (d / "txn_baseline.txt").exists():
            b.txn_baseline = lgb.Booster(model_file=str(d / "txn_baseline.txt"))
        if (d / "txn_mule_baseline.txt").exists():
            b.txn_mule_baseline = lgb.Booster(model_file=str(d / "txn_mule_baseline.txt"))
        if (d / "journey_calibrator.joblib").exists():
            b.calibrator = joblib.load(d / "journey_calibrator.joblib")
        if (d / "fusion.joblib").exists():
            b.fusion = joblib.load(d / "fusion.joblib")
        if (d / "campaign_confidence.joblib").exists():
            b.campaign_conf = joblib.load(d / "campaign_confidence.joblib")
        if (d / "playbook_library.json").exists():
            b.library = json.loads((d / "playbook_library.json").read_text(encoding="utf-8"))
        if (d / "metadata.json").exists():
            b.metadata = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        return b

    @property
    def ready(self) -> bool:
        return self.journey is not None and self.collector is not None

    def verify_hashes(self) -> Dict[str, bool]:
        """Check model files have not been altered since training (signed artifacts)."""
        out = {}
        for name, h in self.metadata.get("hashes", {}).items():
            p = self.dir / name
            out[name] = p.exists() and sha256_file(p) == h
        return out

    # ------------------------------------------------------------ scoring
    def collector_score(self, feats: List[float]) -> float:
        if self.collector is None:
            return 0.0
        return float(self.collector.predict(np.asarray([feats]), num_threads=1)[0])

    def journey_score(self, feats: List[float]) -> float:
        if self.journey is None:
            return 0.0
        raw = float(self.journey.predict(np.asarray([feats]), num_threads=1)[0])
        if self.calibrator is not None:
            return float(self.calibrator.predict([raw])[0])
        return raw

    def fused_risk(self, j: float, dest: List[float]) -> float:
        """Combine victim-side journey probability with destination evidence."""
        if self.fusion is None:
            return 1 - (1 - j) * (1 - 0.5 * dest[0])          # transparent fallback
        return float(self.fusion.predict_proba(np.asarray([fusion_inputs(j, dest)]))[0, 1])

    def explain_journey(self, feats: List[float], top: int = 4) -> List[dict]:
        """Top reasons from LightGBM's built-in SHAP values (pred_contrib)."""
        if self.journey is None:
            return []
        contrib = self.journey.predict(np.asarray([feats]), pred_contrib=True, num_threads=1)[0][:-1]
        order = np.argsort(-contrib)
        reasons = []
        for i in order[: top * 2]:
            if contrib[i] <= 0:
                break
            name = JOURNEY_FEATURES[i]
            tpl = REASON_TEMPLATES.get(name)
            if not tpl:
                continue
            reasons.append({"feature": name, "value": round(float(feats[i]), 3),
                            "impact": round(float(contrib[i]), 3), "text": tpl.format(v=feats[i])})
            if len(reasons) >= top:
                break
        return reasons

    def txn_score(self, feats: List[float]) -> float:
        if self.txn_baseline is None:
            return 0.0
        return float(self.txn_baseline.predict(np.asarray([feats]), num_threads=1)[0])

    def txn_mule_score(self, feats: List[float]) -> float:
        if self.txn_mule_baseline is None:
            return 0.0
        return float(self.txn_mule_baseline.predict(np.asarray([feats]), num_threads=1)[0])

    def campaign_confidence(self, feats: List[float]) -> float:
        if self.campaign_conf is None:
            # transparent fallback before the model is trained
            n, mean_s, _max_s, frac_hi, ev_types, fp_only, tight, coll, _paid = feats
            return float(min(0.95, 0.2 + 0.15 * ev_types + 0.3 * mean_s + 0.1 * tight))
        return float(self.campaign_conf.predict_proba(np.asarray([feats]))[0, 1])

    # ------------------------------------------------------------ playbooks
    def _samples(self, name: str) -> np.ndarray:
        cache = self.__dict__.setdefault("_sample_cache", {})
        if name not in cache:
            pb = self.library[name]
            arr = np.asarray(pb.get("samples") or [pb["centroid"]], dtype=float)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            cache[name] = arr / norms
        return cache[name]

    def new_steps(self, name: str, fp: np.ndarray) -> List[str]:
        """Scam steps present in this journey that playbook `name` has never shown."""
        samples = self._samples(name)
        if len(samples) < 5:
            return []
        seen = samples.max(axis=0)
        return [label for d, label in STEP_DIMS.items() if fp[d] > 0 and seen[d] <= 0]

    def confident_playbook(self, fp: np.ndarray, margin: float = 2.0) -> Optional[str]:
        """Name a playbook only when the journey is clearly closer to it than to any other
        (used for a journey's FIRST risky payment, when its shape is still thin). A wrong
        named warning is worse than a generic one."""
        n = np.linalg.norm(fp)
        if n == 0 or not self.library:
            return None
        v = fp / n
        rels = []
        for name, pb in self.library.items():
            d = float(1.0 - (self._samples(name) @ v).max())
            rel = d / max(pb["threshold"], 1e-6)
            if self.new_steps(name, fp):
                rel = max(rel, 1.01)
            rels.append((rel, name))
        rels.sort()
        best_rel, best = rels[0]
        second = rels[1][0] if len(rels) > 1 else 9e9
        if best_rel <= 1.0 and second >= margin * max(best_rel, 1e-3):
            return best
        return None

    def nearest_playbook(self, fp: np.ndarray) -> Tuple[Optional[str], float, bool]:
        """Return (name, distance, is_novel) using nearest learned journey snapshots.

        Novel = farther than every playbook's own threshold (measured on unseen victims).
        """
        n = np.linalg.norm(fp)
        if n == 0:
            return None, 1.0, True
        v = fp / n
        best, best_rel, best_dist = None, 1e9, 1.0
        for name, pb in self.library.items():
            samples = self._samples(name)
            d = float(1.0 - (samples @ v).max())
            rel = d / max(pb["threshold"], 1e-6)
            if self.new_steps(name, fp):
                rel = max(rel, 1.01)       # contains a step this playbook has never shown
            if rel < best_rel:
                best, best_rel, best_dist = name, rel, d
        if best is None:
            return None, 1.0, True
        return best, best_dist, best_rel > 1.0
