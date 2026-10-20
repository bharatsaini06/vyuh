"""Scam-stage tagger.

Maps each event, in context, to zero or more Vyuh scam stages:

    S3 Trust       small credit from a stranger, or money "paid back" by a new payee
    S4 Control     payment made during a call / screen-share / remote-access session
    S5 Extraction  payment to a fresh payee, escalation, loan/FD money moved
    S6 Cash-out    (receiver side) money forwarded on within minutes
    S7 Re-victim   "tax / fee / unlock" payment after several payments

S1 (contact) and S2 (hook) happen outside the bank; they can only be observed
when a customer shares a message, so they are not tagged from payments.
The rules are deliberately simple and readable so investigators can audit them.
"""
from __future__ import annotations

from typing import List

from core import config

STAGE_NAMES = {
    "S3": "Trust building",
    "S4": "Control / coaching",
    "S5": "Extraction",
    "S6": "Cash-out",
    "S7": "Re-victimisation",
}
FEE_WORDS = {"tax", "fee", "unlock", "penalty", "clearance"}


def tag_credit(amount: float, unknown_sender: bool, refund: bool) -> List[str]:
    tags = []
    if refund:
        tags.append("S3:refund")
    elif unknown_sender and amount <= 2000:
        tags.append("S3:stranger_credit")
    return tags


def tag_debit(amount: float, fresh: bool, call: int, screen: int, remote: int,
              prev_fresh_amount: float, n_prev_fresh_7d: int, remark: str) -> List[str]:
    tags = []
    if call or screen or remote:
        tags.append("S4:" + ("remote_access" if remote else "screen_share" if screen else "on_call"))
    if fresh:
        if prev_fresh_amount > 0 and amount >= 1.5 * prev_fresh_amount:
            tags.append("S5:escalation")
        else:
            tags.append("S5:new_payee")
        if remark in FEE_WORDS and n_prev_fresh_7d >= 2:
            tags.append("S7:fee_to_withdraw")
    return tags


def tag_funding(kind: str) -> List[str]:
    return ["S5:loan_moved" if kind == "loan" else "S5:fd_broken"]


def is_risky_remark(remark: str) -> bool:
    return remark in config.RISKY_REMARKS
