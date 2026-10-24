"""Test Lab: run Vyuh on a file of payments that someone else chose.

A judge (or a bank analyst) uploads a CSV of payments. Vyuh runs them through a
FRESH engine - separate from the live demo, with no knowledge of the demo's
customers - in time order, exactly like the live system would see them:

  * every payment is scored (risk, response level R0-R3, plain-language reasons),
  * the campaign linker runs every 15 simulated minutes, so cases form as they would live,
  * if the file has a `label` column (scam / genuine), Vyuh reports what it got right,
    what it missed, how many genuine payments it disturbed (false positives) and
    lists every failure case. The same file is also run through the simple
    rules engine (B0) for comparison.

Identifiers are pseudonymised (HMAC) on arrival, as in production. The raw ids
from the file are kept only in memory for this lab session, so the results can
be shown back to the person who uploaded them; nothing is written to disk.
"""
from __future__ import annotations

import csv
import io
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from cases.manager import CampaignTracker
from core import config
from core.security import pseudonymise
from pipeline.engine import Engine
from pipeline.events import EVENT_TYPES, AccountInfo, Event, normalise_remark
from pipeline.state import DAY

MAX_ROWS = 50_000
MAX_BYTES = 8 * 1024 * 1024
LINK_EVERY_MIN = 15
DEFAULT_ACCOUNT_AGE_DAYS = 180          # used when the file does not say how old an account is

LEVEL_TEXT = {"R0": "No action", "R1": "Warning", "R2": "Paused 30 min", "R2T": "Trusted contact asked",
              "R3": "Paused + call-back"}

# Column names we accept (left) -> our name (right). Lower-case, spaces/dashes become "_".
ALIASES = {
    "event_id": "event_id", "id": "event_id", "txn_id": "event_id", "transaction_id": "event_id",
    "ts": "ts", "timestamp": "ts", "time": "ts", "datetime": "ts", "date_time": "ts",
    "type": "type", "event_type": "type",
    "src": "src", "payer": "src", "from": "src", "sender": "src", "from_account": "src", "payer_id": "src",
    "dst": "dst", "payee": "dst", "to": "dst", "receiver": "dst", "to_account": "dst", "payee_id": "dst",
    "amount": "amount", "amt": "amount", "amount_inr": "amount",
    "rail": "rail", "channel": "rail",
    "remark": "remark", "note": "remark", "narration": "remark", "description": "remark",
    "is_collect": "is_collect", "collect_request": "is_collect",
    "call_active": "call_active", "on_call": "call_active", "call": "call_active",
    "screen_share": "screen_share", "screen_sharing": "screen_share",
    "remote_app": "remote_app", "remote_access_app": "remote_app",
    "paste_payee": "paste_payee", "pasted_payee": "paste_payee",
    "session_sec": "session_sec", "session_seconds": "session_sec",
    "payee_fri": "payee_fri", "fri": "payee_fri",
    "label": "label", "is_scam": "label", "is_fraud": "label", "fraud": "label", "scam": "label",
    "payee_type": "payee_type", "payee_account_age_days": "payee_age_days", "payee_age_days": "payee_age_days",
    "payer_account_age_days": "payer_age_days", "payer_age_days": "payer_age_days",
    "payer_age_band": "payer_age_band", "age_band": "payer_age_band",
    "trusted_contact": "trusted_contact", "payer_trusted_contact": "trusted_contact",
}
REQUIRED = ("ts", "src", "dst", "amount")
SCAM_WORDS = {"scam", "fraud", "1", "yes", "y", "true", "victim_payment", "fraudulent"}
GENUINE_WORDS = {"genuine", "legit", "legitimate", "0", "no", "n", "false", "normal", "benign"}

TEMPLATE_HEADER = ["event_id", "timestamp", "payer", "payee", "amount", "type", "remark", "on_call",
                   "screen_share", "remote_app", "pasted_payee", "session_seconds", "payee_fri", "is_collect",
                   "payee_type", "payee_account_age_days", "payer_age_band", "trusted_contact", "label"]
