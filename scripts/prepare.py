"""One command to prepare everything after installing the requirements.

    python -m scripts.prepare            # generate data, train, build warm state, evaluate
    python -m scripts.prepare --quick    # smaller world (3,000 customers) for slow laptops
    python -m scripts.prepare --skip-eval

Steps (each prints what it is doing):
  1. Generate the synthetic dataset           -> data/
  2. Train the models on days 1-30             -> models/saved/
  3. Build the replay warm state               -> data/warm_state.pkl
  4. Evaluate against baselines (days 31-60)   -> reports/evaluation.md
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def write_reference(config) -> None:
    """Copy the fresh evaluation into docs/REFERENCE_RESULTS.md so the README numbers
    always come from a run on THIS machine."""
    from datetime import datetime
    src = config.REPORT_DIR / "evaluation.md"
    if not src.exists():
        return
    head = (f"<!-- Reference run: {config.SIM_CUSTOMERS:,} customers, seed {config.SIM_SEED}, "
            f"written {datetime.now():%Y-%m-%d %H:%M} by scripts/prepare.py. Re-create with: python -m scripts.prepare --force -->\n")
    (config.ROOT / "docs" / "REFERENCE_RESULTS.md").write_text(head + src.read_text(encoding="utf-8"), encoding="utf-8")
    print("Reference results written to docs/REFERENCE_RESULTS.md")


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare Vyuh: data, models, warm state, evaluation")
    ap.add_argument("--quick", action="store_true", help="3,000 customers instead of 8,000")
    ap.add_argument("--skip-eval", action="store_true", help="skip the evaluation step")
    ap.add_argument("--skip-adversarial", action="store_true", help="evaluate only the clean test period")
    ap.add_argument("--force", action="store_true", help="regenerate data even if it exists")
    a = ap.parse_args()
    if a.quick:
        os.environ["VYUH_SIM_CUSTOMERS"] = "3000"

    from core import config
    config.ensure_dirs()
    t_all = time.time()

    print("\n=== Step 1/4: generate synthetic data ===")
    old_format = (config.DATA_DIR / "events.csv").exists() and not (config.DATA_DIR / "suspect_registry.csv").exists()
    if old_format and not a.force:
        print("data/ was made by an older Vyuh version - regenerating it")
        a.force = True
    if (config.DATA_DIR / "events.csv").exists() and not a.force:
        print("data/events.csv already exists - keeping it (use --force to regenerate)")
    else:
        from sim.generate import generate
        s = generate(config.DATA_DIR, config.SIM_CUSTOMERS, config.SIM_DAYS, config.SIM_SEED, set())
        print(f"{s['events']:,} events, {s['campaigns']} scam campaigns, {s['victims']} victims ({s['seconds']}s)")
        for stale in ("warm_state.pkl", "vyuh.db"):
            p = config.DATA_DIR / stale
            if p.exists():
                p.unlink()
        adv = config.DATA_DIR / "adversarial" / "events.csv"
        if adv.exists():
            adv.unlink()          # the evaluation regenerates the adversarial world to match

    print("\n=== Step 2/4: train models (takes a few minutes) ===")
    from models.train import train
    meta = train()
    print("journey model validation:", meta["metrics"]["journey"])

    print("\n=== Step 3/4: build replay warm state ===")
    from api.runtime import build_warm_state
    build_warm_state()

    if not a.skip_eval:
        print("\n=== Step 4/4: evaluate against baselines ===")
        sys.argv = ["eval.evaluate"] + (["--skip-adversarial"] if a.skip_adversarial else [])
        from eval.evaluate import main as eval_main
        eval_main()
        write_reference(config)
    print(f"\nAll done in {time.time() - t_all:.0f}s. Start the app with:\n"
          "    uvicorn api.main:app --port 8000\n"
          "and open http://127.0.0.1:8000")


if __name__ == "__main__":
    main()
