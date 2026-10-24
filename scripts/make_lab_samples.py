"""Make ready-to-upload Test Lab files from the VyuhSim test month.

    python -m scripts.make_lab_samples

Writes CSV files to samples/. Each file contains every payment of the chosen
customers in a time window: real scam victims (from chosen campaigns), the
receiving (mule) accounts' onward transfers, and a random set of ordinary
customers. A `label` column says scam / genuine, so the Test Lab can report
accuracy, false positives and every miss.

Only the TEST period (days 31-60) is used: the models never trained on it.
Standard library only, so it runs anywhere the data folder exists.
"""
from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = ["event_id", "timestamp", "payer", "payee", "amount", "type", "remark", "on_call", "screen_share",
          "remote_app", "pasted_payee", "session_seconds", "payee_fri", "is_collect", "payee_type",
          "payee_account_age_days", "payer_age_band", "trusted_contact", "label"]


def load(data: Path):
    accounts = {r["account_id"]: r for r in csv.DictReader(open(data / "accounts.csv", encoding="utf-8"))}
    labels = {r["event_id"]: r for r in csv.DictReader(open(data / "event_labels.csv", encoding="utf-8"))}
    roles = defaultdict(set)
    for r in csv.DictReader(open(data / "ground_truth.csv", encoding="utf-8")):
        roles[r["account_id"]].add((r["role"], r["campaign_id"]))
    events = list(csv.DictReader(open(data / "events.csv", encoding="utf-8")))
    return accounts, labels, roles, events


def campaign_info(events, labels):
    first, last, victims, playbook = {}, {}, defaultdict(set), {}
    for e in events:
        lab = labels.get(e["event_id"])
        if lab and lab["role"] == "victim_payment":
            c = lab["campaign_id"]
            first.setdefault(c, e["ts"])
            last[c] = e["ts"]
            victims[c].add(e["src"])
            playbook[c] = lab["playbook"]
    return first, last, victims, playbook