TEMPLATE_ROWS = [
    ["E1", "2026-10-20 10:00", "cust_ravi", "grocer_shop", "850", "txn", "groceries", 0, 0, 0, 0, 40, 0, 0,
     "business", 900, "36-59", 0, "genuine"],
    ["E2", "2026-10-21 11:02", "mule_aa1", "cust_ravi", "500", "txn", "task", 0, 0, 0, 0, 0, 0, 0,
     "individual", 6, "36-59", 0, "genuine"],
    ["E3", "2026-10-21 11:40", "cust_ravi", "mule_aa1", "2000", "txn", "task", 0, 0, 0, 1, 25, 0, 0,
     "individual", 6, "36-59", 0, "scam"],
    ["E4", "2026-10-21 13:15", "cust_ravi", "mule_bb2", "9000", "txn", "fee", 1, 0, 0, 1, 30, 0, 0,
     "individual", 4, "36-59", 0, "scam"],
]
TEMPLATE_NOTES = [
    "# Vyuh Test Lab template. One row = one payment. Lines starting with # are ignored.",
    "# Required: timestamp, payer, payee, amount. Everything else is optional (0 / blank = no).",
    "# type: txn (payment, default) | loan (instant loan credited to payer) | fd_break (fixed deposit broken)",
    "# on_call / screen_share / remote_app / pasted_payee / is_collect: 1 = yes, 0 = no",
    "# payee_fri: Fraud Risk Indicator of the payee's mobile number, 0-3 (0 = none)",
    "# payee_type: individual | business.  payee_account_age_days: how old the receiving account is",
    "#   (if blank, accounts are treated as 180 days old).  payer_age_band: e.g. 18-35, 36-59, 60+",
    "# trusted_contact: 1 if the payer set up family approval for large risky payments",
    "# label (optional): scam | genuine. If present, Vyuh reports its accuracy and every mistake.",
]


def template_csv() -> str:
    buf = io.StringIO()
    for line in TEMPLATE_NOTES:
        buf.write(line + "\n")
    w = csv.writer(buf)
    w.writerow(TEMPLATE_HEADER)
    w.writerows(TEMPLATE_ROWS)
    return buf.getvalue()


# ------------------------------------------------------------------ parsing
def _key(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_").lstrip("﻿")


def _parse_time(text: str) -> datetime:
    s = (text or "").strip()
    if not s:
        raise ValueError("timestamp is empty")
    try:
        return datetime.fromisoformat(s.replace("Z", "").replace("T", " "))
    except ValueError:
        pass
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"cannot read timestamp '{s}' (use YYYY-MM-DD HH:MM)")


def _flag(v) -> int:
    s = str(v if v is not None else "").strip().lower()
    return 1 if s in ("1", "yes", "y", "true", "on") else 0


def _int(v, lo: int = 0, hi: int = 10 ** 9) -> int:
    try:
        return max(lo, min(hi, int(float(str(v).strip() or 0))))
    except ValueError:
        return lo


def _label(v) -> Optional[int]:
    s = str(v or "").strip().lower()
    if s in SCAM_WORDS:
        return 1
    if s in GENUINE_WORDS:
        return 0
    return None


