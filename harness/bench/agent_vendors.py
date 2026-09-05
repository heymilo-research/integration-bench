"""Host-side standalone-vendor boot for the agent work phase."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from bench.compose import ComposeError
from bench.compose_unit import ComposeUnitStack, task_compose_unit_ready
from bench.config import load_task_config
from bench.eval_output import EvalDir


def start_agent_vendors(
    task_dir: Path,
    project: str,
    *,
    startup_timeout_s: float = 120.0,
    eval_dir: EvalDir | None = None,
):
    """Bring up the canonical standalone-vendor Compose unit."""
    task_dir = Path(task_dir)
    if not task_compose_unit_ready(task_dir):
        raise ComposeError(
            f"{task_dir} references a vendor absent from images.lock.json; "
            "the legacy per-task Compose runtime has been removed"
        )
    ephemeral = eval_dir is None
    if eval_dir is None:
        root = Path(tempfile.mkdtemp(prefix=f"ib-compose-{project}-"))
        for relative in ("workspace/repo", "vendor-logs", "canonical-data"):
            (root / relative).mkdir(parents=True, exist_ok=True)
        eval_dir = EvalDir(root=root, eval_id=project)
    try:
        stack = ComposeUnitStack(
            task_dir,
            eval_dir,
            include_agent=False,
            startup_timeout_s=startup_timeout_s,
            ephemeral=ephemeral,
        )
        stack.up(exclude_app=True)
    except Exception:
        if ephemeral:
            shutil.rmtree(eval_dir.root, ignore_errors=True)
        raise
    return stack


def agent_env_from_stack(task_dir: Path, stack) -> dict[str, str]:
    """Credentials + published vendor URLs for the agent process/sandbox."""
    task = load_task_config(Path(task_dir))
    env: dict[str, str] = {}
    for name, meta in task.vendors.items():
        env.update({str(k): str(v) for k, v in meta.credentials.items()})

    service_map = {name: name for name in task.vendors}
    primary = next(iter(task.vendors))
    primary_url = stack.data_base_url_for(service_map[primary])
    if primary_url:
        env["VENDOR_BASE_URL"] = primary_url
        env["VENDOR_DOCS_URL"] = f"{primary_url}/_docs/"
    for name, service in service_map.items():
        url = stack.data_base_url_for(service)
        if not url:
            continue
        # Block name and product both, for alias blocks (vendor-legacy vs
        # globalhire) whose task compose declares the product form.
        product = task.vendors[name].product
        for alias in dict.fromkeys((name, product)):
            prefix = alias.upper().replace("-", "_")
            env[f"VENDOR_BASE_URL_{prefix}"] = url
            env[f"{prefix}_BASE_URL"] = url
            env[f"{prefix}_DOCS_URL"] = f"{url}/_docs/"
    database_url = getattr(stack, "database_url", None) or getattr(stack, "postgres_url", None)
    if database_url:
        env["DATABASE_URL"] = database_url
    return env
