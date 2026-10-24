"""Test Lab CSV reading: flexible column names, clear errors, labels."""
import pytest

from api.lab import parse_csv, template_csv


def test_template_round_trip():
    rows, problems = parse_csv(template_csv())
    assert len(rows) == 4 and not problems
    assert [r["label"] for r in rows] == [0, 0, 1, 1]
    assert rows[3]["call"] == 1 and rows[3]["paste"] == 1


def test_aliases_dates_and_bad_rows():
    text = ("Transaction ID,Time,From,To,Amount (INR),Is Fraud\n"
            "t1,24/10/2026 10:15,a,b,\"1,500\",no\n"
            "t2,2026-10-24T10:20:00,a,c,0,yes\n"           # amount 0 -> skipped
            "t3,not a date,a,c,100,yes\n")                 # bad time -> skipped
    with pytest.raises(ValueError):
        parse_csv("x,y\n1,2\n")
    rows, problems = parse_csv(text.replace("Amount (INR)", "amount"))
    assert len(rows) == 1 and rows[0]["amount"] == 1500 and rows[0]["label"] == 0
    assert len(problems) == 2
