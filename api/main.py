"""Vyuh backend (FastAPI).

Start from the project root:
    uvicorn api.main:app --port 8000

Then open http://127.0.0.1:8000 for the web console and
http://127.0.0.1:8000/docs for the interactive API reference.
"""
from __future__ import annotations

import csv
import io
import json
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cases.store import Store
from core import config
from core.security import create_token, verify_token
from pipeline.events import EventIn

STATE: Dict[str, object] = {}
ROLES = {"investigator", "supervisor", "auditor"}
_FAILED_LOGINS: Dict[str, List[float]] = {}
_LAST_VIEW: Dict[tuple, float] = {}
VIEW_AUDIT_SECONDS = 600          # one "case_viewed" audit entry per user and case per 10 minutes


def runtime():
    rt = STATE.get("runtime")
    if rt is None:
        raise HTTPException(503, STATE.get("setup_error") or "Models or data not prepared yet. Run: python -m scripts.prepare")
    return rt


def store() -> Store:
    return STATE["store"]  # type: ignore[return-value]


def _stale_setup() -> str:
    """Models or data made by an older version of Vyuh would crash or mislead: say so."""
    meta_p = config.MODEL_DIR / "metadata.json"
    if not meta_p.exists():
        return ""
    from pipeline.features import COLLECTOR_FEATURES, JOURNEY_FEATURES
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    if (meta.get("collector_features") != COLLECTOR_FEATURES or meta.get("journey_features") != JOURNEY_FEATURES
            or not (config.DATA_DIR / "suspect_registry.csv").exists()):
        return ("Data and models were made by an older version of Vyuh. "
                "Run once: python -m scripts.prepare --force")
    return ""


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
    st = Store()
    created = st.ensure_default_users()
    STATE["store"] = st
    if created:
        st.audit("system", "system", "default_users_created", "", {"users": created})
    stale = _stale_setup()
    if stale:
        STATE["setup_error"] = stale
        print("\n*** " + stale + " ***\n", flush=True)
    elif (config.MODEL_DIR / "journey.txt").exists() and (config.DATA_DIR / "events.csv").exists():
        from api.runtime import Runtime
        rt = Runtime(st)
        rt.start_thread()
        STATE["runtime"] = rt
        st.audit("system", "system", "runtime_started", "", {"model_version": rt.models.metadata.get("version")})
    yield
    rt = STATE.get("runtime")
    if rt is not None:
        rt.shutdown()


app = FastAPI(title="Vyuh - Scam Campaign Intelligence", version="1.0.0", lifespan=lifespan,
              description="Detects AI-enabled scam workflows as campaigns and raises one explained case per "
                          "campaign. All data in this deployment is synthetic.")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'")
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else "no-cache"
    return response


# ---------------------------------------------------------------- auth
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


def current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not logged in")
    payload = verify_token(authorization.split(" ", 1)[1])
    if not payload or payload.get("role") not in ROLES:
        raise HTTPException(401, "Session expired or invalid")
    return {"username": payload["sub"], "role": payload["role"]}


def require(*roles: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, f"This action needs role: {' or '.join(roles)}")
        return user
    return dep


def api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    import hmac
    if not x_api_key or not hmac.compare_digest(x_api_key, config.API_KEY):
        raise HTTPException(401, "Invalid API key")


ANY = require("investigator", "supervisor", "auditor")
WORK = require("investigator", "supervisor")
SUP = require("supervisor")
AUDIT = require("supervisor", "auditor")


@app.post("/api/auth/login", tags=["auth"])
def login(body: LoginIn, request: Request):
    key = f"{body.username}|{request.client.host if request.client else ''}"
    now = time.time()
    recent = [t for t in _FAILED_LOGINS.get(key, []) if t > now - 300]
    if len(recent) >= 5:
        raise HTTPException(429, "Too many failed attempts. Try again in 5 minutes.")
    user = store().check_login(body.username, body.password)
    if not user:
        _FAILED_LOGINS[key] = recent + [now]
        store().audit(body.username, "unknown", "login_failed", "", {})
        raise HTTPException(401, "Wrong username or password")
    _FAILED_LOGINS.pop(key, None)
    store().audit(user["username"], user["role"], "login", "", {})
    return {"token": create_token(user["username"], user["role"]), **user}


@app.get("/api/auth/me", tags=["auth"])
def me(user: dict = Depends(current_user)):
    return user


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


