"""Golden scenario: a hand-written task-scam victim must produce the expected
stages and features. If someone breaks the tagger or features, this fails."""
from datetime import datetime, timedelta

from pipeline.engine import Engine
from pipeline.events import AccountInfo, Event
from pipeline.features import JOURNEY_FEATURES, fingerprint, journey_features

T0 = datetime(2026, 9, 1, 10, 0).timestamp()
START = T0 - 40 * 86400
IDX = {n: i for i, n in enumerate(JOURNEY_FEATURES)}


def _accounts():
    return {
        "victim": AccountInfo("individual", T0 - 900 * 86400),
        "friend": AccountInfo("individual", T0 - 900 * 86400),
        "shop": AccountInfo("business", T0 - 900 * 86400, "grocery"),
        "mule1": AccountInfo("individual", T0 - 10 * 86400),
        "mule2": AccountInfo("individual", T0 - 12 * 86400),
    }


def _ev(i, hours, typ, src, dst, amt, call=0, remark=""):
    return Event(f"e{i}", T0 + hours * 3600, typ, src, dst, amt, "UPI", remark, 0, call, 0, 0, 1, 120, 0)


def _engine_with_history():
    eng = Engine(_accounts(), None, START)
    # 30 days of normal life: shop and friend payments
    for d in range(30):
        eng.process(Event(f"h{d}", START + d * 86400 + 3600 * 12, "txn", "victim", "shop", 300.0), score=False)
        if d % 5 == 0:
            eng.process(Event(f"f{d}", START + d * 86400 + 3600 * 13, "txn", "victim", "friend", 500.0), score=False)
    return eng


def test_task_scam_journey_features():
    eng = _engine_with_history()
    steps = [
        _ev(1, 0.0, "txn", "mule2", "victim", 150),              # S3 stranger credit
        _ev(2, 1.0, "txn", "victim", "mule1", 1000, remark="task"),
        _ev(3, 1.5, "txn", "mule2", "victim", 1300),             # refund trick
        _ev(4, 3.0, "txn", "victim", "mule1", 3000, call=1),
    ]
    for e in steps:
        eng.process(e, score=False)
    s = eng.get("victim")
    stage_tags = [t for h in s.hist for t in h[14]]
    assert any(t.startswith("S3:stranger_credit") for t in stage_tags)
    assert any(t.startswith("S3:refund") for t in stage_tags)
    assert any(t.startswith("S4") for t in stage_tags)
    assert any(t.startswith("S5:escalation") for t in stage_tags)

    nxt = _ev(5, 5.0, "txn", "victim", "mule1", 9000, call=1, remark="vip")
    jf = journey_features(nxt, s, _accounts()["mule1"], START)
    assert jf[IDX["payee_fresh"]] == 1.0
    assert jf[IDX["ladder_len"]] >= 3                      # 1000 -> 3000 -> 9000
    assert jf[IDX["ladder_max_ratio"]] >= 2.9
    assert jf[IDX["stranger_credits_7d"]] >= 1
    assert jf[IDX["refund_credits_72h"]] >= 1
    assert jf[IDX["remark_risky"]] == 1.0
    assert jf[IDX["stage_count"]] >= 3

    fp = fingerprint(s, nxt.t)
    assert fp[0] == 1.0          # trust credit present
    assert fp[14] == 1.0         # refund present
    assert fp.sum() > 0


def test_normal_customer_has_quiet_features():
    eng = _engine_with_history()
    s = eng.get("victim")
    ev = _ev(9, 2.0, "txn", "victim", "shop", 350)
    jf = journey_features(ev, s, _accounts()["shop"], START)
    assert jf[IDX["payee_fresh"]] == 0.0      # long-known shop (seen during warm-up)
    assert jf[IDX["fresh_debits_72h"]] == 0
    assert jf[IDX["stranger_credits_7d"]] == 0


def test_evaluate_does_not_change_state():
    eng = _engine_with_history()
    before = len(eng.get("victim").hist)
    eng.evaluate(_ev(10, 1.0, "txn", "victim", "mule1", 5000))
    assert len(eng.get("victim").hist) == before


def test_registry_flag_only_counts_past_listings():
    from pipeline.features import COLLECTOR_FEATURES, collector_features
    from pipeline.state import AcctState
    i = COLLECTOR_FEATURES.index("registry_flag")
    s = AcctState(T0 - 86400)
    assert collector_features(s, None, T0, lambda _t: None, None)[i] == 0.0
    assert collector_features(s, None, T0, lambda _t: None, T0 + 3600)[i] == 0.0     # listed later: no leak
    assert collector_features(s, None, T0, lambda _t: None, T0 - 3600)[i] == 1.0
