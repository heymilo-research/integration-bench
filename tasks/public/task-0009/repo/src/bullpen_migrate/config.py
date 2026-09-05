"""Runtime configuration from the environment (COMPLETE)."""

from __future__ import annotations

import os
from pathlib import Path

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "./output"))
VENDOR_BASE_URL = os.environ.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/")

BP_CLIENT_ID = os.environ.get("BP_CLIENT_ID", "")
BP_CLIENT_SECRET = os.environ.get("BP_CLIENT_SECRET", "")
BP_APP_TOKEN = os.environ.get("BP_APP_TOKEN", "")

ENTITIES = ("candidates", "jobs", "applications")