@app.post("/api/auth/password", tags=["auth"])
def change_password(body: PasswordIn, user: dict = Depends(current_user)):
    if not store().check_login(user["username"], body.current_password):
        store().audit(user["username"], user["role"], "password_change_failed", user["username"], {})
        raise HTTPException(403, "Current password is wrong")
    store().set_password(user["username"], body.new_password)
    store().audit(user["username"], user["role"], "password_changed", user["username"], {})
    return {"ok": True}


# ---------------------------------------------------------------- health
@app.get("/api/health", tags=["system"])
def health():
    rt = STATE.get("runtime")
    return {"status": "ok", "runtime_ready": rt is not None, "setup_error": STATE.get("setup_error"),
            "model_version": rt.models.metadata.get("version") if rt else None}


# ---------------------------------------------------------------- live ops / simulation
@app.get("/api/status", tags=["operations"])
def status(user: dict = Depends(ANY)):
    rt = runtime()
    with rt.lock:
        return rt.status()


class ReplayIn(BaseModel):
    action: str = Field(pattern="^(start|pause|reset|step)$")
    speed: Optional[int] = Field(default=None, ge=1, le=20000)
    minutes: Optional[int] = Field(default=None, ge=1, le=1440)


@app.post("/api/replay", tags=["operations"])
def replay(body: ReplayIn, user: dict = Depends(WORK)):
    rt = runtime()
    if body.speed:
        rt.speed = body.speed
    if body.action == "start":
        rt.running = True
    elif body.action == "pause":
        rt.running = False
    elif body.action == "reset":
        rt.reset()
    elif body.action == "step":
        with rt.lock:
            rt.advance((body.minutes or 60) * 60)
    store().audit(user["username"], user["role"], f"replay_{body.action}", "", {"speed": rt.speed})
    return {"ok": True, "running": rt.running, "speed": rt.speed}


class InjectIn(BaseModel):
    playbook: str
    victims: int = Field(default=8, ge=1, le=60)
    senior_first: bool = False      # first victim = a 60+ customer with a trusted contact


@app.post("/api/simulate/inject", tags=["operations"])
def inject(body: InjectIn, user: dict = Depends(WORK)):
    rt = runtime()
    try:
        info = rt.inject(body.playbook, body.victims, senior_first=body.senior_first)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    store().audit(user["username"], user["role"], "campaign_injected", info["campaign_id"], info)
    return info


class AdversaryIn(BaseModel):
    on: bool


@app.post("/api/simulate/adversary", tags=["operations"])
def adversary(body: AdversaryIn, user: dict = Depends(WORK)):
    out = runtime().toggle_adversary(body.on)
    store().audit(user["username"], user["role"], "adversary_toggled", "", out)
    return out


class ModeIn(BaseModel):
    mode: str = Field(pattern="^(shadow|analyst|warnings|full)$")


@app.get("/api/mode", tags=["operations"])
def get_mode(user: dict = Depends(ANY)):
    return {"mode": runtime().engine.mode, "modes": list(config.MODES)}


@app.post("/api/mode", tags=["operations"])
def set_mode(body: ModeIn, user: dict = Depends(SUP)):
    out = runtime().set_mode(body.mode)
    store().audit(user["username"], user["role"], "mode_changed", "", out)
    return out


@app.get("/api/playbooks", tags=["playbooks"])
def playbooks(user: dict = Depends(ANY)):
    rt = runtime()
    lib = rt.models.library
    out = []
    for name, pb in rt.playbooks.items():
        out.append({"name": name, "title": pb["title"], "known": pb.get("known", True),
                    "in_library": name in lib, "library_size": lib.get(name, {}).get("n", 0),
                    "warning": " ".join(pb["warning"].split()),
                    "warning_hi": " ".join(pb.get("warning_hi", "").split())})
    return {"playbooks": out, "candidates": store().playbooks()}