def parse_csv(text: str) -> Tuple[List[dict], List[str]]:
    """Read the uploaded CSV. Returns (rows, problems). Bad rows are skipped and reported."""
    if len(text.encode("utf-8", errors="ignore")) > MAX_BYTES:
        raise ValueError(f"File is too large (limit {MAX_BYTES // (1024 * 1024)} MB).")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise ValueError("The file is empty.")
    reader = csv.DictReader(lines)
    cols = {c: ALIASES.get(_key(c)) for c in (reader.fieldnames or [])}
    have = set(v for v in cols.values() if v)
    missing = [c for c in REQUIRED if c not in have]
    if missing:
        names = {"ts": "timestamp", "src": "payer", "dst": "payee", "amount": "amount"}
        raise ValueError("Missing column(s): " + ", ".join(names[m] for m in missing)
                         + ". Download the template to see the format.")
    rows, problems = [], []
    for i, raw in enumerate(reader, start=2):
        if len(rows) >= MAX_ROWS:
            problems.append(f"Only the first {MAX_ROWS:,} rows were used.")
            break
        r = {cols[k]: (v or "").strip() for k, v in raw.items() if k in cols and cols[k]}
        try:
            ts = _parse_time(r.get("ts", ""))
            typ = (r.get("type") or "txn").lower()
            if typ not in EVENT_TYPES:
                raise ValueError(f"type must be txn, loan or fd_break (got '{typ}')")
            amount = float(str(r.get("amount", "")).replace(",", "").replace("₹", "").strip())
            if not (0 < amount < 1e9):
                raise ValueError("amount must be more than 0")
            src = r.get("src", "")
            if not src:
                raise ValueError("payer is empty")
            dst = r.get("dst", "")
            if typ == "txn" and not dst:
                raise ValueError("payee is empty")
        except ValueError as exc:
            if len(problems) < 25:
                problems.append(f"Row {i}: {exc}")
            continue
        rows.append({
            "event_id": (r.get("event_id") or f"row{i}")[:64], "ts": ts, "type": typ, "src": src[:64],
            "dst": dst[:64], "amount": amount, "rail": (r.get("rail") or "UPI")[:12],
            "remark_raw": r.get("remark", "")[:60], "is_collect": _flag(r.get("is_collect")),
            "call": _flag(r.get("call_active")), "screen": _flag(r.get("screen_share")),
            "remote": _flag(r.get("remote_app")), "paste": _flag(r.get("paste_payee")),
            "sess": _int(r.get("session_sec"), 0, 86400), "fri": _int(r.get("payee_fri"), 0, 3),
            "label": _label(r.get("label")), "payee_type": (r.get("payee_type") or "").lower(),
            "payee_age_days": r.get("payee_age_days", ""), "payer_age_days": r.get("payer_age_days", ""),
            "payer_age_band": r.get("payer_age_band", ""), "trusted": _flag(r.get("trusted_contact")),
            "line": i,
        })
    if not rows:
        raise ValueError("No usable rows. " + (problems[0] if problems else ""))
    return rows, problems


