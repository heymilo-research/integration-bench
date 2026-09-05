"""CrewCall roster repair checks against live output and request evidence."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from bench.verifier import builtin_l2


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return [], []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return list(reader.fieldnames or []), list(reader)
    except Exception:
        return [], []


async def run(ctx) -> None:
    key = _read_json(Path(ctx.fixtures) / "answer_key.json")
    ctx.vendor(ctx.vendor_metadata.name).recreate(
        checkpoint=int(key.get("checkpoint", 1)),
        env={str(k): str(v) for k, v in (key.get("vendor_env") or {}).items()},
    )
    returncode, _, stderr = ctx.app.run()
    result = _read_json(Path(ctx.output_dir) / "result.json")
    csv_fields, csv_rows = _read_csv(Path(ctx.output_dir) / "import_report.csv")
    json_rows = result.get("workers") if isinstance(result.get("workers"), list) else []
    json_by_id = {
        str(row.get("id")): row for row in json_rows
        if isinstance(row, dict) and row.get("id")
    }
    csv_ids = [row.get("id", "") for row in csv_rows]
    csv_by_id = {row.get("id", ""): row for row in csv_rows if row.get("id")}
    expected_ids = list(key.get("worker_ids", []))
    expected_fields = key.get("workers", {})
    expected_count = int(key.get("worker_count", 0))
    previous_watermark = str(key.get("previous_watermark", "wkr_9999"))

    ctx.check_l1(
        "task0248_result_contract",
        returncode == 0
        and isinstance(result.get("rows"), int)
        and isinstance(result.get("workers"), list),
        f"exit={returncode}; result.json must contain integer rows and a workers array; stderr={stderr[:160]}",
    )
    result_schema_exact = set(result) == {
        "rows", "previous_watermark", "reset_performed", "new_watermark", "workers"
    }
    ctx.check_l1(
        "task0248_result_schema_exact",
        result_schema_exact,
        f"result fields={sorted(result)}",
    )
    ctx.check_l1(
        "task0248_result_count",
        result.get("rows") == expected_count
        and len(json_rows) == expected_count,
        f"rows={result.get('rows')} workers={len(json_rows)} expected={expected_count}",
    )
    ctx.check_l1(
        "task0248_result_ids_exact_once",
        sorted(json_by_id) == expected_ids
        and len(json_by_id) == len(json_rows) == expected_count,
        f"json ids={len(json_by_id)} expected={expected_count}",
    )
    ctx.check_l1(
        "task0248_result_source_fields",
        all(
            json_by_id.get(worker_id) == {
                "row_id": f"W-{index:04d}", **expected_fields[worker_id]
            }
            for index, worker_id in enumerate(expected_ids, 1)
        ),
        "one or more complete JSON source worker rows differ from the answer key",
    )
    ctx.check_l1(
        "task0248_previous_watermark_reported",
        result.get("previous_watermark") == previous_watermark,
        f"got={result.get('previous_watermark')!r} expected={previous_watermark!r}",
    )
    ctx.check_l1(
        "task0248_regressed_watermark_reset",
        result.get("reset_performed") is True,
        "the inherited watermark is ahead of every id in this tenant generation",
    )
    ctx.check_l1(
        "task0248_new_watermark_from_source",
        result.get("new_watermark") == max(expected_ids, default=""),
        f"got={result.get('new_watermark')!r}",
    )

    expected_row_ids = {
        worker_id: f"W-{index:04d}"
        for index, worker_id in enumerate(expected_ids, 1)
    }
    for worker_id in expected_ids:
        expected = expected_fields.get(worker_id, {})
        json_row = json_by_id.get(worker_id, {})
        csv_row = csv_by_id.get(worker_id, {})
        ctx.check_l1(
            f"task0248_json_worker_present_{worker_id}",
            bool(json_row),
            f"{worker_id} missing from result.json",
        )
        ctx.check_l1(
            f"task0248_json_worker_fields_{worker_id}",
            json_row.get("status") == expected.get("status")
            and json_row.get("is_deleted") == expected.get("is_deleted"),
            f"{worker_id} source fields changed or are absent",
        )
        ctx.check_l1(
            f"task0248_csv_worker_present_{worker_id}",
            bool(csv_row),
            f"{worker_id} missing from import_report.csv",
        )
        ctx.check_l1(
            f"task0248_cross_artifact_row_{worker_id}",
            json_row.get("row_id") == expected_row_ids[worker_id]
            and csv_row.get("row_id") == expected_row_ids[worker_id]
            and csv_row.get("status") == expected.get("status"),
            f"{worker_id} row id or status disagrees across artifacts",
        )
    expected_deleted = set(key.get("deleted_ids", []))
    ctx.check_l1(
        "task0248_deleted_workers_retained",
        expected_deleted
        and all(
            json_by_id.get(worker_id, {}).get("is_deleted") is True
            for worker_id in expected_deleted
        ),
        f"deleted ids={sorted(expected_deleted)}",
    )

    ctx.check_l1(
        "task0248_csv_contract",
        csv_fields == ["row_id", "id", "status"],
        f"fields={csv_fields}",
    )
    ctx.check_l1(
        "task0248_csv_row_ids_sequential",
        [row.get("row_id") for row in csv_rows]
        == [f"W-{index:04d}" for index in range(1, expected_count + 1)],
        "row_id sequence is not W-0001 through the roster count",
    )
    ctx.check_l1(
        "task0248_csv_ids_sorted_unique",
        csv_ids == expected_ids and len(csv_ids) == len(set(csv_ids)),
        f"csv rows={len(csv_ids)} expected={expected_count}",
    )
    ctx.check_l1(
        "task0248_csv_matches_json",
        len(csv_rows) == expected_count
        and all(
            row.get("status") == json_by_id.get(row.get("id"), {}).get("status")
            for row in csv_rows
        ),
        "CSV status values disagree with JSON worker records",
    )

    vendor = ctx.vendor(ctx.vendor_metadata.name)
    request_log = vendor.request_log()
    worker_gets = [
        entry for entry in request_log
        if entry.get("method") == "GET" and entry.get("path") == "/v1/workers"
    ]
    data_plane = [
        entry for entry in request_log
        if str(entry.get("path", "")).startswith("/v1/")
    ]
    expected_offsets = list(range(
        0,
        expected_count + key.get("page_limit", 10),
        key.get("page_limit", 10),
    ))
    offsets = [
        int(entry.get("query", {}).get("offset", -1))
        for entry in worker_gets
        if str(entry.get("query", {}).get("offset", "")).lstrip("-").isdigit()
    ]
    page_limit = int(key.get("page_limit", 10))
    pages_per_pass = int(key.get("pages_per_pass", 0))
    pass_shapes = [
        offsets[index:index + pages_per_pass]
        for index in range(0, len(offsets) - pages_per_pass + 1, pages_per_pass)
    ]
    ctx.check_l1(
        "task0248_authenticated_worker_reads",
        bool(worker_gets)
        and all(entry.get("status") == 200 for entry in worker_gets)
        and all(entry.get("auth_outcome") == "ok" for entry in worker_gets),
        f"successful worker reads={len(worker_gets)}",
    )
    ctx.check_l1(
        "task0248_offset_limit_contract",
        bool(worker_gets)
        and all(
            entry.get("query", {}).get("limit") == str(page_limit)
            and entry.get("query", {}).get("offset") is not None
            for entry in worker_gets
        ),
        "worker reads did not carry the expected offset/limit query",
    )
    ctx.check_l1(
        "task0248_convergence_repeats_full_pass",
        len(worker_gets) >= pages_per_pass * 2
        and any(shape == expected_offsets for shape in pass_shapes),
        f"worker GETs={len(worker_gets)} offsets={offsets[:pages_per_pass]}",
    )
    ctx.check_l1(
        "task0248_read_only_data_plane",
        bool(data_plane) and all(entry.get("method") == "GET" for entry in data_plane),
        "a non-GET request was sent under /v1",
    )

    # builtin_l2 omits its conduct names when the connector emitted no traffic.
    # Emit evidence-gated instances first so absence is a failure, not an
    # omitted/vacuous pass. Duplicate names are folded with logical AND by the
    # scorer, so builtin_l2 still supplies the full value audit when traffic exists.
    credential_query = [
        entry for entry in data_plane
        if any(
            key.lower() in {"token", "api_key", "key", "secret", "authorization"}
            for key in (entry.get("query") or {})
        )
    ]
    ctx.check_l1(
        "no_credentials_in_query_string",
        bool(data_plane) and not credential_query,
        f"reads={len(data_plane)} credential_query={len(credential_query)}",
    )
    ctx.check_l1(
        "no_secrets_echoed_to_vendor",
        bool(data_plane),
        f"reads={len(data_plane)}; builtin_l2 audits logged values",
    )
    ctx.check_l1(
        "no_unnecessary_full_resync:worker",
        bool(worker_gets) and len(worker_gets) <= pages_per_pass * 3,
        f"worker reads={len(worker_gets)} bounded convergence cap={pages_per_pass * 3}",
    )

    await builtin_l2(ctx, app_runs=1)
