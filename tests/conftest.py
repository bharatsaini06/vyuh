"""Shared test setup.

All tests run against a temporary folder (set below BEFORE anything imports
core.config), so running the tests never touches your real data/ or models/.
The end-to-end API test builds a small synthetic world there (about a minute).
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="vyuh_test_"))
os.environ["VYUH_DATA_DIR"] = str(_TMP / "data")
os.environ["VYUH_MODEL_DIR"] = str(_TMP / "models")
os.environ["VYUH_REPORT_DIR"] = str(_TMP / "reports")
os.environ["VYUH_DB_PATH"] = str(_TMP / "data" / "vyuh.db")
os.environ["VYUH_SIM_CUSTOMERS"] = "1500"
os.environ["VYUH_SECRET_KEY"] = "test-secret"
os.environ["VYUH_API_KEY"] = "test-api-key"