# ------------------------------------------------------------------ session
class LabSession:
    """One uploaded file and the engine state after running it."""

    def __init__(self, models, playbooks: Dict[str, dict]):
        self.models = models
        self.playbooks = playbooks
        self.tok: Dict[str, str] = {}       # raw id -> token (memory only)
        self.raw: Dict[str, str] = {}       # token -> raw id (memory only)
        self.engine: Optional[Engine] = None
        self.tracker: Optional[CampaignTracker] = None
        self.result: Optional[dict] = None

    def token(self, raw: str) -> str:
        if not raw:
            return ""
        t = self.tok.get(raw)
        if t is None:
            t = pseudonymise("lab:" + raw)
            self.tok[raw] = t
            self.raw[t] = raw
        return t

    def name(self, tok: str) -> str:
        return self.raw.get(tok, tok)

    # -------------------------------------------------------------- accounts
    def _accounts(self, rows: List[dict], t0: float) -> Dict[str, AccountInfo]:
        acc: Dict[str, AccountInfo] = {}

        def opened(days: str) -> float:
            try:
                d = float(days)
            except (TypeError, ValueError):
                d = DEFAULT_ACCOUNT_AGE_DAYS
            return t0 - max(0.0, d) * DAY

        for r in rows:
            p = self.token(r["src"])
            if p not in acc:
                acc[p] = AccountInfo("individual", opened(r["payer_age_days"]), "", r["payer_age_band"], "",
                                     r["trusted"])
            elif r["trusted"]:
                acc[p].trusted_contact = 1
            if r["dst"]:
                q = self.token(r["dst"])
                if q not in acc:
                    acc[q] = AccountInfo("business" if r["payee_type"] == "business" else "individual",
                                         opened(r["payee_age_days"]))
                elif r["payee_type"] == "business":
                    acc[q].holder_type = "business"
                if r["payee_age_days"] and q in acc:
                    acc[q].opened_t = min(acc[q].opened_t, opened(r["payee_age_days"]))
        return acc

    # -------------------------------------------------------------- run
    def run(self, rows: List[dict], history_days: Optional[float] = None) -> dict:
        rows = sorted(rows, key=lambda r: r["ts"])
        t0 = rows[0]["ts"].timestamp()
        span_days = (rows[-1]["ts"].timestamp() - t0) / DAY
        if history_days is None:         # auto: long files start with a week of "normal life"
            history_days = 7.0 if span_days >= 14 else 0.0
        history_days = max(0.0, min(float(history_days), span_days))
        hist_until = t0 + history_days * DAY
        accounts = self._accounts(rows, t0)
        eng = Engine(accounts, self.models, t0, self.playbooks)
        # Payees first paid during the history days count as the customer's long-standing
        # payees (a bank knows its customers' regular payees). With 0 history days every
        # payee in the file is new to its payer.
        eng.warm_until = hist_until
        eng.mode = "full"
        tracker = CampaignTracker(eng, self.models)
        self.engine, self.tracker = eng, tracker

        out_rows: List[dict] = []
        case_seen: Dict[str, float] = {}
        next_link = t0 + LINK_EVERY_MIN * 60
        next_emerge = t0 + 3600
        for r in rows:
            t = r["ts"].timestamp()
            if t >= next_link:
                emerge = t >= next_emerge
                if emerge:
                    next_emerge = t + 3600
                tracker.run(t, with_emergence=emerge)
                for c in tracker.campaigns.values():
                    if c.visible and c.case_id not in case_seen:
                        case_seen[c.case_id] = t
                next_link = t + LINK_EVERY_MIN * 60
            ev = Event(r["event_id"], t, r["type"], self.token(r["src"]), self.token(r["dst"]), r["amount"],
                       r["rail"], normalise_remark(r["remark_raw"]), r["is_collect"], r["call"], r["screen"],
                       r["remote"], r["paste"], r["sess"], r["fri"])
            d = eng.process(ev)
            row = self._row(r, ev, d)
            row["history"] = t < hist_until
            out_rows.append(row)
        end_t = rows[-1]["ts"].timestamp()
        tracker.run(end_t + 1, with_emergence=True)
        for c in tracker.campaigns.values():
            if c.visible and c.case_id not in case_seen:
                case_seen[c.case_id] = end_t

        labelled = [x for x in out_rows if x["label"] is not None and x["is_payment"] and not x["history"]]
        res = {
            "rows": out_rows,
            "summary": {**self._summary(out_rows, rows), "history_days": round(history_days, 2),
                        "history_rows": sum(1 for x in out_rows if x["history"])},
            "metrics": self._metrics(labelled) if labelled else None,
            "cases": self._cases(case_seen),
            "privacy": self._privacy(rows[0]),
        }
        self.result = res
        return res

    def _row(self, r: dict, ev: Event, d) -> dict:
        is_pay = ev.type == "txn" and bool(ev.dst)
        level = d.level if d is not None else "R0"
        reasons = [x.get("text", "") for x in (d.reasons if d is not None else [])][:4]
        why_not = ""
        if d is not None and level == "R0" and r["label"] == 1:
            why_not = self._why_missed(d)
        return {
            "line": r["line"], "event_id": r["event_id"], "ts": r["ts"].strftime("%Y-%m-%d %H:%M"),
            "type": r["type"], "payer": r["src"], "payee": r["dst"], "amount": r["amount"],
            "label": r["label"], "is_payment": is_pay,
            "risk": round(d.risk, 3) if d is not None else 0.0,
            "journey_score": round(d.journey_score, 3) if d is not None else 0.0,
            "payee_score": round(d.collector_score, 3) if d is not None else 0.0,
            "level": level, "level_text": LEVEL_TEXT.get(level, level),
            "flagged": level != "R0", "stopped": level in ("R2", "R2T", "R3"),
            "scam_type": self._pb_title(d.playbook) if d is not None and d.playbook else "",
            "new_scam_type": bool(d is not None and d.playbook_novel),
            "campaign": d.campaign_id if d is not None else None,
            "reasons": reasons, "message": d.message if d is not None else "",
            "rules_engine_alert": bool(d is not None and d.baseline_alert),
            "why_missed": why_not,
        }

    def _why_missed(self, d) -> str:
        if d.risk >= config.R1_SCORE:
            return "risk was high, but a warning for this scam was shown recently (no repeat within the cool-down)"
        if not d.fresh:
            return f"payee was already known to this customer; risk {d.risk:.2f} (warning starts at {config.R1_SCORE:.2f})"
        if d.journey_score < 0.3 and d.collector_score < 0.3:
            return (f"looked like a normal first payment: risk {d.risk:.2f}, no earlier scam steps in this "
                    f"customer's history and the payee had no mule-like pattern yet")
        return f"risk {d.risk:.2f} stayed below the warning level {config.R1_SCORE:.2f}"

    def _pb_title(self, name: Optional[str]) -> str:
        if not name:
            return ""
        pb = self.playbooks.get(name) or {}
        return pb.get("title") or name.replace("_", " ")

    @staticmethod
    def _summary(out_rows: List[dict], rows: List[dict]) -> dict:
        pays = [x for x in out_rows if x["is_payment"]]
        lv: Dict[str, int] = {}
        for x in pays:
            lv[x["level"]] = lv.get(x["level"], 0) + 1
        return {"rows": len(out_rows), "payments": len(pays),
                "customers": len({r["src"] for r in rows}), "payees": len({r["dst"] for r in rows if r["dst"]}),
                "levels": lv, "flagged": sum(1 for x in pays if x["flagged"]),
                "rules_engine_alerts": sum(1 for x in pays if x["rules_engine_alert"]),
                "from": out_rows[0]["ts"], "to": out_rows[-1]["ts"]}

    @staticmethod
    def _metrics(lab: List[dict]) -> dict:
        def score(pred_key: str) -> dict:
            tp = sum(1 for x in lab if x["label"] == 1 and x[pred_key])
            fn = sum(1 for x in lab if x["label"] == 1 and not x[pred_key])
            fp = sum(1 for x in lab if x["label"] == 0 and x[pred_key])
            tn = sum(1 for x in lab if x["label"] == 0 and not x[pred_key])
            return {"tp": tp, "fn": fn, "fp": fp, "tn": tn,
                    "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                    "recall": round(tp / (tp + fn), 4) if tp + fn else None,
                    "false_positive_rate": round(fp / (fp + tn), 5) if fp + tn else None}

        vy = score("flagged")
        rules = score("rules_engine_alert")
        stop = score("stopped")
        scam_money = sum(x["amount"] for x in lab if x["label"] == 1)
        flagged_money = sum(x["amount"] for x in lab if x["label"] == 1 and x["flagged"])
        # customer level: was each scam victim warned/paused at least once?
        victims: Dict[str, bool] = {}
        genuine_disturbed: Dict[str, bool] = {}
        for x in lab:
            if x["label"] == 1:
                victims[x["payer"]] = victims.get(x["payer"], False) or x["flagged"]
        for x in lab:
            if x["label"] == 0 and x["payer"] not in victims:
                genuine_disturbed[x["payer"]] = genuine_disturbed.get(x["payer"], False) or x["flagged"]
        missed = [x for x in lab if x["label"] == 1 and not x["flagged"]]
        false_pos = [x for x in lab if x["label"] == 0 and x["flagged"]]
        keep = ("line", "event_id", "ts", "payer", "payee", "amount", "risk", "level_text", "reasons", "why_missed")
        return {
            "labelled_payments": len(lab), "scam_payments": vy["tp"] + vy["fn"],
            "genuine_payments": vy["fp"] + vy["tn"],
            "vyuh_flag": vy, "vyuh_pause": stop, "rules_engine": rules,
            "scam_money": round(scam_money, 2), "scam_money_flagged": round(flagged_money, 2),
            "victims": len(victims), "victims_flagged": sum(victims.values()),
            "genuine_customers": len(genuine_disturbed), "genuine_customers_disturbed": sum(genuine_disturbed.values()),
            "missed": [{k: x[k] for k in keep} for x in missed[:50]],
            "false_positives": [{k: x[k] for k in keep} for x in false_pos[:50]],
            "missed_total": len(missed), "false_positives_total": len(false_pos),
        }

    def _cases(self, case_seen: Dict[str, float]) -> List[dict]:
        tr = self.tracker
        out = []
        for cid in sorted(case_seen, key=lambda c: tr.campaigns[c].priority, reverse=True):
            c = tr.campaigns[cid]
            try:
                det = tr.detail(cid, display=self.name)
            except Exception:           # never let one case break the whole report
                det = {**c.summary(), "why": [], "members": [], "collectors": []}
            out.append({
                "case_id": cid, "title": ("New scam type - matches no known script" if c.emerging
                                          else self._pb_title(c.playbook) or "Linked scam journeys"),
                "kind": c.kind, "emerging": c.emerging, "confidence": round(c.confidence, 3),
                "victims": [self.name(m) for m in c.members],
                "receiving_accounts": [self.name(a) for a in c.collectors],
                "paid": round(c.paid, 2), "saveable": round(c.saveable, 2),
                "first_seen": datetime.fromtimestamp(case_seen[cid]).strftime("%Y-%m-%d %H:%M"),
                "why": det.get("why", [])[:6],
            })
        return out

    def _privacy(self, r: dict) -> dict:
        return {"example_raw": r["src"], "example_stored": self.token(r["src"]),
                "remark_raw": r["remark_raw"], "remark_stored": normalise_remark(r["remark_raw"]),
                "note": "Account ids are replaced by a keyed hash (HMAC-SHA256) before scoring. Remarks are cut "
                        "to one lower-case word. Raw ids stay in memory for this lab session only and are "
                        "never written to disk."}

    # -------------------------------------------------------------- one payment
    def score_one(self, p: dict) -> dict:
        """Dry-run score for one payment typed in by hand. Uses the history from the
        last uploaded file (if any). Changes nothing."""
        if self.engine is None:
            now = datetime.now().timestamp()
            self.engine = Engine({}, self.models, now - 30 * DAY, self.playbooks)
            self.engine.warm_until = now - 30 * DAY
            self.engine.mode = "full"
        eng = self.engine
        if p.get("ts"):
            t = p["ts"].timestamp()
        elif eng.state:
            t = eng.now + 60             # just after the last payment in the uploaded file
        else:
            t = datetime.now().timestamp()
        src, dst = self.token(p["payer"]), self.token(p["payee"])
        if dst not in eng.accounts:
            eng.accounts[dst] = AccountInfo("business" if p.get("payee_type") == "business" else "individual",
                                            t - float(p.get("payee_age_days") or DEFAULT_ACCOUNT_AGE_DAYS) * DAY)
        if src not in eng.accounts:
            eng.accounts[src] = AccountInfo("individual", t - DEFAULT_ACCOUNT_AGE_DAYS * DAY, "",
                                            p.get("payer_age_band", ""), "", int(p.get("trusted_contact") or 0))
        ev = Event("manual", t, "txn", src, dst, float(p["amount"]), "UPI", normalise_remark(p.get("remark", "")),
                   0, int(p.get("call_active") or 0), int(p.get("screen_share") or 0), int(p.get("remote_app") or 0),
                   int(p.get("paste_payee") or 0), int(p.get("session_sec") or 0), int(p.get("payee_fri") or 0))
        d = eng.evaluate(ev)
        known = src in eng.state and bool(eng.state[src].hist)
        reasons = [x.get("text", "") for x in d.reasons][:5]
        if not reasons and d.features is not None and self.models is not None and self.models.journey is not None:
            # below the warning level the live system shows nothing; here we still say what the model saw
            reasons = [x.get("text", "") for x in self.models.explain_journey(d.features)][:5]
        return {"risk": round(d.risk, 3), "level": d.level, "level_text": LEVEL_TEXT.get(d.level, d.level),
                "journey_score": round(d.journey_score, 3), "payee_score": round(d.collector_score, 3),
                "scam_type": self._pb_title(d.playbook), "reasons": reasons,
                "message": d.message, "message_hi": d.message_hi, "new_payee": d.fresh,
                "rules_engine_alert": d.baseline_alert, "latency_ms": round(d.latency_ms, 2),
                "history_used": known, "payer_stored_as": src,
                "thresholds": {"warn": config.R1_SCORE, "pause": config.R2_SCORE,
                               "pause_min_amount": config.R2_MIN_AMOUNT}}


