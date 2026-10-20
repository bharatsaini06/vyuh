from core.security import (create_token, hash_password, pseudonymise, verify_password,
                           verify_token)


def test_pseudonymise_is_deterministic_and_keyed():
    a = pseudonymise("CUST000001", key="k1")
    assert a == pseudonymise("CUST000001", key="k1")
    assert a != pseudonymise("CUST000001", key="k2")          # different key -> different token
    assert a != pseudonymise("CUST000002", key="k1")
    assert a.startswith("acct_") and "CUST" not in a          # raw id never leaks


def test_password_hash_roundtrip():
    h = hash_password("S3cret-pass!")
    assert verify_password("S3cret-pass!", h)
    assert not verify_password("wrong", h)
    assert "S3cret" not in h


def test_token_sign_and_tamper():
    tok = create_token("alice", "investigator")
    payload = verify_token(tok)
    assert payload["sub"] == "alice" and payload["role"] == "investigator"
    body, sig = tok.split(".")
    forged = body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + sig
    assert verify_token(forged) is None


def test_expired_token_rejected():
    tok = create_token("bob", "auditor", ttl_hours=-1)
    assert verify_token(tok) is None
