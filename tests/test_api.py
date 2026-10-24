"""End-to-end: generate a small world, train, start the API, use it like a client."""
import pytest
from fastapi.testclient import TestClient

from core import config


@pytest.fixture(scope="module")
def client():
    config.ensure_dirs()
    from sim.generate import generate
    generate(config.DATA_DIR, config.SIM_CUSTOMERS, config.SIM_DAYS, config.SIM_SEED, set())
    from models.train import train
    train()
    from api.runtime import build_warm_state
    build_warm_state(verbose=False)
    from api.main import app
    with TestClient(app) as c:
        yield c


def login(c, user, pw):
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"authorization": "Bearer " + r.json()["token"]}


def test_health_and_auth(client):
    assert client.get("/api/health").json()["runtime_ready"] is True
    assert client.get("/api/board").status_code == 401
    assert client.post("/api/auth/login", json={"username": "investigator", "password": "nope"}).status_code == 401


def test_roles_enforced(client):
    aud = login(client, "auditor", config.DEFAULT_PASSWORDS["auditor"])
    assert client.get("/api/board", headers=aud).status_code == 200
    assert client.post("/api/simulate/inject", json={"playbook": "task_scam"}, headers=aud).status_code == 403
    inv = login(client, "investigator", config.DEFAULT_PASSWORDS["investigator"])
    assert client.get("/api/audit", headers=inv).status_code == 403


def test_campaign_flow(client):
    inv = login(client, "investigator", config.DEFAULT_PASSWORDS["investigator"])
    sup = login(client, "supervisor", config.DEFAULT_PASSWORDS["supervisor"])
    r = client.post("/api/simulate/inject", json={"playbook": "task_scam", "victims": 10}, headers=inv)
    assert r.status_code == 200
    for _ in range(12):   # advance 12 simulated hours
        assert client.post("/api/replay", json={"action": "step", "minutes": 60}, headers=inv).status_code == 200
    st = client.get("/api/status", headers=inv).json()
    assert st["events_processed"] > 0
    board = client.get("/api/board", headers=inv).json()
    assert "cases" in board and "monitoring" in board
    cases = board["cases"] + board["overflow"]
    if cases:
        cid = cases[0]["case_id"]
        d = client.get(f"/api/cases/{cid}", headers=inv).json()
        assert d["why"] and "graph" in d
        assert client.post(f"/api/cases/{cid}/actions", json={"action": "share_with_partners"}, headers=inv).status_code == 403
        assert client.get(f"/api/cases/{cid}/report", headers=inv).status_code == 409   # confirm first
        assert client.post(f"/api/cases/{cid}/decision", json={"decision": "confirm"}, headers=inv).status_code == 200
        assert client.get(f"/api/cases/{cid}/export", headers=sup).status_code == 200
        rep = client.get(f"/api/cases/{cid}/report", headers=inv).json()
        assert rep["format"] == "vyuh-1930-report/1" and "hold_list" in rep and "money_trail" in rep
        csv_r = client.get(f"/api/cases/{cid}/report?format=csv", headers=inv)
        assert csv_r.status_code == 200 and csv_r.text.startswith("section,")
    audit = client.get("/api/audit", headers=sup).json()
    assert audit["verify"]["valid"] is True


def test_integration_api_requires_key_and_validates(client):
    ev = {"event_id": "x1", "ts": "2026-09-20T10:00:00", "type": "txn", "src": "CUST000001",
          "dst": "NEWACCT999", "amount": 25000, "call_active": 1}
    assert client.post("/api/v1/score", json=ev).status_code == 401
    h = {"x-api-key": "test-api-key"}
    r = client.post("/api/v1/score", json=ev, headers=h)
    assert r.status_code == 200 and 0.0 <= r.json()["risk"] <= 1.0
    bad = dict(ev, amount=-5)
    assert client.post("/api/v1/score", json=bad, headers=h).status_code == 422


def test_customer_screen_and_controls(client):
    inv = login(client, "investigator", config.DEFAULT_PASSWORDS["investigator"])
    sup = login(client, "supervisor", config.DEFAULT_PASSWORDS["supervisor"])
    pbs = client.get("/api/playbooks", headers=inv).json()["playbooks"]
    names = {p["name"] for p in pbs}
    assert "qr_refund_scam" in names and "collect_request" not in names
    assert all(p["warning_hi"] for p in pbs)                       # every scam has a Hindi warning
    assert client.post("/api/phone/respond", json={"choice": "talk"}, headers=inv).status_code == 409   # nothing waiting
    assert client.post("/api/phone/respond", json={"choice": "hack"}, headers=inv).status_code == 422
    client.post("/api/simulate/inject", json={"playbook": "digital_arrest", "victims": 6, "senior_first": True}, headers=inv)
    assert client.post("/api/phone/killswitch", json={"on": True}, headers=inv).json()["locked"] is True
    assert client.get("/api/phone", headers=inv).json()["locked"] is True
    client.post("/api/phone/killswitch", json={"on": False}, headers=inv)


def test_rollout_mode_and_password(client):
    inv = login(client, "investigator", config.DEFAULT_PASSWORDS["investigator"])
    sup = login(client, "supervisor", config.DEFAULT_PASSWORDS["supervisor"])
    assert client.post("/api/mode", json={"mode": "shadow"}, headers=inv).status_code == 403
    assert client.post("/api/mode", json={"mode": "shadow"}, headers=sup).json()["mode"] == "shadow"
    assert client.get("/api/status", headers=inv).json()["mode"] == "shadow"
    assert client.post("/api/mode", json={"mode": "full"}, headers=sup).status_code == 200
    r = client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "AnotherPass@2026"}, headers=inv)
    assert r.status_code == 403


def test_test_lab_file_scenario_and_single_payment(client):
    inv = login(client, "investigator", config.DEFAULT_PASSWORDS["investigator"])
    aud = login(client, "auditor", config.DEFAULT_PASSWORDS["auditor"])
    tpl = client.get("/api/lab/template", headers=aud)
    assert tpl.status_code == 200 and "timestamp,payer,payee,amount" in tpl.text
    # auditors may read but not run tests
    assert client.post("/api/lab/run", json={"csv": tpl.text}, headers=aud).status_code == 403
    r = client.post("/api/lab/run", json={"csv": tpl.text, "filename": "template.csv"}, headers=inv)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["summary"]["payments"] == 4 and res["metrics"]["scam_payments"] == 2
    assert res["privacy"]["example_stored"].startswith("acct_")
    # bad files are rejected with a clear message
    bad = client.post("/api/lab/run", json={"csv": "a,b\n1,2"}, headers=inv)
    assert bad.status_code == 400 and "Missing column" in bad.json()["detail"]
    sc = client.post("/api/lab/scenario", json={"kind": "task_scam", "victims": 4}, headers=inv)
    assert sc.status_code == 200, sc.text
    body = sc.json()
    assert body["metrics"]["scam_payments"] == 16 and body["csv"].startswith("event_id")
    one = client.post("/api/lab/score", json={"payer": "customer_01", "payee": "x", "amount": 9000,
                                              "call_active": 1}, headers=inv)
    assert one.status_code == 200 and one.json()["level"] in ("R0", "R1", "R2", "R2T", "R3")
    # the live demo is untouched by the lab
    assert client.get("/api/health").json()["runtime_ready"] is True