# ------------------------------------------------------------------ scenario builder
SCENARIOS = {
    "task_scam": {"title": "Part-time task scam", "remarks": ["task", "task", "fee", "vip", "fee", "withdraw"],
                  "credit_first": True, "call": False, "screen": False, "paste": True},
    "digital_arrest": {"title": "Digital arrest (fake police / CBI on video call)",
                       "remarks": ["verification", "rbi", "clearance", "verification", "rbi", "clearance"],
                       "credit_first": False, "call": True, "screen": True, "paste": True},
    "investment": {"title": "Fake stock-tip / investment group", "remarks": ["vip", "invest", "vip", "tax", "fee", "tax"],
                   "credit_first": True, "call": False, "screen": False, "paste": True},
    "kyc_remote": {"title": "Fake KYC with a remote-access app", "remarks": ["kyc", "kyc", "update", "kyc", "update", "kyc"],
                   "credit_first": False, "call": True, "screen": False, "remote": True, "paste": True},
    "genuine_new_payee": {"title": "GENUINE: customer pays a new landlord (no scam)", "remarks": ["rent"],
                          "genuine": True},
}


def build_scenario(p: dict) -> Tuple[List[dict], float]:
    """Turn a few settings into a realistic file: 10 days of normal life for each
    customer, then the scam (or a genuine new payment). Returns (rows, history_days)."""
    import random
    kind = p.get("kind", "task_scam")
    sc = SCENARIOS.get(kind)
    if sc is None:
        raise ValueError("unknown scenario")
    victims = max(1, min(int(p.get("victims", 6)), 30))
    steps = max(1, min(int(p.get("steps", 4)), 6))
    first_amt = max(100.0, min(float(p.get("first_amount", 2000)), 5_000_000))
    growth = max(1.0, min(float(p.get("growth", 2.0)), 5.0))
    rotate = bool(p.get("new_account_each_payment", False))
    on_call = p.get("on_call")
    on_call = sc.get("call", False) if on_call is None else bool(on_call)
    screen = p.get("screen_share")
    screen = sc.get("screen", False) if screen is None else bool(screen)
    hide_words = bool(p.get("hide_words", False))
    senior = bool(p.get("senior_with_trusted_contact", False))
    rng = random.Random(f"{kind}-{victims}-{steps}-{first_amt}-{growth}-{rotate}-{on_call}-{screen}-{hide_words}-{senior}")
    start = datetime(2026, 10, 1, 9, 0)
    hist_days = 10.0
    attack_t = start + timedelta(days=hist_days, hours=1)
    rows: List[dict] = []
    n = [0]

    def add(ts, src, dst, amount, remark="", label=None, typ="txn", call=0, scr=0, remote=0, paste=0,
            payee_type="individual", payee_age="", age_band="36-59", trusted=0, sess=60):
        n[0] += 1
        rows.append({"event_id": f"S{n[0]:05d}", "ts": ts, "type": typ, "src": src, "dst": dst,
                     "amount": round(amount, 2), "rail": "UPI", "remark_raw": remark, "is_collect": 0,
                     "call": call, "screen": scr, "remote": remote, "paste": paste, "sess": sess, "fri": 0,
                     "label": label, "payee_type": payee_type, "payee_age_days": str(payee_age),
                     "payer_age_days": "", "payer_age_band": age_band, "trusted": trusted, "line": n[0] + 1})

    mules = [f"mule_{chr(97 + i)}{rng.randint(100, 999)}" for i in range(3)]
    cashout = f"cashout_{rng.randint(1000, 9999)}"
    shops = ["grocery_store", "electricity_board", "mobile_recharge", "pharmacy", "petrol_pump"]
    for v in range(victims):
        cust = f"customer_{v + 1:02d}"
        band = "60+" if (senior and v == 0) else rng.choice(["18-35", "36-59", "36-59", "60+"])
        trusted = 1 if (senior and v == 0) else 0
        friend = f"friend_of_{v + 1:02d}"
        # 10 days of normal life: shops, a friend, a utility bill
        for d in range(int(hist_days)):
            for _ in range(rng.randint(0, 2)):
                t = start + timedelta(days=d, hours=rng.uniform(8, 21))
                add(t, cust, rng.choice(shops), rng.uniform(150, 2500), "", 0, payee_type="business",
                    payee_age=rng.randint(400, 3000), age_band=band, trusted=trusted)
            if rng.random() < 0.3:
                t = start + timedelta(days=d, hours=rng.uniform(9, 22))
                add(t, cust, friend, rng.uniform(200, 3000), "", 0, payee_age=900, age_band=band, trusted=trusted)
        t = attack_t + timedelta(minutes=v * rng.uniform(25, 70))
        if sc.get("genuine"):
            landlord = f"new_landlord_{v + 1:02d}"
            add(t, cust, landlord, first_amt, "rent", 0, payee_age=rng.randint(700, 4000), age_band=band,
                trusted=trusted, sess=90)
            continue
        if sc.get("credit_first"):
            add(t, mules[2], cust, rng.uniform(150, 500), "" if hide_words else "reward", None,
                payee_age=200, age_band=band)
            t += timedelta(minutes=rng.uniform(20, 90))
        amt = first_amt
        for k in range(steps):
            mule = (f"mule_new_{v + 1:02d}_{k + 1}" if rotate else mules[k % 2])
            remark = "" if hide_words else sc["remarks"][k % len(sc["remarks"])]
            a = amt * (rng.uniform(0.85, 1.25) if rotate else 1.0)
            add(t, cust, mule, a, remark, 1, call=int(on_call), scr=int(screen), remote=int(sc.get("remote", 0)),
                paste=int(sc.get("paste", 0)), payee_age=rng.randint(2, 20), age_band=band, trusted=trusted,
                sess=rng.randint(20, 60))
            # the mule passes the money on within minutes (cash-out pattern)
            add(t + timedelta(minutes=rng.uniform(3, 25)), mule, cashout, a * 0.97, "", None, payee_age=30)
            t += timedelta(minutes=rng.uniform(30, 240) if not rotate else rng.uniform(60, 600))
            amt *= growth
    rows.sort(key=lambda r: r["ts"])
    # everything before the attack is "normal life" (history), not scored for accuracy
    hist = (attack_t - rows[0]["ts"]).total_seconds() / DAY - 1e-6
    return rows, max(0.0, hist)