@app.post("/api/playbooks/{case_id}/approve", tags=["playbooks"])
def approve_playbook(case_id: str, user: dict = Depends(SUP)):
    rt = runtime()
    with rt.lock:
        camp = rt.tracker.campaigns.get(case_id)
        if camp is None or camp.centroid is None:
            raise HTTPException(404, "case not found or has no journey shape")
        name = f"learned-{case_id.lower()}"
        samples = [[float(x) for x in rt.engine.journeys[m].fp] for m in camp.members
                   if m in rt.engine.journeys and rt.engine.journeys[m].fp is not None]
        rt.models.__dict__.pop("_sample_cache", None)
        rt.models.library[name] = {"centroid": [float(x) for x in camp.centroid], "threshold": 0.08,
                                   "samples": samples or [[float(x) for x in camp.centroid]],
                                   "n": len(camp.members), "title": f"Learned from {case_id}",
                                   "source": f"approved by {user['username']}"}
        rt.playbooks.setdefault(name, {"title": f"Learned pattern ({case_id})", "known": True,
                                       "warning": "This payment matches a scam pattern our investigators "
                                                  "recently confirmed. Stop and verify. Call 1930 if unsure."})
        store().upsert_playbook(name, f"Learned from {case_id}", "library", case_id,
                                rt.models.library[name]["centroid"], 0.08, user["username"])
    store().audit(user["username"], user["role"], "playbook_approved", case_id, {"name": name})
    return {"ok": True, "name": name}


# ---------------------------------------------------------------- cases
@app.get("/api/board", tags=["cases"])
def board(user: dict = Depends(ANY)):
    rt = runtime()
    with rt.lock:
        return rt.tracker.board()


@app.get("/api/cases/{case_id}", tags=["cases"])
def case_detail(case_id: str, user: dict = Depends(ANY)):
    rt = runtime()
    with rt.lock:
        if case_id not in rt.tracker.campaigns:
            raise HTTPException(404, "case not found")
        d = rt.tracker.detail(case_id, extras=rt.case_extras())
    d["decisions"] = store().decisions(case_id)
    d["actions"] = store().actions(case_id)
    d["audit"] = store().audit_log(50, case_id)
    key = (user["username"], case_id)
    now = time.time()
    if now - _LAST_VIEW.get(key, 0.0) >= VIEW_AUDIT_SECONDS:      # keep the audit log readable
        _LAST_VIEW[key] = now
        store().audit(user["username"], user["role"], "case_viewed", case_id, {})
    return d


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(confirm|monitor|reject|close)$")
    reason: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=500)


@app.post("/api/cases/{case_id}/decision", tags=["cases"])
def decide(case_id: str, body: DecisionIn, user: dict = Depends(WORK)):
    rt = runtime()
    if body.decision == "reject" and body.reason not in ("legitimate_business", "duplicate", "weak_evidence", "other"):
        raise HTTPException(400, "reject needs reason: legitimate_business, duplicate, weak_evidence or other")
    with rt.lock:
        if case_id not in rt.tracker.campaigns:
            raise HTTPException(404, "case not found")
        camp = rt.tracker.decide(case_id, body.decision, body.reason)
        if body.decision == "confirm":
            for m in camp.members:
                store().add_label(m, "scam_victim", f"investigator:{user['username']}", case_id)
            for c in camp.collectors:
                store().add_label(c, "collector_of_interest", f"investigator:{user['username']}", case_id)
        if body.decision == "reject" and body.reason == "legitimate_business":
            for c in camp.collectors:
                store().request_suppression(c, case_id, "legitimate_business", user["username"],
                                            rt.sim_t + config.SUPPRESSION_DAYS * 86400)
        store().upsert_case(camp.summary())
    store().add_decision(case_id, user["username"], user["role"], body.decision, body.reason, body.note)
    store().audit(user["username"], user["role"], f"case_{body.decision}", case_id,
                  {"reason": body.reason, "note": body.note})
    return {"ok": True, "status": camp.status}


ACTIONS = {
    "callback_live_victims": ("R3", WORK),
    "collector_review": ("R4", WORK),
    "draft_1930_report": ("R4", WORK),
    "share_with_partners": ("R5", SUP),
}


class ActionIn(BaseModel):
    action: str


