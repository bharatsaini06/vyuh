"""Central configuration for Vyuh.

Every tunable number lives here so that investigators, auditors and engineers
can see exactly which thresholds drive decisions. Values can be overridden with
environment variables or a `.env` file in the project root (see .env.example).
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Tiny .env reader (KEY=VALUE lines) so we need no extra dependency."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# Values edited in Windows Notepad can carry stray spaces or carriage returns;
# strip them so a password like "Investigator@123\r" still works.
for _k, _v in list(os.environ.items()):
    if _k.startswith("VYUH_") and _v != _v.strip():
        os.environ[_k] = _v.strip()


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# ---------------------------------------------------------------- paths
DATA_DIR = Path(os.environ.get("VYUH_DATA_DIR", ROOT / "data"))
MODEL_DIR = Path(os.environ.get("VYUH_MODEL_DIR", ROOT / "models" / "saved"))
REPORT_DIR = Path(os.environ.get("VYUH_REPORT_DIR", ROOT / "reports"))
PLAYBOOK_DIR = ROOT / "sim" / "playbooks"
FRONTEND_DIR = ROOT / "frontend"
DB_PATH = Path(os.environ.get("VYUH_DB_PATH", DATA_DIR / "vyuh.db"))

# ---------------------------------------------------------------- secrets
# SECRET_KEY signs login tokens and keys the pseudonymisation HMAC.
# It MUST be changed for any shared deployment (see docs).
SECRET_KEY = os.environ.get("VYUH_SECRET_KEY", "dev-only-insecure-key-change-me")
API_KEY = os.environ.get("VYUH_API_KEY", "dev-only-api-key-change-me")  # for bank system integration
TOKEN_TTL_HOURS = _env_int("VYUH_TOKEN_TTL_HOURS", 8)
DEFAULT_PASSWORDS = {
    "investigator": os.environ.get("VYUH_INVESTIGATOR_PASSWORD", "Investigator@123"),
    "supervisor": os.environ.get("VYUH_SUPERVISOR_PASSWORD", "Supervisor@123"),
    "auditor": os.environ.get("VYUH_AUDITOR_PASSWORD", "Auditor@123"),
}

# ---------------------------------------------------------------- simulation
SIM_START = datetime(2026, 8, 1, 0, 0, 0)
SIM_DAYS = _env_int("VYUH_SIM_DAYS", 60)
SIM_CUSTOMERS = _env_int("VYUH_SIM_CUSTOMERS", 8000)
SIM_SEED = _env_int("VYUH_SIM_SEED", 42)
TRAIN_DAYS = 30            # days 1-30 train, 31-60 test
WARMUP_DAYS = 10           # first days only build history (no training rows)
REPLAY_START_DAY = _env_int("VYUH_REPLAY_START_DAY", 41)
DEFAULT_SPEED = _env_int("VYUH_DEFAULT_SPEED", 300)  # simulated seconds per real second
VICTIM_COMPLIANCE = _env_float("VYUH_VICTIM_COMPLIANCE", 0.6)  # chance a warned victim stops

# ---------------------------------------------------------------- detection
LINK_MIN_SCORE = 0.30      # journeys at/above this join the linking graph
RISKY_SCORE = 0.50         # "high-risk journey"
R1_SCORE = _env_float("VYUH_R1_SCORE", 0.60)   # specific in-app warning
R2_SCORE = _env_float("VYUH_R2_SCORE", 0.85)   # cooling-off pause
R2_MIN_AMOUNT = 5000
R3_MIN_AMOUNT = 100000     # call-back request
PAUSE_MINUTES = 30
WARN_COOLDOWN_DAYS = 7
COLLECTOR_LINK_MIN = 0.40  # payee collector score needed for a shared-payee link
FINGERPRINT_SIM = 0.85     # cosine similarity for a fingerprint link
LINK_WINDOW_HOURS = 72
SLOW_PATH_EVERY_MIN = _env_int("VYUH_SLOW_PATH_EVERY_MIN", 2)   # simulated minutes between linking runs (Round 1 design)
EMERGENCE_EVERY_MIN = 60

LINK_WEIGHTS = {
    "shared_collector": 1.0,
    "shared_downstream": 0.7,
    "fingerprint": 0.5,
}
CROWD_BOOST = 0.6          # max extra risk from belonging to an active campaign

# Thresholds are jittered by up to +/- this amount per payment (deterministic per
# event id), so nobody can learn the exact boundary by probing with test payments.
THRESHOLD_JITTER = _env_float("VYUH_THRESHOLD_JITTER", 0.02)

# Rollout mode (Round 1 page 9): shadow -> analyst -> warnings -> full.
#   shadow    score and build cases; nothing reaches customers or investigators
#   analyst   cases go to investigators; customers see nothing
#   warnings  customers can get R1 warnings, no pauses
#   full      the whole ladder (R0-R3 automatic, R4/R5 human)
MODES = ("shadow", "analyst", "warnings", "full")
MODE = os.environ.get("VYUH_MODE", "full")

# ---------------------------------------------------------------- customer protections
# Trusted contact (RBI discussion paper, April 2026): a customer who opted in
# (in VyuhSim: some customers aged 60+) needs a family member to approve a risky
# payment of Rs 50,000 or more to a new payee. Age is a customer SETTING here,
# never a model feature.
TRUSTED_MIN_AMOUNT = 50000
TRUSTED_OPTIN_SHARE = 0.5          # share of 60+ customers who opted in (simulation)
TRUSTED_DECLINE = 0.85             # simulated: trusted contact refuses a scam payment
CALL_HOLD_SECONDS = 30             # extra hold before "Continue" while a call is active

# Suspect Registry / mule-flag feed and 1930 complaints (simulated, see sim/scams.py)
COMPLAINT_SHARE = 0.55             # share of victims who complain on 1930 / NCRP
REGISTRY_DELAY_HOURS = (2, 24)     # complaint -> account appears in the registry feed
REGISTRY_NOISE_SHARE = 0.003       # genuine accounts wrongly reported (honesty check)
LABEL_MIN_AGE_DAYS = 7             # labels younger than this never go into retraining

# ---------------------------------------------------------------- policy simulator (evaluation)
RBI_DELAY_MIN_AMOUNT = 10000       # RBI discussion paper: 1-hour delay for transfers >= Rs 10,000
RBI_DELAY_HOURS = 1
HYBRID_MIN_RISK = 0.30             # hybrid: >= Rs 10,000 waits only if Vyuh risk >= this
REALISTIC_SCAM_RATE = 1e-4         # assumed scam share of payments in a real bank (for re-weighting)

# ---------------------------------------------------------------- cases / alert fatigue
CASE_MIN_MEMBERS = 3
CASE_MIN_CONFIDENCE = _env_float("VYUH_CASE_MIN_CONFIDENCE", 0.70)   # Round 1 design value
CASE_MIN_EVIDENCE_TYPES = 2
LONE_VICTIM_SCORE = 0.90
LONE_VICTIM_SAVEABLE = 200000
ANALYST_CAPACITY = _env_int("VYUH_ANALYST_CAPACITY", 40)
HARD_FLOOR_SAVEABLE = 500000
MATERIAL_GROWTH = 0.5      # re-notify when victims grow by 50%
SAVEABLE_BAND = 100000
SUPPRESSION_DAYS = 30

# Words in UPI remarks that public advisories associate with scams.
RISKY_REMARKS = {"task", "fee", "vip", "verification", "rbi", "customs", "clearance",
                 "invest", "ipo", "tax", "kyc", "refund", "unlock", "penalty", "bail", "recovery"}


def sim_day_start(day: int) -> datetime:
    """Datetime at the start of simulation day `day` (1-based)."""
    from datetime import timedelta
    return SIM_START + timedelta(days=day - 1)


def ensure_dirs() -> None:
    for d in (DATA_DIR, MODEL_DIR, REPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
