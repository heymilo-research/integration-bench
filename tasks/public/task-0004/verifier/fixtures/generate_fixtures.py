#!/usr/bin/env python3
"""Regenerate verifier/fixtures/{roster_uncontested,corrections}.json from
REAL vendor behavior.

What this does:
  1. Applies solution.patch to a throwaway copy of ../../repo (the starter)
     to get the gold connector.
  2. Boots StaffLine and Placemint as real uvicorn processes on EPHEMERAL
     localhost ports (never 8000/8001/8080 -- this machine may have a live
     grading queue using those), with this task's exact env config
     (StaffLine CHECKPOINT=1 / VENDOR_SEED=3000; Placemint CHECKPOINT=3 /
     VENDOR_SEED=5090 / DATASET_SIZE=400).
  3. Runs the gold connector's `merge` then `correct` against them.
  4. Extracts the uncontested slice (candidates with zero Placemint name
     match) and the 15 named disputed candidates' correction results, and
     diffs them against the checked-in fixtures below.

Requires the canonical StaffLine and Placemint source trees in this monorepo
(this is an authoring script, not something the harness runs at grade time).
`VENDORS_ROOT` may override the monorepo `vendors/` path.

Usage:
    python3 generate_fixtures.py [--write]

Without --write, only reports whether the live output matches the checked-in
fixtures. With --write, overwrites them.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parents[2]
FIXTURES_DIR = TASK_DIR / "verifier" / "fixtures"
REPO_DIR = TASK_DIR / "repo"
SOLUTION_PATCH = TASK_DIR / "authoring" / "solution.patch"

VENDORS_ROOT = Path(
    os.environ.get(
        "VENDORS_ROOT",
        str(TASK_DIR.parents[2] / "vendors"),
    )
)
STAFFLINE_SRC = VENDORS_ROOT / "staffline" / "src"
PLACEMINT_SRC = VENDORS_ROOT / "placemint" / "src"

DISPUTED_CANDIDATE_IDS = [
    "cand_0014", "cand_0027", "cand_0043", "cand_0060", "cand_0064",
    "cand_0072", "cand_0091", "cand_0092", "cand_0106", "cand_0120",
    "cand_0121", "cand_0125", "cand_0129", "cand_0132", "cand_0145",
]


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_healthy(port: int, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"vendor on port {port} never became healthy")


def _prepare_gold_repo(workdir: Path) -> Path:
    import shutil

    # solution.patch's paths are "a/repo/..." / "b/repo/..." (it must
    # `git apply --check` cleanly from the task dir, which contains a `repo/`
    # directory) -- so the copy must ALSO be named `repo`, one level below
    # cwd, for `-p1` (strip only the leading `a/`/`b/`) to resolve correctly.
    gold_repo = workdir / "repo"
    shutil.copytree(REPO_DIR, gold_repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    subprocess.run(
        ["git", "apply", "--unsafe-paths", "-p1", str(SOLUTION_PATCH)],
        cwd=workdir, check=True,
    )
    return gold_repo


def _launch_staffline(log_dir: Path) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(STAFFLINE_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env.update({
        "PORT": str(port),
        "CHECKPOINT": "1",
        "VENDOR_SEED": "3000",
        "SL_APP_TOKEN": "sl-test-app-token",
        "SL_HMAC_SECRET": "sl-test-hmac-secret",
        "REQUEST_LOG_PATH": str(log_dir / "sl_requests.jsonl"),
        "TOKEN_LOG_PATH": str(log_dir / "sl_tokens.jsonl"),
    })
    proc = subprocess.Popen(
        [sys.executable, "-c", "from staffline.__main__ import main; main()"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return proc, port


def _launch_placemint(log_dir: Path) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PLACEMINT_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env.update({
        "PORT": str(port),
        "CHECKPOINT": "3",
        "VENDOR_SEED": "5090",
        "DATASET_SIZE": "400",
        "PM_CLIENT_ID": "pm-test-client-id",
        "PM_CLIENT_SECRET": "pm-test-client-secret",
        "REQUEST_LOG_PATH": str(log_dir / "pm_requests.jsonl"),
        "TOKEN_LOG_PATH": str(log_dir / "pm_tokens.jsonl"),
    })
    proc = subprocess.Popen(
        [sys.executable, "-c", "from placemint import run; run()"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return proc, port


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="overwrite the checked-in fixtures")
    parser.add_argument("--workdir", default=None, help="scratch dir (defaults to a tempdir)")
    args = parser.parse_args()

    import tempfile

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="task0004_fixturegen_"))
    workdir.mkdir(parents=True, exist_ok=True)
    log_dir = workdir / "logs"
    out_dir = workdir / "output"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    gold_repo = _prepare_gold_repo(workdir)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(gold_repo)], check=True,
    )

    sl_proc, sl_port = _launch_staffline(log_dir)
    pm_proc, pm_port = _launch_placemint(log_dir)
    try:
        _wait_healthy(sl_port)
        _wait_healthy(pm_port)

        env = dict(os.environ)
        env.update({
            "STAFFLINE_BASE_URL": f"http://127.0.0.1:{sl_port}",
            "SL_APP_TOKEN": "sl-test-app-token",
            "SL_HMAC_SECRET": "sl-test-hmac-secret",
            "PLACEMINT_BASE_URL": f"http://127.0.0.1:{pm_port}",
            "PM_CLIENT_ID": "pm-test-client-id",
            "PM_CLIENT_SECRET": "pm-test-client-secret",
            "OUTPUT_DIR": str(out_dir),
        })

        for cmd in ("merge", "correct"):
            r = subprocess.run(
                [sys.executable, "-m", "staffline_placemint_merge", cmd],
                env=env, capture_output=True, text=True,
            )
            print(f"{cmd} exit={r.returncode}")
            if r.returncode != 0:
                print(r.stdout[-4000:], r.stderr[-4000:])
                return 1

        roster = json.loads((out_dir / "roster.json").read_text())
        corrections = json.loads((out_dir / "corrections.json").read_text())
    finally:
        sl_proc.terminate()
        pm_proc.terminate()
        sl_proc.wait(timeout=5)
        pm_proc.wait(timeout=5)

    live_uncontested = sorted(
        (r for r in roster if r["source_of_truth"] == "staffline"), key=lambda r: r["source_id"]
    )
    live_disputed_corrections = sorted(
        (c for c in corrections if c["candidate_id"] in DISPUTED_CANDIDATE_IDS),
        key=lambda c: c["candidate_id"],
    )

    uncontested_path = FIXTURES_DIR / "roster_uncontested.json"
    corrections_path = FIXTURES_DIR / "corrections.json"

    checked_in_uncontested = json.loads(uncontested_path.read_text())
    checked_in_corrections = json.loads(corrections_path.read_text())

    uncontested_match = live_uncontested == checked_in_uncontested
    corrections_match = live_disputed_corrections == checked_in_corrections
    print(f"roster_uncontested.json matches live output: {uncontested_match}")
    print(f"corrections.json matches live output:        {corrections_match}")
    print(f"live uncontested rows: {len(live_uncontested)}, disputed corrections: {len(live_disputed_corrections)}")

    if args.write:
        uncontested_path.write_text(json.dumps(live_uncontested, indent=2) + "\n")
        corrections_path.write_text(json.dumps(live_disputed_corrections, indent=2) + "\n")
        print("wrote fixtures.")

    return 0 if (uncontested_match and corrections_match) or args.write else 2


if __name__ == "__main__":
    raise SystemExit(main())
