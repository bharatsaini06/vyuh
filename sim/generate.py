"""Generate the VyuhSim dataset.

Run from the project root:
    python -m sim.generate                    # default world (8,000 customers, 60 days)
    python -m sim.generate --customers 2000   # smaller, faster
    python -m sim.generate --adversary all --out data/adversarial

Files written (in --out):
    accounts.csv        what a bank knows about each account (no labels)
    events.csv          the payment/account event stream (no labels)
    event_labels.csv    which events belong to a scam (evaluation only)
    ground_truth.csv    victims, collectors, layers per campaign (evaluation only)
    personas.csv        simulator persona per customer (fairness checks only)
    suspect_registry.csv  Suspect-Registry / mule-flag style feed: account + time it was
                        listed (from victims' complaints, plus a few wrongly reported
                        genuine accounts). A bank would receive this from I4C / MuleHunter.
    complaints.csv      1930 / NCRP complaints: victim, time, accounts paid. Arrive hours
                        to days late; used as delayed labels and shown on cases.
    sim_world.pkl       world snapshot used for live campaign injection
"""
from __future__ import annotations

import argparse
import csv
import pickle
import random
import time
from datetime import timedelta
from pathlib import Path

from core import config
from sim.scams import ADVERSARY_MODES, ScamFactory, load_playbooks, schedule_campaigns
from sim.world import World

EVENT_COLUMNS = ["event_id", "ts", "type", "src", "dst", "amount", "rail", "remark", "is_collect",
                 "call_active", "screen_share", "remote_app", "paste_payee", "session_sec", "payee_fri"]


def generate(out_dir: Path, customers: int, days: int, seed: int, adversary: set) -> dict:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    world = World(customers, config.SIM_START, days, seed)
    world.generate_normal()
    factory = ScamFactory(world, load_playbooks(), random.Random(seed + 1))
    campaigns = schedule_campaigns(factory, config.SIM_START, days, adversary=adversary)

    end = config.SIM_START + timedelta(days=days)
    rows = [e for e in world.events if config.SIM_START <= e[0] < end]
    rows.sort(key=lambda e: e[0])

    with open(out_dir / "events.csv", "w", newline="", encoding="utf-8") as fe, \
            open(out_dir / "event_labels.csv", "w", newline="", encoding="utf-8") as fl:
        we, wl = csv.writer(fe), csv.writer(fl)
        we.writerow(EVENT_COLUMNS)
        wl.writerow(["event_id", "campaign_id", "playbook", "stage", "role"])
        n_scam = 0
        for i, e in enumerate(rows):
            eid = f"E{i:08d}"
            we.writerow([eid, e[0].isoformat(timespec="seconds")] + list(e[1:14]))
            if e[14]:
                wl.writerow([eid, e[14], e[15], e[16], e[17]])
                n_scam += 1

    with open(out_dir / "accounts.csv", "w", newline="", encoding="utf-8") as fa:
        wa = csv.writer(fa)
        wa.writerow(["account_id", "holder_type", "opened_at", "merchant_category", "age_band", "home_state",
                     "trusted_contact"])
        for a in world.accounts.values():
            wa.writerow([a.account_id, a.holder_type, a.opened_at.isoformat(timespec="seconds"),
                         a.merchant_category, a.age_band, a.home_state, a.trusted_contact])

    write_signals(out_dir, world, factory, seed, end)

    with open(out_dir / "ground_truth.csv", "w", newline="", encoding="utf-8") as fg:
        wg = csv.writer(fg)
        wg.writerow(["account_id", "role", "campaign_id", "playbook"])
        wg.writerows(factory.truth)

    with open(out_dir / "personas.csv", "w", newline="", encoding="utf-8") as fp:
        wp = csv.writer(fp)
        wp.writerow(["account_id", "persona"])
        wp.writerows(world.persona_of.items())

    world.events = []   # keep the snapshot small
    with open(out_dir / "sim_world.pkl", "wb") as fw:
        pickle.dump({"world": world, "used": factory.used, "truth": factory.truth}, fw)

    summary = {"events": len(rows), "scam_events": n_scam, "accounts": len(world.accounts),
               "campaigns": len(campaigns),
               "victims": sum(len(c.victims) for c in campaigns),
               "seconds": round(time.time() - t0, 1)}
    return summary


def write_signals(out_dir: Path, world, factory, seed: int, end) -> None:
    """Write the delayed outside signals: 1930 complaints and the registry feed."""
    rn = random.Random(seed + 13)
    noise = []
    custs = sorted(world.customers)
    for cid in rn.sample(custs, k=int(len(custs) * config.REGISTRY_NOISE_SHARE)):
        ts = config.SIM_START + timedelta(days=rn.uniform(0, config.SIM_DAYS))
        noise.append((ts, cid, "complaint (disputed - account is genuine)"))
    first: dict = {}
    for ts, acc, src in list(factory.registry) + noise:
        if acc not in first or ts < first[acc][0]:
            first[acc] = (ts, src)
    with open(out_dir / "suspect_registry.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["listed_at", "account_id", "source"])
        for acc, (ts, src) in sorted(first.items(), key=lambda kv: kv[1][0]):
            if ts < end:
                w.writerow([ts.isoformat(timespec="seconds"), acc, src])
    with open(out_dir / "complaints.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["complained_at", "victim_id", "paid_accounts", "campaign_id", "playbook"])
        for row in sorted(factory.complaints, key=lambda r: r[0]):
            if row[0] < end:
                w.writerow([row[0].isoformat(timespec="seconds")] + list(row[1:]))


def main() -> None:
    p = argparse.ArgumentParser(description="Generate the synthetic VyuhSim dataset")
    p.add_argument("--out", default=str(config.DATA_DIR), help="output folder")
    p.add_argument("--customers", type=int, default=config.SIM_CUSTOMERS)
    p.add_argument("--days", type=int, default=config.SIM_DAYS)
    p.add_argument("--seed", type=int, default=config.SIM_SEED)
    p.add_argument("--adversary", default="",
                   help="comma list of attack modes for test-period campaigns: "
                        + ",".join(sorted(ADVERSARY_MODES)) + " or 'all'")
    a = p.parse_args()
    adv = set(ADVERSARY_MODES) if a.adversary == "all" else {m for m in a.adversary.split(",") if m}
    unknown = adv - ADVERSARY_MODES
    if unknown:
        raise SystemExit(f"Unknown adversary mode(s): {unknown}")
    print(f"Generating world: {a.customers} customers, {a.days} days, seed {a.seed}, adversary={sorted(adv) or 'none'}")
    s = generate(Path(a.out), a.customers, a.days, a.seed, adv)
    print(f"Done in {s['seconds']}s: {s['events']:,} events ({s['scam_events']:,} scam-related), "
          f"{s['accounts']:,} accounts, {s['campaigns']} campaigns, {s['victims']} victims -> {a.out}")


if __name__ == "__main__":
    main()
