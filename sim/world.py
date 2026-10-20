"""VyuhSim world: synthetic customers, merchants and normal payment behaviour.

Everything here is synthetic. No real person, bank or account is represented.
The world deliberately contains legitimate behaviour that LOOKS like scams
(fundraisers, house moves, family emergencies, chit funds, new sellers) so
that false positives can be measured honestly.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

STATES = ["Rajasthan", "Delhi", "Maharashtra", "Karnataka", "Tamil Nadu", "Uttar Pradesh",
          "West Bengal", "Gujarat", "Telangana", "Kerala"]

PERSONAS = {
    #            share  tx/day  monthly outflow range (INR)   age bands
    "student":  (0.20, 1.2, (3000, 12000), ["18-25"]),
    "salaried": (0.40, 1.6, (15000, 90000), ["26-35", "36-45", "46-60"]),
    "senior":   (0.12, 0.5, (8000, 40000), ["60+"]),
    "merchant": (0.08, 0.8, (20000, 150000), ["26-35", "36-45", "46-60"]),
    "gig":      (0.20, 1.5, (8000, 30000), ["18-25", "26-35"]),
}
MERCHANT_CATEGORIES = ["grocery", "food", "pharmacy", "fuel", "electronics", "clothing",
                       "education", "hospital", "ecommerce", "securities", "travel"]
NORMAL_REMARKS = ["", "", "", "food", "rent", "groceries", "bill", "fees", "gift", "trip", "loan", "share"]


@dataclass
class Account:
    account_id: str
    holder_type: str               # individual | business
    opened_at: datetime
    merchant_category: str = ""
    age_band: str = ""
    home_state: str = ""
    trusted_contact: int = 0       # customer opted in to trusted-contact approval (a setting, not a model feature)


@dataclass
class Customer:
    account_id: str
    persona: str
    state: str
    monthly_outflow: float
    savings: float
    rate: float
    friends: List[str] = field(default_factory=list)
    merchants: List[str] = field(default_factory=list)
    billers: List[str] = field(default_factory=list)
    landlord: Optional[str] = None
    rent: float = 0.0
    income_source: Optional[str] = None
    income: float = 0.0
    suppliers: List[str] = field(default_factory=list)


class World:
    """Holds every synthetic account and generates normal activity."""

    def __init__(self, n_customers: int, start: datetime, days: int, seed: int):
        self.rng = random.Random(seed)
        self.start = start
        self.days = days
        self.accounts: Dict[str, Account] = {}
        self.customers: Dict[str, Customer] = {}
        self.persona_of: Dict[str, str] = {}
        self.merchant_ids: List[str] = []
        self.cashout_ids: List[str] = []
        self.biller_ids: List[str] = []
        self.employer_ids: List[str] = []
        self.platform_ids: List[str] = []
        self.hospital_ids: List[str] = []
        self.events: List[tuple] = []     # see sim.events.ROW for the column order
        self.mule_counter = 0
        self.remote_users: set = set()
        self._build(n_customers)
        # ~3% of customers use remote-access tools for work or family support
        self.remote_users = set(self.rng.sample(list(self.customers), k=max(1, n_customers * 3 // 100)))
        # Some senior customers opted in to trusted-contact approval (separate RNG so
        # the rest of the world is unchanged by this setting).
        r2 = random.Random(seed + 7)
        from core.config import TRUSTED_OPTIN_SHARE
        for cid in self.customers:
            acc = self.accounts[cid]
            if acc.age_band == "60+" and r2.random() < TRUSTED_OPTIN_SHARE:
                acc.trusted_contact = 1

    # ------------------------------------------------------------ helpers
    def _opened(self, min_days: int, max_days: int) -> datetime:
        return self.start - timedelta(days=self.rng.randint(min_days, max_days))

    def new_account_id(self, prefix: str = "ACCT") -> str:
        self.mule_counter += 1
        return f"{prefix}{self.mule_counter:06d}"

    def add_account(self, acc: Account) -> None:
        self.accounts[acc.account_id] = acc

    # ------------------------------------------------------------ build
    def _build(self, n: int) -> None:
        r = self.rng
        n_merch = max(40, n // 20)
        for i in range(1, n_merch + 1):
            mid = f"MRCH{i:05d}"
            cat = r.choice(MERCHANT_CATEGORIES)
            self.add_account(Account(mid, "business", self._opened(200, 3650), cat,
                                     home_state=r.choice(STATES)))
            self.merchant_ids.append(mid)
            if cat == "hospital":
                self.hospital_ids.append(mid)
        if not self.hospital_ids:
            self.hospital_ids.append(self.merchant_ids[0])
        # Payment aggregators / crypto-exchange style accounts: used by normal
        # people AND as scam cash-out points.
        for i in range(1, 7):
            cid = f"PAGG{i:03d}"
            self.add_account(Account(cid, "business", self._opened(400, 2000), "payment_aggregator"))
            self.cashout_ids.append(cid)
        for i in range(1, 13):
            bid = f"BILL{i:03d}"
            self.add_account(Account(bid, "business", self._opened(1000, 5000), "utility"))
            self.biller_ids.append(bid)
        for i in range(1, 41):
            eid = f"EMPL{i:03d}"
            self.add_account(Account(eid, "business", self._opened(500, 5000), "employer"))
            self.employer_ids.append(eid)
        for i in range(1, 6):
            pid = f"PLAT{i:02d}"
            self.add_account(Account(pid, "business", self._opened(500, 3000), "gig_platform"))
            self.platform_ids.append(pid)
        self.add_account(Account("PENS0001", "business", self._opened(3000, 6000), "pension"))

        names = list(PERSONAS)
        weights = [PERSONAS[p][0] for p in names]
        for i in range(1, n + 1):
            cid = f"CUST{i:06d}"
            persona = r.choices(names, weights)[0]
            share, rate, (lo, hi), bands = PERSONAS[persona]
            state = r.choice(STATES)
            outflow = math.exp(r.uniform(math.log(lo), math.log(hi)))
            savings_mult = r.uniform(4, 15) if persona == "senior" else r.uniform(2, 8)
            opened = self._opened(1, 60) if r.random() < 0.10 else self._opened(60, 3650)
            self.add_account(Account(cid, "individual", opened,
                                     age_band=r.choice(bands), home_state=state))
            self.customers[cid] = Customer(cid, persona, state, outflow, outflow * savings_mult,
                                           rate * r.uniform(0.6, 1.4))
            self.persona_of[cid] = persona

        ids = list(self.customers)
        shop_people = [c for c in ids if self.persona_of[c] == "merchant"]
        payable_shops = self.merchant_ids + shop_people
        for c in self.customers.values():
            c.friends = r.sample(ids, k=r.randint(4, 14))
            c.friends = [f for f in c.friends if f != c.account_id]
            c.merchants = r.sample(payable_shops, k=min(len(payable_shops), r.randint(5, 14)))
            c.billers = r.sample(self.biller_ids, k=r.randint(2, 4))
            if c.persona in ("salaried", "student") and r.random() < (0.7 if c.persona == "salaried" else 0.4):
                c.landlord = r.choice(ids)
                c.rent = round(r.uniform(6000, 25000), -2) if c.persona == "salaried" else round(r.uniform(3000, 9000), -2)
            if c.persona == "salaried":
                c.income_source = r.choice(self.employer_ids)
                c.income = round(c.monthly_outflow * r.uniform(1.1, 1.6), -2)
            elif c.persona == "senior":
                c.income_source = "PENS0001"
                c.income = round(c.monthly_outflow * r.uniform(1.0, 1.3), -2)
            elif c.persona == "gig":
                c.income_source = r.choice(self.platform_ids)
                c.income = round(c.monthly_outflow / 4 * r.uniform(1.0, 1.3), -2)
            elif c.persona == "merchant":
                c.suppliers = r.sample(self.merchant_ids, k=3)

    # ------------------------------------------------------------ event helpers
    def _session(self, new_payee: bool, family: bool = False, payer: Optional[str] = None) -> tuple:
        r = self.rng
        # Legitimate look-alikes: people pay a plumber/seller while talking to them,
        # some customers genuinely use remote-support or screen-sharing tools.
        call = 1 if r.random() < (0.15 if family else 0.08 if new_payee else 0.03) else 0
        screen = 1 if r.random() < 0.004 else 0
        remote = 1 if (payer in self.remote_users or r.random() < 0.008) else 0
        paste = 1 if r.random() < (0.12 if new_payee else 0.01) else 0
        sess = int(min(1800, max(8, r.lognormvariate(math.log(35), 0.6))))
        return call, screen, remote, paste, sess

    def _ts(self, day: int, hour: Optional[float] = None) -> datetime:
        r = self.rng
        if hour is None:
            # Mostly 8:00-23:00, small night share.
            hour = r.uniform(0, 6) if r.random() < 0.04 else r.triangular(8, 23, 19)
        return self.start + timedelta(days=day, hours=hour)

    def txn(self, ts: datetime, src: str, dst: str, amount: float, remark: str = "",
            new_payee: bool = False, family: bool = False, session: Optional[tuple] = None,
            is_collect: int = 0, fri: int = 0, label: tuple = ("", "", "", "")) -> None:
        """One payment. `is_collect` = the payment was started from a QR code or a
        request (not typed by the customer): normal at shops, rare between people."""
        amount = round(max(1.0, amount), 2)
        rail = "UPI" if amount <= 100000 else self.rng.choice(["IMPS", "NEFT"])
        call, screen, remote, paste, sess = session or self._session(new_payee, family, src)
        self.events.append((ts, "txn", src, dst, amount, rail, remark, is_collect,
                            call, screen, remote, paste, sess, fri) + label)

    def account_event(self, ts: datetime, kind: str, acct: str, amount: float,
                      label: tuple = ("", "", "", "")) -> None:
        self.events.append((ts, kind, acct, "", round(amount, 2), "", "", 0, 0, 0, 0, 0, 0, 0) + label)

    # ------------------------------------------------------------ normal activity
    def generate_normal(self) -> None:
        r = self.rng
        ids = list(self.customers)
        festival = set(range(20, 25))   # a festive week with more shopping
        for day in range(self.days):
            date = self.start + timedelta(days=day)
            dom = date.day
            for c in self.customers.values():
                # Monthly / weekly income
                if c.income_source:
                    if c.persona == "gig":
                        if date.weekday() == 0:
                            self.txn(self._ts(day, 11), c.income_source, c.account_id, c.income * r.uniform(0.7, 1.3))
                    elif dom == 1:
                        self.txn(self._ts(day, 10), c.income_source, c.account_id, c.income)
                # rent day 2-5, fixed per customer (int, not hash(): Python randomises str hashes per run)
                if c.landlord and dom == 2 + (int(c.account_id[4:]) % 4):
                    self.txn(self._ts(day), c.account_id, c.landlord, c.rent, "rent")
                rate = c.rate * (1.6 if day in festival else 1.0) * (1.15 if date.weekday() >= 5 else 1.0)
                k = _poisson(r, rate)
                for _ in range(k):
                    self._one_normal_payment(c, day, ids, day in festival)
                # legit small credits from strangers (buyer paying, split bill, cashback)
                if r.random() < 0.02:
                    self.txn(self._ts(day), r.choice(ids), c.account_id, r.uniform(50, 2000),
                             r.choice(["", "item", "split", "return"]), new_payee=True)
        self._edge_cases(ids)

    def _one_normal_payment(self, c: Customer, day: int, ids: List[str], festive: bool) -> None:
        r = self.rng
        ts = self._ts(day)
        scale = c.monthly_outflow / 30
        u = r.random()
        if c.persona == "merchant" and u < 0.35:
            if r.random() < 0.08:
                dst = r.choice(self.merchant_ids)
                self.txn(ts, c.account_id, dst, r.uniform(2000, 20000), "", new_payee=True)
            else:
                self.txn(ts, c.account_id, r.choice(c.suppliers), r.uniform(2000, 20000))
            return
        if u < 0.55:                                    # shop / merchant
            amt = r.lognormvariate(math.log(max(60, scale * 0.6)), 0.8) * (1.8 if festive else 1)
            qr = int(r.random() < 0.5)                  # about half of shop payments are QR scans
            if r.random() < 0.10:
                self.txn(ts, c.account_id, r.choice(self.merchant_ids), amt, "", new_payee=True, is_collect=qr)
            else:
                self.txn(ts, c.account_id, r.choice(c.merchants), amt, r.choice(["", "food", "groceries"]),
                         is_collect=qr)
        elif u < 0.80:                                  # friends / family
            if c.friends:
                family = r.random() < 0.3
                self.txn(ts, c.account_id, r.choice(c.friends), r.uniform(50, 3000),
                         r.choice(NORMAL_REMARKS), family=family)
        elif u < 0.88:                                  # bills
            self.txn(ts, c.account_id, r.choice(c.billers), r.uniform(200, 3000), "bill")
        elif u < 0.93 and c.persona == "student":       # college fees (the word "fee" is not a scam!)
            self.txn(ts, c.account_id, r.choice(self.merchant_ids), r.uniform(5000, 40000), "fees")
        elif u < 0.97:                                  # legit brand-new individual payee
            dst = r.choice(ids)
            self.txn(ts, c.account_id, dst, r.uniform(200, min(15000, scale * 8 + 500)),
                     r.choice(["", "item", "service", "tuition"]), new_payee=True,
                     is_collect=int(r.random() < 0.15))   # scanned a seller's personal QR
        else:                                           # aggregator / wallet top-up / crypto
            self.txn(ts, c.account_id, r.choice(self.cashout_ids), r.uniform(500, 8000), "")

    # ------------------------------------------------------------ false-positive traps
    def _edge_cases(self, ids: List[str]) -> None:
        r = self.rng
        D = self.days
        salaried = [c for c in self.customers.values() if c.persona == "salaried"]
        # 1. Medical fundraisers: many unrelated donors, then a hospital payment.
        for _ in range(max(2, len(ids) // 1500)):
            host = r.choice(ids)
            day = r.randint(3, D - 5)
            donors = r.sample(ids, k=r.randint(60, 180))
            total = 0.0
            for d in donors:
                if d == host:
                    continue
                amt = r.choice([101, 251, 501, 1001, 2001, 500, 1000])
                total += amt
                self.txn(self._ts(day + r.randint(0, 2)), d, host, amt, "donation", new_payee=True)
            self.txn(self._ts(day + 3, 12), host, r.choice(self.hospital_ids), total * 0.95, "hospital")
        # 2. House moves: deposit + broker + movers to brand-new payees within days.
        for c in r.sample(salaried, k=max(3, len(salaried) // 60)):
            day = r.randint(2, D - 6)
            new_landlord = r.choice(ids)
            rent = c.rent or round(r.uniform(8000, 25000), -2)
            self.txn(self._ts(day), c.account_id, r.choice(ids), rent * 0.5, "broker", new_payee=True)
            self.txn(self._ts(day + 1), c.account_id, new_landlord, rent * 2, "deposit", new_payee=True)
            self.txn(self._ts(day + 2), c.account_id, r.choice(self.merchant_ids), r.uniform(3000, 9000), "movers", new_payee=True)
            self.txn(self._ts(day + 4), c.account_id, new_landlord, rent, "rent")
        # 3. Family emergencies: large urgent transfer to a known person during a call.
        for c in r.sample(list(self.customers.values()), k=max(5, len(ids) // 200)):
            if not c.friends:
                continue
            amt = min(c.savings * r.uniform(0.2, 0.6), 150000)
            self.txn(self._ts(r.randint(1, D - 1)), c.account_id, r.choice(c.friends), amt,
                     "urgent", session=(1, 0, 0, 0, r.randint(120, 600)))
        # 4. Chit funds / group savings: same members pay a collector monthly.
        for _ in range(max(2, len(ids) // 800)):
            organiser = r.choice(ids)
            members = r.sample(ids, k=r.randint(15, 25))
            for month_start in range(0, D, 30):
                for m in members:
                    if m != organiser:
                        self.txn(self._ts(month_start + r.randint(4, 7)), m, organiser, 2000, "chit")
                self.txn(self._ts(month_start + 9), organiser, r.choice(members), 2000 * len(members) * 0.9, "chit")
        # 5. Legit personal loans spent on a new big-ticket payee (car, gadget, wedding).
        for c in r.sample(salaried, k=max(3, len(salaried) // 80)):
            day = r.randint(2, D - 3)
            amt = round(r.uniform(100000, 400000), -3)
            ts = self._ts(day, r.uniform(10, 17))
            self.account_event(ts, "loan", c.account_id, amt)
            self.txn(ts + timedelta(hours=r.uniform(2, 30)), c.account_id, r.choice(self.merchant_ids),
                     amt * r.uniform(0.6, 0.95), "purchase", new_payee=True)
        # 6. Second-hand purchases: 2-3 escalating payments to one new seller.
        for c in r.sample(list(self.customers.values()), k=max(5, len(ids) // 150)):
            seller = r.choice(ids)
            day = r.randint(1, D - 2)
            base = r.uniform(1000, 8000)
            for i in range(r.randint(2, 3)):
                self.txn(self._ts(day, 10 + 3 * i), c.account_id, seller, base * (1 + i), "item",
                         new_payee=(i == 0))


def _poisson(r: random.Random, lam: float) -> int:
    """Knuth's Poisson sampler (fine for small lambda)."""
    if lam <= 0:
        return 0
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= r.random()
        if p <= L:
            return k
        k += 1
