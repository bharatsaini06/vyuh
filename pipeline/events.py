"""Event model, validation and loading.

An *event* is one thing that happened: a payment (`txn`), a loan disbursal
(`loan`) or a fixed-deposit break (`fd_break`). Every identifier is
pseudonymised (keyed hash) the moment it enters the system.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from pydantic import BaseModel, Field, field_validator

from core.security import pseudonymise

EVENT_TYPES = {"txn", "loan", "fd_break"}


@dataclass(slots=True)
class Event:
    eid: str
    t: float            # seconds since epoch
    type: str
    src: str            # pseudonymised account token
    dst: str
    amount: float
    rail: str = "UPI"
    remark: str = ""
    is_collect: int = 0
    call: int = 0
    screen: int = 0
    remote: int = 0
    paste: int = 0
    sess: int = 0
    fri: int = 0
    raw_src: str = ""   # kept only inside the simulator/demo runtime, never stored
    raw_dst: str = ""


class EventIn(BaseModel):
    """Schema for events arriving through the integration API."""
    event_id: str = Field(min_length=1, max_length=64)
    ts: datetime
    type: str = "txn"
    src: str = Field(min_length=1, max_length=64)
    dst: str = Field(default="", max_length=64)
    amount: float = Field(gt=0, lt=1e9)
    rail: str = "UPI"
    remark: str = Field(default="", max_length=60)
    is_collect: int = 0
    call_active: int = 0
    screen_share: int = 0
    remote_app: int = 0
    paste_payee: int = 0
    session_sec: int = Field(default=0, ge=0, le=86400)
    payee_fri: int = Field(default=0, ge=0, le=3)

    @field_validator("type")
    @classmethod
    def _type_ok(cls, v: str) -> str:
        if v not in EVENT_TYPES:
            raise ValueError(f"type must be one of {sorted(EVENT_TYPES)}")
        return v

    def to_event(self, tokens: Optional[Dict[str, str]] = None) -> Event:
        tok = tokens if tokens is not None else {}
        def p(x: str) -> str:
            if not x:
                return ""
            if x not in tok:
                tok[x] = pseudonymise(x)
            return tok[x]
        return Event(self.event_id, self.ts.timestamp(), self.type, p(self.src), p(self.dst),
                     float(self.amount), self.rail, normalise_remark(self.remark), int(self.is_collect),
                     int(self.call_active), int(self.screen_share), int(self.remote_app),
                     int(self.paste_payee), int(self.session_sec), int(self.payee_fri))


def normalise_remark(text: str) -> str:
    """Keep only a lower-case first word; free text is never stored."""
    text = (text or "").strip().lower()
    return text.split()[0][:20] if text else ""


@dataclass(slots=True)
class AccountInfo:
    holder_type: str
    opened_t: float
    category: str = ""
    age_band: str = ""
    state: str = ""
    trusted_contact: int = 0      # customer setting: a family member approves risky large payments


class TokenMap:
    """Caches raw-id -> token so we hash each identifier once."""

    def __init__(self) -> None:
        self.to_token: Dict[str, str] = {}
        self.to_raw: Dict[str, str] = {}   # only used by the simulator UI (phone view)

    def __call__(self, raw: str) -> str:
        if not raw:
            return ""
        tok = self.to_token.get(raw)
        if tok is None:
            tok = pseudonymise(raw)
            self.to_token[raw] = tok
            self.to_raw[tok] = raw
        return tok


def load_accounts(path: Path, tokens: TokenMap) -> Dict[str, AccountInfo]:
    info: Dict[str, AccountInfo] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            info[tokens(row["account_id"])] = AccountInfo(
                row["holder_type"], datetime.fromisoformat(row["opened_at"]).timestamp(),
                row["merchant_category"], row["age_band"], row["home_state"],
                int(row.get("trusted_contact") or 0))
    return info


def load_registry(path: Path, tokens: TokenMap) -> Dict[str, float]:
    """Suspect-Registry / mule-flag feed: token -> time the account was listed.

    Features only ever use a listing whose time is <= the payment time, so loading
    the whole file up front leaks nothing from the future. Missing file = no feed.
    """
    out: Dict[str, float] = {}
    if not Path(path).exists():
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tok = tokens(row["account_id"])
            t = datetime.fromisoformat(row["listed_at"]).timestamp()
            if tok not in out or t < out[tok]:
                out[tok] = t
    return out


def load_complaints(path: Path, tokens: TokenMap) -> List[tuple]:
    """1930 complaints: list of (t, victim_token, [paid account tokens]), time-ordered.
    The campaign columns in the file are simulator truth and are NOT returned."""
    out: List[tuple] = []
    if not Path(path).exists():
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append((datetime.fromisoformat(row["complained_at"]).timestamp(), tokens(row["victim_id"]),
                        [tokens(a) for a in row["paid_accounts"].split("|") if a]))
    out.sort(key=lambda r: r[0])
    return out


def read_events(path: Path, tokens: TokenMap, start: Optional[datetime] = None,
                end: Optional[datetime] = None) -> Iterator[Event]:
    """Stream events from CSV in file order (the generator writes them sorted)."""
    t_start = start.timestamp() if start else None
    t_end = end.timestamp() if end else None
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            t = datetime.fromisoformat(row[1]).timestamp()
            if t_start is not None and t < t_start:
                continue
            if t_end is not None and t >= t_end:
                break
            yield Event(row[0], t, row[2], tokens(row[3]), tokens(row[4]), float(row[5]), row[6],
                        normalise_remark(row[7]), int(row[8]), int(row[9]), int(row[10]), int(row[11]),
                        int(row[12]), int(row[13]), int(row[14]), row[3], row[4])


def load_event_list(path: Path, tokens: TokenMap, start: Optional[datetime] = None,
                    end: Optional[datetime] = None) -> List[Event]:
    return list(read_events(path, tokens, start, end))


def load_labels(path: Path) -> Dict[str, tuple]:
    """event_id -> (campaign_id, playbook, stage, role). Evaluation only."""
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["event_id"]] = (row["campaign_id"], row["playbook"], row["stage"], row["role"])
    return out


def load_ground_truth(path: Path, tokens: TokenMap) -> Dict[str, List[tuple]]:
    """token -> list of (role, campaign_id, playbook). Evaluation only."""
    out: Dict[str, List[tuple]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.setdefault(tokens(row["account_id"]), []).append(
                (row["role"], row["campaign_id"], row["playbook"]))
    return out
