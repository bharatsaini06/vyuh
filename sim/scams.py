"""Scam campaign generator: turns a playbook YAML into concrete events.

A *campaign* = one criminal operation running one playbook on many victims,
cashing out through a pool of collector (mule) accounts that forward money to
layer accounts and finally to cash-out points.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

from core import config
from sim.world import Account, World

ADVERSARY_MODES = {"rotate", "randomize", "slow", "split", "decoy", "coached", "compromised"}


def load_playbooks(directory: Path = config.PLAYBOOK_DIR) -> Dict[str, dict]:
    books = {}
    for f in sorted(Path(directory).glob("*.yaml")):
        pb = yaml.safe_load(f.read_text(encoding="utf-8"))
        books[pb["name"]] = pb
    return books


def _u(r: random.Random, rng_pair) -> float:
    lo, hi = rng_pair
    return r.uniform(lo, hi)


def _ui(r: random.Random, rng_pair) -> int:
    lo, hi = rng_pair
    return r.randint(int(lo), int(hi))


@dataclass
class CampaignResult:
    campaign_id: str
    playbook: str
    victims: List[str] = field(default_factory=list)
    collectors: List[str] = field(default_factory=list)
    layers: List[str] = field(default_factory=list)
    events: List[tuple] = field(default_factory=list)


class ScamFactory:
    def __init__(self, world: World, playbooks: Dict[str, dict], rng: random.Random):
        self.world = world
        self.playbooks = playbooks
        self.r = rng
        self.used: Set[str] = set()       # customers already used as victims/mules
        self.truth: List[tuple] = []      # (account, role, campaign_id, playbook)
        self.collector_first_use: Dict[str, datetime] = {}
        # Delayed outside signals (see generate.py): victims' 1930 complaints and the
        # Suspect-Registry style flags they cause on the accounts they paid.
        self.rc = random.Random(rng.random())
        self.complaints: List[tuple] = []  # (ts, victim, "acc1|acc2", campaign_id, playbook)
        self.registry: List[tuple] = []    # (listed_ts, account, source)

    # ------------------------------------------------------------ actors
    def _pick_victims(self, pb: dict, n: int) -> List[str]:
        r = self.r
        targets = pb["targets"]
        personas = list(targets)
        weights = [targets[p] for p in personas]
        by_persona: Dict[str, List[str]] = {}
        for cid, c in self.world.customers.items():
            if cid not in self.used:
                by_persona.setdefault(c.persona, []).append(cid)
        victims = []
        for _ in range(n):
            p = r.choices(personas, weights)[0]
            pool = by_persona.get(p) or []
            if not pool:
                continue
            cid = pool.pop(r.randrange(len(pool)))
            victims.append(cid)
            self.used.add(cid)
        return victims

    def _make_collector(self, start: datetime, dormant_share: float, compromised: bool,
                        kind: str = "individual") -> str:
        r = self.r
        w = self.world
        if kind == "business":
            # a freshly onboarded "merchant" whose QR code the scammers circulate
            aid = w.new_account_id("MRCQ")
            w.add_account(Account(aid, "business", start - timedelta(days=r.randint(5, 60)), "small_merchant",
                                  home_state=r.choice(["Jharkhand", "Bihar", "West Bengal", "Haryana"])))
            return aid
        u = r.random()
        if compromised and u < 0.5:
            mid = r.choice(w.merchant_ids)          # aged, clean-looking business account
            return mid
        if u < dormant_share:
            candidates = [c for c, cu in w.customers.items()
                          if c not in self.used and cu.rate < 0.8 and cu.persona != "merchant"]
            if candidates:
                cid = r.choice(candidates)
                self.used.add(cid)
                return cid
        aid = w.new_account_id("ACCT")
        opened = start - timedelta(days=r.randint(3, 40))
        w.add_account(Account(aid, "individual", opened, age_band=r.choice(["18-25", "26-35"]),
                              home_state=r.choice(["Jharkhand", "Rajasthan", "West Bengal", "Haryana"])))
        return aid

    def _make_layer(self, start: datetime) -> str:
        w = self.world
        aid = w.new_account_id("ACCT")
        w.add_account(Account(aid, "individual", start - timedelta(days=self.r.randint(10, 120)),
                              age_band="26-35", home_state="Delhi"))
        return aid

    # ------------------------------------------------------------ main
    def make_campaign(self, pb_name: str, start: datetime, n_victims: Optional[int] = None,
                      campaign_id: str = "", adversary: Optional[Set[str]] = None,
                      arrival_scale: float = 1.0, victims: Optional[List[str]] = None) -> CampaignResult:
        r = self.r
        pb = self.playbooks[pb_name]
        adv = set(adversary or ())
        res = CampaignResult(campaign_id, pb_name)
        n = n_victims or _ui(r, pb["campaigns"]["victims"])
        res.victims = victims if victims is not None else self._pick_victims(pb, n)
        for v in res.victims:
            self.used.add(v)
        co = dict(pb["cash_out"])
        n_coll = co["collectors"]
        rotate_every = co["rotate_every"]
        if "rotate" in adv:
            n_coll, rotate_every = max(len(res.victims) * 2, 4), 1
        res.collectors = [self._make_collector(start, co["dormant_share"], "compromised" in adv,
                                               co.get("collector_type", "individual"))
                          for _ in range(n_coll)]
        res.layers = [self._make_layer(start) for _ in range(3)]
        cashouts = r.sample(self.world.cashout_ids, k=2)
        for a in res.collectors:
            self.truth.append((a, "collector", campaign_id, pb_name))
        for a in res.layers:
            self.truth.append((a, "layer", campaign_id, pb_name))
        for v in res.victims:
            self.truth.append((v, "victim", campaign_id, pb_name))

        t = start
        for idx, victim in enumerate(res.victims):
            gap = _u(r, pb["campaigns"]["arrival_hours"]) * arrival_scale
            t = t + timedelta(hours=r.expovariate(1.0 / max(gap, 0.05)))
            coll_idx = (idx // rotate_every) % len(res.collectors)
            if "rotate" in adv:
                coll_idx = (2 * idx) % len(res.collectors)
            self._victim_journey(pb, victim, t, res, coll_idx, cashouts, adv)
        return res

    # ------------------------------------------------------------ victim journey
    def _victim_journey(self, pb: dict, victim: str, t0: datetime, res: CampaignResult,
                        coll_idx: int, cashouts: List[str], adv: Set[str]) -> None:
        r = self.r
        w = self.world
        cust = w.customers[victim]
        remaining = cust.savings
        paid_total = 0.0
        t = t0
        sig = dict(pb["signals"])
        if "coached" in adv:
            for k in ("call_active", "screen_share", "remote_app"):
                sig[k] *= 0.2
        slow = 4.0 if "slow" in adv else 1.0
        rem_cfg = pb.get("remarks", {"prob": 0, "words": []})
        rem_prob = 0.0 if "decoy" in adv else rem_cfg["prob"]
        cid, pbn = res.campaign_id, res.playbook
        step_counter = [0]
        paid_to: Dict[str, None] = {}
        last_pay = [t0]

        def collector_for_step() -> str:
            if "split" in adv or "rotate" in adv:
                c = res.collectors[(coll_idx + step_counter[0]) % len(res.collectors)]
            else:
                c = res.collectors[coll_idx]
            step_counter[0] += 1
            return c

        def gap(h_range) -> timedelta:
            g = _u(r, h_range) * slow
            if "randomize" in adv:
                g *= r.uniform(0.4, 2.5)
            return timedelta(hours=g)

        def session() -> tuple:
            lo, hi = sig["session_sec"]
            return (int(r.random() < sig["call_active"]), int(r.random() < sig["screen_share"]),
                    int(r.random() < sig["remote_app"]), int(r.random() < sig["paste_payee"]),
                    int(r.uniform(lo, hi)))

        def remark() -> str:
            return r.choice(rem_cfg["words"]) if rem_cfg["words"] and r.random() < rem_prob else ""

        def pay(ts: datetime, amount: float, stage: str) -> None:
            nonlocal remaining, paid_total
            parts = r.randint(2, 3) if "split" in adv and amount > 3000 else 1
            for p in range(parts):
                coll = collector_for_step()
                part_amt = amount / parts
                pts = ts + timedelta(minutes=7 * p)
                first = self.collector_first_use.setdefault(coll, pts)
                fri = 2 if (pts - first).days >= 5 and r.random() < 0.5 else 0
                w.txn(pts, victim, coll, part_amt, remark(), session=session(),
                      is_collect=int(r.random() < sig.get("collect", 0)), fri=fri,
                      label=(cid, pbn, stage, "victim_payment"))
                self._forward(coll, pts, part_amt, res, cashouts)
                paid_to[coll] = None
                last_pay[0] = max(last_pay[0], pts)
            remaining -= amount
            paid_total += amount
            if "decoy" in adv and r.random() < 0.5 and cust.merchants:
                w.txn(ts + timedelta(minutes=r.randint(20, 90)), victim, r.choice(cust.merchants),
                      r.uniform(100, 800), "food")

        for step in pb["steps"]:
            kind = step["kind"]
            if kind == "trust_credit":
                for _ in range(_ui(r, step["count"])):
                    t += gap(step["gap_hours"])
                    src = res.collectors[(coll_idx + 1) % len(res.collectors)]
                    w.txn(t, src, victim, _u(r, step["amount"]), "reward",
                          session=(0, 0, 0, 0, 20), label=(cid, pbn, "S3", "trust_credit"))
            elif kind == "funding":
                if r.random() < step["prob"]:
                    t += gap(step["gap_hours"])
                    if step["type"] == "loan":
                        amt = round(_u(r, step["amount"]), -3)
                        w.account_event(t, "loan", victim, amt, label=(cid, pbn, "S5", "funding"))
                        remaining += amt
                    else:
                        amt = remaining * _u(r, step["fraction"])
                        w.account_event(t, "fd_break", victim, amt, label=(cid, pbn, "S5", "funding"))
            elif kind == "ladder":
                amount = _u(r, step["start"])
                lo, hi = step["ratio"]
                if "randomize" in adv:
                    lo, hi = 1.1, 4.0
                for i in range(_ui(r, step["steps"])):
                    t += gap(step["gap_hours"])
                    amt = min(amount * r.uniform(0.9, 1.1), remaining)
                    if amt < 300:
                        break
                    pay(t, amt, "S5")
                    if i < step.get("refund_steps", 0) and r.random() < step.get("refund_prob", 0):
                        t += timedelta(minutes=r.randint(5, 40))
                        back = amt * step.get("refund_multiplier", 1.0)
                        src = res.collectors[(coll_idx + 1) % len(res.collectors)]
                        w.txn(t, src, victim, back, "payout", session=(0, 0, 0, 0, 20),
                              label=(cid, pbn, "S3", "trust_credit"))
                        remaining += back
                    amount *= r.uniform(lo, hi)
            elif kind == "lump":
                for _ in range(_ui(r, step["count"])):
                    t += gap(step["gap_hours"])
                    amt = remaining * _u(r, step["fraction"])
                    if amt < 1000:
                        break
                    pay(t, amt, "S5")
            elif kind == "burst":
                for _ in range(_ui(r, step["count"])):
                    t += gap(step["gap_hours"])
                    if "amount" in step:
                        amt = min(_u(r, step["amount"]), remaining)
                    else:
                        amt = remaining * _u(r, step["fraction"])
                    if amt < 300:
                        break
                    pay(t, amt, "S5")
            elif kind == "fee_to_withdraw":
                if r.random() < step["prob"] and paid_total > 0:
                    t += gap(step["gap_hours"])
                    amt = min(paid_total * _u(r, step["amount_ratio"]), remaining)
                    if amt >= 500:
                        pay(t, amt, "S7")

        self._complaint(victim, paid_to, last_pay[0], cid, pbn)

    def _complaint(self, victim: str, paid_to: Dict[str, None], last_pay: datetime, cid: str, pbn: str) -> None:
        """Some victims complain on 1930 / NCRP hours to days after their last payment;
        the accounts they paid then appear in the Suspect-Registry style feed."""
        rc = self.rc
        if not paid_to or rc.random() >= config.COMPLAINT_SHARE:
            return
        delay_h = min(max(rc.lognormvariate(math.log(20), 1.0), 0.5), 240)
        ts = last_pay + timedelta(hours=delay_h)
        self.complaints.append((ts, victim, "|".join(paid_to), cid, pbn))
        lo, hi = config.REGISTRY_DELAY_HOURS
        for acc in paid_to:
            self.registry.append((ts + timedelta(hours=rc.uniform(lo, hi)), acc, "1930 complaint"))

    def _forward(self, coll: str, ts: datetime, amount: float, res: CampaignResult,
                 cashouts: List[str]) -> None:
        """Collector forwards the money within minutes, then the layer cashes out."""
        r = self.r
        w = self.world
        lo, hi = self.playbooks[res.playbook]["cash_out"]["forward_minutes"]
        t1 = ts + timedelta(minutes=r.uniform(lo, hi))
        layer = r.choice(res.layers)
        w.txn(t1, coll, layer, amount * r.uniform(0.95, 0.99), "", session=(0, 0, 0, 1, 15),
              label=(res.campaign_id, res.playbook, "S6", "mule_forward"))
        t2 = t1 + timedelta(minutes=r.uniform(5, 90))
        w.txn(t2, layer, r.choice(cashouts), amount * r.uniform(0.9, 0.95), "", session=(0, 0, 0, 1, 15),
              label=(res.campaign_id, res.playbook, "S6", "mule_forward"))


def schedule_campaigns(factory: ScamFactory, start: datetime, days: int,
                       adversary: Optional[Set[str]] = None, adversary_from_day: int = 31) -> List[CampaignResult]:
    """Spread campaigns of every playbook across the simulation window."""
    r = factory.r
    results = []
    n = 0
    for name, pb in factory.playbooks.items():
        first_day = pb.get("start_day", 3)
        months = max(1, round((days - first_day) / 30)) if not pb.get("known", True) else max(1, round(days / 30))
        count = pb["campaigns"]["per_month"] * months
        for _ in range(count):
            day = r.randint(first_day, max(first_day, days - 6))
            st = start + timedelta(days=day - 1, hours=r.uniform(8, 20))
            n += 1
            adv = adversary if (adversary and day >= adversary_from_day) else None
            results.append(factory.make_campaign(name, st, campaign_id=f"GT-{n:03d}", adversary=adv))
    return results