@app.post("/api/cases/{case_id}/actions", tags=["cases"])
def case_action(case_id: str, body: ActionIn, user: dict = Depends(current_user)):
    if body.action not in ACTIONS:
        raise HTTPException(400, f"action must be one of {sorted(ACTIONS)}")
    level, dep = ACTIONS[body.action]
    dep(user)  # role check for this specific action
    rt = runtime()
    with rt.lock:
        camp = rt.tracker.campaigns.get(case_id)
        if camp is None:
            raise HTTPException(404, "case not found")
        if level in ("R4", "R5") and camp.status != "confirmed":
            raise HTTPException(409, "Confirm the case before account-level actions or external sharing")
        detail = ""
        if body.action == "callback_live_victims":
            live = [m for m, i in camp.member_info.items() if i.get("live")]
            for m in live:
                rec = rt.engine.journeys.get(m)
                if rec:
                    rec.protected = True
                rt.stopped_victims.add(m)
            detail = f"{len(live)} live victim(s) queued for call-back"
        elif body.action == "collector_review":
            detail = f"{len(camp.collectors)} collector account(s) sent for review per bank procedure"
        elif body.action == "draft_1930_report":
            rep = rt.tracker.report_1930(case_id, rt.case_extras())
            sm = rep["summary"]
            detail = (f"1930 report drafted: {sm['victims']} victims, {sm['accounts_to_request_hold']} accounts on the "
                      f"hold list (Rs {sm['amount_still_in_accounts']:,.0f} still in them). Download it from the case.")
        elif body.action == "share_with_partners":
            detail = "Pseudonymised indicator package released to partner institutions"
    aid = store().add_action(case_id, user["username"], user["role"], body.action, "done", detail)
    store().audit(user["username"], user["role"], f"action_{body.action}", case_id, {"level": level, "detail": detail})
    return {"ok": True, "id": aid, "detail": detail}


@app.get("/api/cases/{case_id}/export", tags=["cases"])
def export_case(case_id: str, user: dict = Depends(SUP)):
    rt = runtime()
    with rt.lock:
        camp = rt.tracker.campaigns.get(case_id)
        if camp is None:
            raise HTTPException(404, "case not found")
        if camp.status != "confirmed":
            raise HTTPException(409, "Only confirmed cases can be exported")
        pkg = {"format": "vyuh-campaign-package/1", "case_id": case_id, "playbook": camp.playbook,
               "emerging": camp.emerging, "victim_count": len(camp.members),
               "collector_tokens": sorted(camp.collectors),
               "journey_fingerprint": [round(float(x), 4) for x in camp.centroid] if camp.centroid is not None else None,
               "first_seen_sim": camp.created_t, "last_update_sim": camp.updated_t,
               "note": "Pseudonymised. Tokens can be resolved only by the originating institution."}
    store().audit(user["username"], user["role"], "case_exported", case_id, {})
    return pkg


@app.get("/api/cases/{case_id}/report", tags=["cases"])
def case_report(case_id: str, format: str = "json", user: dict = Depends(WORK)):
    """1930 / CFCFRMS-style package (JSON) or its money trail (CSV). Confirmed cases only."""
    rt = runtime()
    with rt.lock:
        camp = rt.tracker.campaigns.get(case_id)
        if camp is None:
            raise HTTPException(404, "case not found")
        if camp.status != "confirmed":
            raise HTTPException(409, "Confirm the case before drafting a 1930 report")
        rep = rt.tracker.report_1930(case_id, rt.case_extras())
    store().audit(user["username"], user["role"], "report_1930_downloaded", case_id, {"format": format})
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["section", "time_sim", "from_or_account", "to", "amount_inr", "detail"])
        for r in rep["money_trail"]:
            w.writerow(["money_trail", time.strftime("%Y-%m-%d %H:%M", time.localtime(r["time_sim"])), r["from"],
                        r["to"], r["amount"], r["kind"]])
        for h in rep["hold_list"]:
            w.writerow(["hold_list", "", h["account"], "", h["balance_7d"], h["role"]])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{case_id}-1930-report.csv"'})
    return rep


@app.get("/api/cases/{case_id}/truth", tags=["cases"])
def case_truth(case_id: str, user: dict = Depends(SUP)):
    rt = runtime()
    with rt.lock:
        if case_id not in rt.tracker.campaigns:
            raise HTTPException(404, "case not found")
        return rt.truth_for_case(case_id)


# ---------------------------------------------------------------- suppressions (two-person rule)
@app.get("/api/suppressions", tags=["cases"])
def list_suppressions(user: dict = Depends(ANY)):
    return store().suppressions()


class SuppressionDecisionIn(BaseModel):
    approve: bool


@app.post("/api/suppressions/{sid}", tags=["cases"])
def decide_suppression(sid: int, body: SuppressionDecisionIn, user: dict = Depends(SUP)):
    try:
        row = store().decide_suppression(sid, user["username"], body.approve)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if row is None:
        raise HTTPException(404, "not found")
    if body.approve:
        runtime().suppress(row["account"], row["until_sim"])
    store().audit(user["username"], user["role"], "suppression_" + ("approved" if body.approve else "declined"),
                  row["account"], {"id": sid})
    return row


