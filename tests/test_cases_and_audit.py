from cases.store import Store
from graph.emergence import is_bursting, poisson_tail


def test_audit_chain_detects_tampering(tmp_path):
    st = Store(tmp_path / "t.db")
    st.audit("alice", "investigator", "case_confirm", "C-0001", {"reason": ""})
    st.audit("bob", "supervisor", "case_exported", "C-0001", {})
    assert st.verify_audit()["valid"]
    with st.lock:
        st.db.execute("UPDATE audit SET actor='mallory' WHERE id=1")
        st.db.commit()
    res = st.verify_audit()
    assert not res["valid"] and res["broken_at"] == 1


def test_two_person_rule(tmp_path):
    st = Store(tmp_path / "t.db")
    st.request_suppression("acct_x", "C-1", "legitimate_business", "alice", 0)
    sid = st.suppressions()[0]["id"]
    try:
        st.decide_suppression(sid, "alice", True)
        assert False, "requester must not approve own request"
    except PermissionError:
        pass
    row = st.decide_suppression(sid, "bob", True)
    assert row["status"] == "approved" and row["approved_by"] == "bob"
    try:
        st.decide_suppression(sid, "carol", False)          # declining after approval is not allowed...
        assert False
    except ValueError:
        pass
    assert st.revoke_suppression(sid, "carol")["status"] == "revoked"   # ...revoke lifts it instead


def test_suppression_expires(tmp_path):
    st = Store(tmp_path / "t.db")
    st.request_suppression("acct_y", "C-2", "legitimate_business", "alice", 1000.0)
    sid = st.suppressions()[0]["id"]
    st.decide_suppression(sid, "bob", True)
    assert st.expire_suppressions(999.0) == []
    assert [r["id"] for r in st.expire_suppressions(1000.0)] == [sid]
    assert st.suppressions()[0]["status"] == "expired"


def test_labels_for_training_hold_back_young_labels(tmp_path):
    st = Store(tmp_path / "t.db")
    st.add_label("acct_z", "scam_victim", "investigator:alice", "C-3")
    out = st.labels_for_training(min_age_days=7)
    assert out["held_back"] == 1 and not out["eligible"]
    out = st.labels_for_training(min_age_days=7, now=__import__("time").time() + 8 * 86400)
    assert len(out["eligible"]) == 1


def test_default_users_and_login(tmp_path):
    st = Store(tmp_path / "t.db")
    created = st.ensure_default_users()
    assert set(created) == {"investigator", "supervisor", "auditor"}
    assert st.check_login("investigator", "wrong") is None


def test_burst_test():
    now = 100 * 3600.0
    quiet = [now - h * 3600 for h in (20, 15, 10)]
    burst = quiet + [now - 0.5 * 3600 * i for i in range(1, 7)]
    assert not is_bursting(quiet, now)
    assert is_bursting(burst, now)
    assert 0.0 <= poisson_tail(5, 1.0) < 0.01