def write(path: Path, about: str, rows, accounts, labels, keep_payers, scam_label_ok=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    n_scam = n_gen = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# {about}\n")
        f.write("# Synthetic VyuhSim test-month data (never used for training). label: scam / genuine / blank.\n")
        w = csv.writer(f)
        w.writerow(HEADER)
        for e in rows:
            lab = labels.get(e["event_id"])
            if lab and lab["role"] == "victim_payment" and scam_label_ok:
                label = "scam"
            elif lab:
                label = ""                       # mule forwards, trust credits, funding: not a customer decision
            elif e["src"] in keep_payers and e["type"] == "txn":
                label = "genuine"
            else:
                label = ""
            n_scam += label == "scam"
            n_gen += label == "genuine"
            payee = accounts.get(e["dst"], {})
            payer = accounts.get(e["src"], {})
            t = datetime.fromisoformat(e["ts"])
            age = ""
            if payee.get("opened_at"):
                age = max(0, (t - datetime.fromisoformat(payee["opened_at"])).days)
            w.writerow([e["event_id"], t.strftime("%Y-%m-%d %H:%M:%S"), e["src"], e["dst"], e["amount"], e["type"],
                        e["remark"], e["call_active"], e["screen_share"], e["remote_app"], e["paste_payee"],
                        e["session_sec"], e["payee_fri"], e["is_collect"], payee.get("holder_type", ""), age,
                        payer.get("age_band", ""), payer.get("trusted_contact", "0") or "0", label])
    print(f"{path.name}: {len(rows):,} rows, {n_scam} scam, {n_gen:,} genuine")


def build(data: Path, out: Path, test_start: str, n_genuine: int, seed: int) -> None:
    accounts, labels, roles, events = load(data)
    first, last, victims, playbook = campaign_info(events, labels)
    rng = random.Random(seed)
    in_test = [c for c in first if first[c] >= test_start]
    collectors = defaultdict(set)
    for acct, rs in roles.items():
        for role, cid in rs:
            if role != "victim":
                collectors[cid].add(acct)
    involved = set(roles)
    ordinary = sorted(a for a, r in accounts.items()
                      if r["holder_type"] == "individual" and a not in involved and a.startswith("CUST"))

    def pick(pb_list):
        chosen = []
        for pb in pb_list:
            cs = sorted((c for c in in_test if playbook[c] == pb), key=lambda c: first[c])
            if cs:
                chosen.append(cs[0])
        return chosen

    def window_rows(camps, genuine, days_before=7, days_after=1):
        t0 = min(datetime.fromisoformat(first[c]) for c in camps) - timedelta(days=days_before)
        t1 = max(datetime.fromisoformat(last[c]) for c in camps) + timedelta(days=days_after)
        a, b = t0.isoformat(), t1.isoformat()
        people = set(genuine) | {v for c in camps for v in victims[c]} | {x for c in camps for x in collectors[c]}
        return [e for e in events if a <= e["ts"] < b and (e["src"] in people or e["dst"] in people)], \
            set(genuine) | {v for c in camps for v in victims[c]}

    # 1. mixed: three known scam types + ordinary customers
    camps = pick(["digital_arrest", "task_scam", "kyc_remote_access"])
    gen = rng.sample(ordinary, min(n_genuine, len(ordinary)))
    if camps:
        rows, payers = window_rows(camps, gen)
        write(out / "sample_1_mixed_scams.csv",
              f"Mixed: {', '.join(playbook[c].replace('_', ' ') for c in camps)} campaigns + {len(gen)} ordinary "
              f"customers. Tip: history days = 7.", rows, accounts, labels, payers)

    # 2. the scam type the models never saw in training
    camps = pick(["digital_arrest_loan"])
    gen = rng.sample(ordinary, min(n_genuine // 2, len(ordinary)))
    if camps:
        rows, payers = window_rows(camps, gen)
        write(out / "sample_2_unseen_scam_type.csv",
              "Unseen scam type: digital arrest + instant loan (held out of training) + ordinary customers. "
              "Tip: history days = 7.", rows, accounts, labels, payers)

    # 3. genuine only: how many honest payments get disturbed?
    gen = rng.sample(ordinary, min(n_genuine, len(ordinary)))
    t0 = datetime.fromisoformat(test_start)
    a, b = t0.isoformat(), (t0 + timedelta(days=10)).isoformat()
    gset = set(gen)
    rows = [e for e in events if a <= e["ts"] < b and e["src"] in gset]
    write(out / "sample_3_genuine_only.csv",
          f"Genuine only: {len(gen)} ordinary customers, 10 days, no scams. Measures false positives. "
          f"Tip: history days = 7.", rows, accounts, labels, gset)

    # 4. scammers adapting (all seven attack modes), if the adversarial run exists
    adv = data / "adversarial"
    if (adv / "events.csv").exists():
        a_acc, a_lab, a_roles, a_events = load(adv)
        f2, l2, v2, p2 = campaign_info(a_events, a_lab)
        in2 = sorted((c for c in f2 if f2[c] >= test_start), key=lambda c: f2[c])[:3]
        col2 = defaultdict(set)
        for acct, rs in a_roles.items():
            for role, cid in rs:
                if role != "victim":
                    col2[cid].add(acct)
        ord2 = sorted(a for a, r in a_acc.items() if r["holder_type"] == "individual" and a not in a_roles
                      and a.startswith("CUST"))
        gen = rng.sample(ord2, min(n_genuine // 2, len(ord2)))
        if in2:
            t0 = min(datetime.fromisoformat(f2[c]) for c in in2) - timedelta(days=7)
            t1 = max(datetime.fromisoformat(l2[c]) for c in in2) + timedelta(days=1)
            people = set(gen) | {v for c in in2 for v in v2[c]} | {x for c in in2 for x in col2[c]}
            rows = [e for e in a_events if t0.isoformat() <= e["ts"] < t1.isoformat()
                    and (e["src"] in people or e["dst"] in people)]
            write(out / "sample_4_scammers_adapting.csv",
                  f"Scammers adapting (new account per payment, random amounts, no tell-tale words): "
                  f"{', '.join(p2[c].replace('_', ' ') for c in in2)} + ordinary customers. Tip: history days = 7.",
                  rows, a_acc, a_lab, set(gen) | {v for c in in2 for v in v2[c]})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "samples"))
    ap.add_argument("--test-start", default="2026-08-31T00:00:00", help="first day of the test month")
    ap.add_argument("--genuine", type=int, default=250, help="ordinary customers per file")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    build(Path(a.data), Path(a.out), a.test_start, a.genuine, a.seed)


if __name__ == "__main__":
    main()