@app.post("/api/suppressions/{sid}/revoke", tags=["cases"])
def revoke_suppression(sid: int, user: dict = Depends(SUP)):
    """Lift an approved suppression early (for example when new evidence appears)."""
    try:
        row = store().revoke_suppression(sid, user["username"])
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if row is None:
        raise HTTPException(404, "not found")
    runtime().unsuppress(row["account"])
    store().audit(user["username"], user["role"], "suppression_revoked", row["account"], {"id": sid})
    return row


# ---------------------------------------------------------------- customer phone view (demo)
@app.get("/api/phone", tags=["customer"])
def phone(user: dict = Depends(WORK)):
    rt = runtime()
    with rt.lock:
        return rt.phone_view()


class PhoneIn(BaseModel):
    choice: str = Field(pattern="^(cancel|continue|talk|genuine|approve|decline)$")


@app.post("/api/phone/respond", tags=["customer"])
def phone_respond(body: PhoneIn, user: dict = Depends(WORK)):
    out = runtime().phone_respond(body.choice)
    if not out.get("ok"):
        raise HTTPException(409, out.get("detail", "not possible now"))
    store().audit(user["username"], user["role"], "customer_response_simulated", "", out)
    return out


class LockIn(BaseModel):
    on: bool


@app.post("/api/phone/killswitch", tags=["customer"])
def phone_killswitch(body: LockIn, user: dict = Depends(WORK)):
    """Customer control: stop all my digital payments (RBI discussion paper, 2026)."""
    out = runtime().kill_switch(body.on)
    if not out.get("ok"):
        raise HTTPException(409, out.get("detail", "not possible now"))
    store().audit(user["username"], user["role"], "customer_kill_switch", "", out)
    return out


# ---------------------------------------------------------------- monitoring & audit
@app.get("/api/model", tags=["system"])
def model_info(user: dict = Depends(ANY)):
    rt = runtime()
    meta = dict(rt.models.metadata)
    meta.pop("journey_feature_quantiles", None)
    return {"metadata": meta, "hashes_valid": rt.models.verify_hashes(),
            "library": {k: {"n": v.get("n"), "victims": v.get("victims"), "threshold": v.get("threshold"),
                            "title": v.get("title")}
                        for k, v in rt.models.library.items()}}


@app.get("/api/monitoring/drift", tags=["system"])
def drift(user: dict = Depends(ANY)):
    """Population Stability Index of live journey features vs training."""
    rt = runtime()
    from eval.drift import psi_report
    with rt.lock:
        return psi_report(rt)


@app.get("/api/labels/training", tags=["system"])
def training_labels(user: dict = Depends(SUP)):
    """Investigator labels that are old enough (7+ days) to be used for retraining."""
    out = store().labels_for_training()
    return {"min_age_days": out["min_age_days"], "eligible": len(out["eligible"]),
            "held_back": out["held_back"], "total": out["total"]}


@app.get("/api/interventions", tags=["customer"])
def interventions(user: dict = Depends(ANY)):
    return store().interventions(limit=200)


@app.get("/api/audit", tags=["system"])
def audit(user: dict = Depends(AUDIT)):
    return {"verify": store().verify_audit(), "entries": store().audit_log(300)}


@app.get("/api/report", tags=["system"])
def evaluation_report(user: dict = Depends(ANY)):
    p = config.REPORT_DIR / "evaluation.json"
    if not p.exists():
        raise HTTPException(404, "No evaluation yet. Run: python -m eval.evaluate")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- bank integration API
@app.post("/api/v1/score", tags=["integration"], dependencies=[Depends(api_key)])
def score(event: EventIn):
    """Dry-run score for a payment BEFORE it is authorised (called by the bank's fraud engine).

    Does not change any state. The fraud engine keeps final authority.
    """
    rt = runtime()
    with rt.lock:
        d = rt.engine.evaluate(event.to_event(rt.tokens.to_token))
    if d is None:
        return {"risk": 0.0, "level": "R0", "detail": "not a payment"}
    out = d.to_dict()
    out.pop("b1_score", None)
    return out


@app.post("/api/v1/events", tags=["integration"], dependencies=[Depends(api_key)])
def ingest(events: List[EventIn]):
    """Ingest final (authorised) events from bank systems. Max 1,000 per call."""
    if len(events) > 1000:
        raise HTTPException(413, "max 1000 events per call")
    rt = runtime()
    results = []
    with rt.lock:
        for e in sorted(events, key=lambda x: x.ts):
            ev = e.to_event(rt.tokens.to_token)
            d = rt.engine.process(ev)
            if d is not None and d.level != "R0":
                store().add_intervention(d.to_dict())
            results.append({"event_id": e.event_id, "level": d.level if d else "R0",
                            "risk": d.risk if d else 0.0})
    return {"accepted": len(results), "results": results}


