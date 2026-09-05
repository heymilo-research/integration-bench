import csv
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


def _get(base: str, token: str, offset: int, limit: int) -> dict:
    req = Request(
        f"{base.rstrip('/')}/v1/workers?offset={offset}&limit={limit}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def _crawl(base: str, token: str) -> list[dict]:
    rows = []
    for offset in range(0, 10000, 10):
        page = _get(base, token, offset, 10)
        rows.extend(page.get("data") or [])
        if len(page.get("data") or []) < page.get("limit", 10):
            break
    return rows


def _watermark(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("last_worker_id")
    except (OSError, ValueError, AttributeError):
        return ""
    return value if isinstance(value, str) else ""


def _write(rows: list[dict], out_dir: Path, previous: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [{"row_id": f"W-{i:04d}", **row} for i, row in enumerate(rows, 1)]
    (out_dir / "result.json").write_text(
        json.dumps({
            "rows": len(rows),
            "previous_watermark": previous,
            "reset_performed": False,
            "new_watermark": max((str(row["id"]) for row in rows), default=previous),
            "workers": rows,
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    with (out_dir / "import_report.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["row_id", "id", "status"])
        writer.writeheader()
        writer.writerows({k: row.get(k, "") for k in writer.fieldnames} for row in rows)


def _entrypoint() -> int:
    base = os.environ.get("VENDOR_BASE_URL", "http://crewcall:8000")
    token = os.environ.get("CC_API_KEY", "")
    previous = _watermark(Path(os.environ.get("WATERMARK_FILE", "input/worker-watermark.json")))
    rows = [row for row in _crawl(base, token) if str(row.get("id", "")) > previous]
    _write(rows, Path(os.environ.get("OUTPUT_DIR", "output")), previous)
    return 0
