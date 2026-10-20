"""SQLite persistence: users, cases, decisions, actions, interventions,
labels, suppressions, playbook approvals and a tamper-evident audit log.

SQLite ships with Python, so there is no database server to install.
Every write goes through one lock, which makes the store safe to use from
the API threads and the replay thread at the same time.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import config
from core.security import hash_password, verify_password

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, role TEXT NOT NULL, pw_hash TEXT NOT NULL,
    created_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY, kind TEXT, status TEXT, playbook TEXT, emerging INTEGER,
    victims INTEGER, paid REAL, saveable REAL, confidence REAL, priority REAL,
    created_sim REAL, updated_sim REAL, updated_at REAL, summary TEXT);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, ts REAL, actor TEXT, role TEXT,
    decision TEXT, reason TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, ts REAL, actor TEXT, role TEXT,
    action TEXT, status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS interventions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, sim_t REAL, account TEXT, level TEXT, risk REAL,
    playbook TEXT, campaign_id TEXT, amount REAL, payee TEXT, message TEXT, outcome TEXT,
    reasons TEXT);
CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, label TEXT, source TEXT, case_id TEXT,
    ts REAL);
CREATE TABLE IF NOT EXISTS suppressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, case_id TEXT, reason TEXT,
    requested_by TEXT, approved_by TEXT, status TEXT, ts REAL, until_sim REAL);
CREATE TABLE IF NOT EXISTS playbooks (
    name TEXT PRIMARY KEY, title TEXT, status TEXT, case_id TEXT, centroid TEXT,
    threshold REAL, approved_by TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, role TEXT, action TEXT,
    target TEXT, details TEXT, prev_hash TEXT, hash TEXT);
CREATE INDEX IF NOT EXISTS ix_interventions_account ON interventions(account);
CREATE INDEX IF NOT EXISTS ix_decisions_case ON decisions(case_id);
"""