# ---------------------------------------------------------------- test lab (judge's own input)
SAMPLES_DIR = config.ROOT / "samples"


class LabRunIn(BaseModel):
    csv: str = Field(min_length=1, max_length=9_000_000)
    filename: str = Field(default="upload.csv", max_length=120)
    history_days: Optional[float] = Field(default=None, ge=0, le=60)   # None = automatic


class LabPaymentIn(BaseModel):
    payer: str = Field(min_length=1, max_length=64)
    payee: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0, lt=1e9)
    remark: str = Field(default="", max_length=60)
    call_active: int = Field(default=0, ge=0, le=1)
    screen_share: int = Field(default=0, ge=0, le=1)
    remote_app: int = Field(default=0, ge=0, le=1)
    paste_payee: int = Field(default=0, ge=0, le=1)
    payee_fri: int = Field(default=0, ge=0, le=3)
    payee_type: str = Field(default="individual", pattern="^(individual|business)$")
    payee_age_days: float = Field(default=180, ge=0, le=36500)
    trusted_contact: int = Field(default=0, ge=0, le=1)


@app.get("/api/lab/template", tags=["test lab"])
def lab_template(user: dict = Depends(ANY)):
    from api.lab import template_csv
    return Response(template_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="vyuh_test_template.csv"'})


@app.get("/api/lab/samples", tags=["test lab"])
def lab_samples(user: dict = Depends(ANY)):
    if not SAMPLES_DIR.exists():
        return []
    out = []
    for f in sorted(SAMPLES_DIR.glob("*.csv")):
        first = f.open(encoding="utf-8").readline().lstrip("# ").strip()
        out.append({"name": f.name, "about": first[:200]})
    return out


@app.get("/api/lab/samples/{name}", tags=["test lab"])
def lab_sample(name: str, user: dict = Depends(ANY)):
    f = (SAMPLES_DIR / name).resolve()
    if f.parent != SAMPLES_DIR.resolve() or f.suffix != ".csv" or not f.exists():
        raise HTTPException(404, "No such sample")
    return Response(f.read_text(encoding="utf-8"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{f.name}"'})


@app.post("/api/lab/run", tags=["test lab"])
def lab_run(body: LabRunIn, user: dict = Depends(WORK)):
    from api import lab
    rt = runtime()
    try:
        res = lab.run_file(body.csv, rt.models, rt.playbooks, body.history_days)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    m = res.get("metrics") or {}
    store().audit(user["username"], user["role"], "lab_run", body.filename[:80],
                  {"rows": res["summary"]["rows"], "flagged": res["summary"]["flagged"],
                   "cases": len(res["cases"]), "labelled": m.get("labelled_payments", 0)})
    return res


class LabScenarioIn(BaseModel):
    kind: str = Field(default="task_scam", pattern="^(task_scam|digital_arrest|investment|kyc_remote|genuine_new_payee)$")
    victims: int = Field(default=6, ge=1, le=30)
    steps: int = Field(default=4, ge=1, le=6)
    first_amount: float = Field(default=2000, ge=100, le=5_000_000)
    growth: float = Field(default=2.0, ge=1.0, le=5.0)
    on_call: Optional[bool] = None
    screen_share: Optional[bool] = None
    new_account_each_payment: bool = False
    hide_words: bool = False
    senior_with_trusted_contact: bool = False


@app.post("/api/lab/scenario", tags=["test lab"])
def lab_scenario(body: LabScenarioIn, user: dict = Depends(WORK)):
    from api import lab
    rt = runtime()
    res = lab.run_scenario(body.model_dump(), rt.models, rt.playbooks)
    store().audit(user["username"], user["role"], "lab_scenario", body.kind,
                  {"victims": body.victims, "flagged": res["summary"]["flagged"], "cases": len(res["cases"])})
    return res


@app.post("/api/lab/score", tags=["test lab"])
def lab_score(body: LabPaymentIn, user: dict = Depends(WORK)):
    from api import lab
    rt = runtime()
    return lab.score_payment(body.model_dump(), rt.models, rt.playbooks)


# ---------------------------------------------------------------- web console
if config.FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(str(config.FRONTEND_DIR / "index.html"))


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal error. See server log.", "type": type(exc).__name__})
