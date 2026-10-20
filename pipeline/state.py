"""Per-account rolling state (the "journey store").

One AcctState per pseudonymised account. It remembers just enough recent
history (30 days as payer, 7 days as receiver) to compute features quickly.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict, Optional, Set, Tuple

DAY = 86400.0
HOUR = 3600.0

# History record for an account acting as a customer (payer/receiver):
# (t, kind, amount, counterparty, fresh, unknown_credit, refund, call, screen,
#  remote, paste, sess, collect, remark, stages)
H_T, H_KIND, H_AMT, H_CP, H_FRESH, H_UNKNOWN, H_REFUND, H_CALL, H_SCREEN, H_REMOTE, \
    H_PASTE, H_SESS, H_COLLECT, H_REMARK, H_STAGES = range(15)


class AcctState:
    __slots__ = ("payees", "senders", "hist", "out_total", "first_t", "last_t", "reactivated_t",
                 "inb", "outb", "last_in_t", "fri", "last_warn", "collector_score",
                 "collector_t", "collector_n", "in_total_all")

    def __init__(self, t: float) -> None:
        self.payees: Dict[str, Tuple[int, float]] = {}   # payee -> (count, first_paid_t)
        self.senders: Dict[str, float] = {}             # sender -> first time seen
        self.hist: Deque[tuple] = deque()
        self.out_total = 0.0
        self.in_total_all = 0.0
        self.first_t = t
        self.last_t = t
        self.reactivated_t = 0.0
        self.inb: Deque[tuple] = deque()      # (t, payer, amount, first_time)
        self.outb: Deque[tuple] = deque()     # (t, payee, amount, dwell_minutes)
        self.last_in_t = 0.0
        self.fri = 0
        self.last_warn: Dict[str, float] = {}
        self.collector_score = 0.0
        self.collector_t = 0.0
        self.collector_n = -1

    # ------------------------------------------------------------ maintenance
    def touch(self, t: float) -> None:
        if t - self.last_t > 45 * DAY:
            self.reactivated_t = t
        self.last_t = max(self.last_t, t)

    def prune(self, t: float) -> None:
        h = self.hist
        while h and h[0][H_T] < t - 30 * DAY:
            h.popleft()
        while self.inb and self.inb[0][0] < t - 7 * DAY:
            self.inb.popleft()
        while self.outb and self.outb[0][0] < t - 7 * DAY:
            self.outb.popleft()

    # ------------------------------------------------------------ queries
    def baseline_monthly(self, t: float, data_start: float) -> float:
        """Typical monthly outflow learned from this account's own history."""
        # divide by the history we actually have (at least 14 days), so the baseline is
        # not under-estimated in the first month of data (that showed up as false drift)
        days = max(14.0, (t - max(self.first_t, data_start)) / DAY)
        return max(1000.0, self.out_total / days * 30.0)

    def is_fresh_payee(self, payee: str, t: float) -> bool:
        rec = self.payees.get(payee)
        return rec is None or rec[1] >= t - 30 * DAY

    def never_paid(self, payee: str) -> bool:
        return payee not in self.payees

    def balance_7d(self) -> float:
        return sum(x[2] for x in self.inb) - sum(x[2] for x in self.outb)