def run_scenario(p: dict, models, playbooks) -> dict:
    rows, hist = build_scenario(p)
    with _LOCK:
        s = LabSession(models, playbooks)
        res = s.run(rows, hist)
        _SESSION["current"] = s
    res["problems"] = []
    res["scenario"] = {"title": SCENARIOS[p.get("kind", "task_scam")]["title"], **{k: p.get(k) for k in p}}
    res["csv"] = rows_to_csv(rows)
    return res


def rows_to_csv(rows: List[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TEMPLATE_HEADER)
    for r in rows:
        lab = "" if r["label"] is None else ("scam" if r["label"] == 1 else "genuine")
        w.writerow([r["event_id"], r["ts"].strftime("%Y-%m-%d %H:%M:%S"), r["src"], r["dst"], r["amount"], r["type"],
                    r["remark_raw"], r["call"], r["screen"], r["remote"], r["paste"], r["sess"], r["fri"],
                    r["is_collect"], r["payee_type"], r["payee_age_days"], r["payer_age_band"], r["trusted"], lab])
    return buf.getvalue()


_LOCK = threading.Lock()
_SESSION: Dict[str, LabSession] = {}


def run_file(text: str, models, playbooks, history_days: Optional[float] = None) -> dict:
    rows, problems = parse_csv(text)
    with _LOCK:
        s = LabSession(models, playbooks)
        res = s.run(rows, history_days)
        _SESSION["current"] = s
    res["problems"] = problems
    return res


def score_payment(p: dict, models, playbooks) -> dict:
    with _LOCK:
        s = _SESSION.get("current")
        if s is None:
            s = LabSession(models, playbooks)
            _SESSION["current"] = s
        return s.score_one(p)


def clear() -> None:
    with _LOCK:
        _SESSION.clear()