class Store:
    def __init__(self, path: Path = config.DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    # ------------------------------------------------------------ generic
    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> List[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def reset_runtime(self) -> None:
        """Clear simulation-generated data (cases, interventions...). Users and audit are kept."""
        with self.lock:
            for t in ("cases", "decisions", "actions", "interventions", "labels", "suppressions"):
                self.db.execute(f"DELETE FROM {t}")
            self.db.execute("DELETE FROM playbooks WHERE status='candidate'")
            self.db.commit()

    # ------------------------------------------------------------ users
    def ensure_default_users(self) -> List[str]:
        created = []
        for role, pw in config.DEFAULT_PASSWORDS.items():
            if not self._all("SELECT username FROM users WHERE username=?", (role,)):
                self._exec("INSERT INTO users VALUES (?,?,?,?,1)", (role, role, hash_password(pw), time.time()))
                created.append(role)
        return created

    def check_login(self, username: str, password: str) -> Optional[dict]:
        rows = self._all("SELECT * FROM users WHERE username=? AND active=1", (username,))
        if rows and verify_password(password, rows[0]["pw_hash"]):
            return {"username": rows[0]["username"], "role": rows[0]["role"]}
        return None

    def set_password(self, username: str, password: str) -> None:
        self._exec("UPDATE users SET pw_hash=? WHERE username=?", (hash_password(password), username))

    def add_user(self, username: str, role: str, password: str) -> None:
        self._exec("INSERT OR REPLACE INTO users VALUES (?,?,?,?,1)",
                   (username, role, hash_password(password), time.time()))

    # ------------------------------------------------------------ cases
    def upsert_case(self, s: dict) -> None:
        self._exec("""INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(case_id) DO UPDATE SET status=excluded.status, playbook=excluded.playbook,
            emerging=excluded.emerging, victims=excluded.victims, paid=excluded.paid,
            saveable=excluded.saveable, confidence=excluded.confidence, priority=excluded.priority,
            updated_sim=excluded.updated_sim, updated_at=excluded.updated_at, summary=excluded.summary""",
                   (s["case_id"], s["kind"], s["status"], s.get("playbook"), int(s.get("emerging", False)),
                    s["victims"], s["paid"], s["saveable"], s["confidence"], s["priority"],
                    s["created_t"], s["updated_t"], time.time(), json.dumps(s)))

    def add_decision(self, case_id: str, actor: str, role: str, decision: str, reason: str, note: str) -> None:
        self._exec("INSERT INTO decisions (case_id, ts, actor, role, decision, reason, note) VALUES (?,?,?,?,?,?,?)",
                   (case_id, time.time(), actor, role, decision, reason, note))

    def decisions(self, case_id: str) -> List[dict]:
        return self._all("SELECT * FROM decisions WHERE case_id=? ORDER BY id", (case_id,))

    def add_action(self, case_id: str, actor: str, role: str, action: str, status: str, detail: str = "") -> int:
        cur = self._exec("INSERT INTO actions (case_id, ts, actor, role, action, status, detail) VALUES (?,?,?,?,?,?,?)",
                         (case_id, time.time(), actor, role, action, status, detail))
        return int(cur.lastrowid)

    def actions(self, case_id: str) -> List[dict]:
        return self._all("SELECT * FROM actions WHERE case_id=? ORDER BY id", (case_id,))

    # ------------------------------------------------------------ interventions & labels
    def add_intervention(self, d: dict) -> None:
        self._exec("""INSERT INTO interventions (sim_t, account, level, risk, playbook, campaign_id, amount,
                      payee, message, outcome, reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (d["t"], d["payer"], d["level"], d["risk"], d.get("playbook"), d.get("campaign_id"),
                    d["amount"], d["payee"], d.get("message", ""), d.get("outcome", "shown"),
                    json.dumps(d.get("reasons", []))))

    def set_intervention_outcome(self, account: str, outcome: str) -> None:
        self._exec("""UPDATE interventions SET outcome=? WHERE id=(SELECT MAX(id) FROM interventions
                      WHERE account=?)""", (outcome, account))

    def interventions(self, account: Optional[str] = None, limit: int = 200) -> List[dict]:
        if account:
            return self._all("SELECT * FROM interventions WHERE account=? ORDER BY id DESC LIMIT ?", (account, limit))
        return self._all("SELECT * FROM interventions ORDER BY id DESC LIMIT ?", (limit,))

    def add_label(self, account: str, label: str, source: str, case_id: str) -> None:
        self._exec("INSERT INTO labels (account, label, source, case_id, ts) VALUES (?,?,?,?,?)",
                   (account, label, source, case_id, time.time()))

    def labels(self) -> List[dict]:
        return self._all("SELECT * FROM labels ORDER BY id")

    def labels_for_training(self, min_age_days: float = config.LABEL_MIN_AGE_DAYS,
                            now: Optional[float] = None) -> Dict[str, Any]:
        """Labels old enough to trust for retraining. Younger labels are held back
        (a confirmation can still be reversed, and complaints keep arriving)."""
        now = time.time() if now is None else now
        rows = self.labels()
        cutoff = now - min_age_days * 86400
        ok = [r for r in rows if r["ts"] <= cutoff]
        return {"min_age_days": min_age_days, "eligible": ok, "held_back": len(rows) - len(ok), "total": len(rows)}

    # ------------------------------------------------------------ suppressions (two-person rule)
    def request_suppression(self, account: str, case_id: str, reason: str, by: str, until_sim: float) -> None:
        self._exec("""INSERT INTO suppressions (account, case_id, reason, requested_by, approved_by, status, ts, until_sim)
                      VALUES (?,?,?,?,NULL,'pending',?,?)""", (account, case_id, reason, by, time.time(), until_sim))

    def suppressions(self, status: Optional[str] = None) -> List[dict]:
        if status:
            return self._all("SELECT * FROM suppressions WHERE status=? ORDER BY id DESC", (status,))
        return self._all("SELECT * FROM suppressions ORDER BY id DESC")

    def decide_suppression(self, sid: int, approver: str, approve: bool) -> Optional[dict]:
        rows = self._all("SELECT * FROM suppressions WHERE id=?", (sid,))
        if not rows:
            return None
        if rows[0]["requested_by"] == approver:
            raise PermissionError("two-person rule: the requester cannot approve their own request")
        if rows[0]["status"] != "pending":
            raise ValueError(f"request is already {rows[0]['status']}; use revoke to lift an approved suppression")
        self._exec("UPDATE suppressions SET status=?, approved_by=? WHERE id=?",
                   ("approved" if approve else "declined", approver, sid))
        return self._all("SELECT * FROM suppressions WHERE id=?", (sid,))[0]

    def revoke_suppression(self, sid: int, by: str) -> Optional[dict]:
        """Lift an approved suppression early (e.g. new evidence appeared)."""
        rows = self._all("SELECT * FROM suppressions WHERE id=?", (sid,))
        if not rows:
            return None
        if rows[0]["status"] != "approved":
            raise ValueError("only an approved suppression can be revoked")
        self._exec("UPDATE suppressions SET status='revoked', approved_by=? WHERE id=?",
                   (f"{rows[0]['approved_by']} (revoked by {by})", sid))
        return self._all("SELECT * FROM suppressions WHERE id=?", (sid,))[0]

    def expire_suppressions(self, sim_now: float) -> List[dict]:
        """Approved suppressions end automatically after SUPPRESSION_DAYS (simulated time)."""
        rows = self._all("SELECT * FROM suppressions WHERE status='approved' AND until_sim<=?", (sim_now,))
        for r in rows:
            self._exec("UPDATE suppressions SET status='expired' WHERE id=?", (r["id"],))
        return rows

    # ------------------------------------------------------------ playbooks
    def upsert_playbook(self, name: str, title: str, status: str, case_id: str, centroid: list,
                        threshold: float, approved_by: Optional[str]) -> None:
        self._exec("INSERT OR REPLACE INTO playbooks VALUES (?,?,?,?,?,?,?,?)",
                   (name, title, status, case_id, json.dumps(centroid), threshold, approved_by, time.time()))

    def playbooks(self) -> List[dict]:
        return self._all("SELECT name, title, status, case_id, threshold, approved_by, ts FROM playbooks ORDER BY ts DESC")

    # ------------------------------------------------------------ audit (hash chain)
    def audit(self, actor: str, role: str, action: str, target: str = "", details: Any = None) -> None:
        with self.lock:
            last = self.db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else "GENESIS"
            ts = time.time()
            det = json.dumps(details or {}, sort_keys=True, default=str)
            h = hashlib.sha256(f"{prev}|{ts:.6f}|{actor}|{role}|{action}|{target}|{det}".encode()).hexdigest()
            self.db.execute("INSERT INTO audit (ts, actor, role, action, target, details, prev_hash, hash) "
                            "VALUES (?,?,?,?,?,?,?,?)", (ts, actor, role, action, target, det, prev, h))
            self.db.commit()

    def audit_log(self, limit: int = 300, target: Optional[str] = None) -> List[dict]:
        if target:
            return self._all("SELECT * FROM audit WHERE target=? ORDER BY id DESC LIMIT ?", (target, limit))
        return self._all("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    def verify_audit(self) -> Dict[str, Any]:
        """Recompute the hash chain. Any edited or deleted row breaks it."""
        rows = self._all("SELECT * FROM audit ORDER BY id")
        prev = "GENESIS"
        for r in rows:
            h = hashlib.sha256(f"{prev}|{r['ts']:.6f}|{r['actor']}|{r['role']}|{r['action']}|"
                               f"{r['target']}|{r['details']}".encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != h:
                return {"valid": False, "broken_at": r["id"], "entries": len(rows)}
            prev = r["hash"]
        return {"valid": True, "entries": len(rows)}
