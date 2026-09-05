"""VerifierContext (`ctx`) for the file-and-env M1 vendor contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from bench.config import TaskConfig, VendorMetadata
from bench.verifier.checks import CheckRecorder


class VerifierStack(Protocol):
    app_service: str
    project: str
    vendor_envs: dict[str, dict[str, str]]

    def run(self, service: str, *args: str, check: bool = True): ...
    def recreate_vendor(self, service: str) -> None: ...
    def data_base_url_for(self, service: str) -> str | None: ...
    def read_vendor_jsonl(self, service: str, filename: str) -> list[dict[str, Any]]: ...


class AppHandle:
    """Compose-exec wrapper over the per-task app service."""

    def __init__(
        self, stack: VerifierStack, entry: list[str], output_dir: Path | None = None
    ) -> None:
        self._stack = stack
        self._entry = entry
        self.output_dir = output_dir

    def run(self, extra_args: list[str] | None = None) -> tuple[int, str, str]:
        args = list(self._entry)
        if extra_args:
            args.extend(extra_args)
        result = self._stack.run(self._stack.app_service, *args, check=False)
        stderr = result.stderr or ""
        # docker compose run prefixes a "Container <project>-<service>-1  Running"
        # status line to stderr; remove it so verdicts stay flake-identical.
        lines = [ln for ln in stderr.splitlines() if "Running" not in ln]
        return result.returncode, result.stdout, "\n".join(lines)


class VendorHandle:
    """File-and-env vendor operations: recreate by checkpoint, read logs."""

    def __init__(
        self,
        stack: VerifierStack,
        metadata: VendorMetadata,
        service: str = "vendor",
    ) -> None:
        self._stack = stack
        self._metadata = metadata
        self._service = service

    def recreate(self, *, checkpoint: int = 0, env: dict[str, str] | None = None) -> None:
        """Recreate the vendor container at the given CHECKPOINT.

        Uses this handle's own service name (not necessarily the stack's
        "primary" vendor service) so multi-vendor tasks — e.g. `vendor-legacy`
        + `vendor-new` per SPEC §5.3 — can recreate either vendor
        independently via `ctx.vendor("vendor-legacy").recreate(...)`.

        `env` sets additional environment on this vendor service for this and
        subsequent boots. Vendors with no admin/control channel read their
        `FAULT_*` knobs from the boot environment only (see
        vendors/talentloop/src/talentloop/main.py), so recreating with an env
        override is the sole way for a scenario to arm a fault mid-task —
        e.g. `recreate(checkpoint=7, env={"FAULT_5XX_ON_PAGE": "3:1"})`.

        Pass an empty string to clear a knob: the value is written into the
        compose override verbatim, and the vendors parse `""` as "off".
        """
        overrides = self._stack.vendor_envs.setdefault(self._service, {})
        overrides[self._metadata.checkpoint_env] = str(checkpoint)
        if env:
            overrides.update({str(k): str(v) for k, v in env.items()})
        self._stack.recreate_vendor(self._service)

    @property
    def base_url(self) -> str | None:
        """This vendor's published data port URL (multi-vendor safe)."""
        return self._stack.data_base_url_for(self._service)

    def _read_log(self, filename: str) -> list[dict[str, Any]]:
        return self._stack.read_vendor_jsonl(self._service, filename)

    def request_log(self) -> list[dict[str, Any]]:
        return self._read_log("requests.jsonl")

    def token_log(self) -> list[dict[str, Any]]:
        return self._read_log("tokens.jsonl")

    def webhook_deliveries(self) -> list[dict[str, Any]]:
        """Webhook delivery attempts, for vendors that dispatch webhooks.

        M2 contract: vendors with a webhook surface append one JSONL line per
        delivery attempt to ``{log_path}/webhook_deliveries.jsonl``. Vendors
        without webhooks simply never create the file (missing file -> []).
        """
        return self._read_log("webhook_deliveries.jsonl")


class VerifierContext:
    """`ctx` passed to scenario modules."""

    def __init__(
        self,
        task: TaskConfig,
        vendor_metadata: VendorMetadata,
        app: AppHandle,
        fixtures: Path,
        secrets: dict[str, str],
        output_dir: Path | None = None,
    ) -> None:
        self.task = task
        self.vendor_metadata = vendor_metadata
        self.app = app
        self.fixtures = fixtures
        self.secrets = secrets
        self.output_dir = output_dir
        self._vendors: dict[str, VendorHandle] = {}
        self._recorder = CheckRecorder()

    def vendor(self, name: str) -> VendorHandle:
        return self._vendors[name]

    # -- recording asserts: record results, never raise ----------------------

    def check(
        self,
        name: str,
        ok: bool,
        detail: str = "",
        *,
        pass_value: int = 1,
        fail_value: int = 0,
        mandatory: bool = False,
        bucket: str = "l1",
    ) -> None:
        """Record a result and state what it is worth. See ``CheckRecorder.check``.

        This is the target API; the four ``check_<bucket>`` methods below are
        migration shims that pick a default and are otherwise identical.
        """
        self._recorder.check(
            name,
            ok,
            pass_value=pass_value,
            fail_value=fail_value,
            mandatory=mandatory,
            detail=detail,
            bucket=bucket,
        )

    def check_l1(self, name: str, ok: bool, detail: str = "") -> None:
        self._recorder.check_l1(name, ok, detail)

    def check_hard(self, name: str, ok: bool, detail: str = "") -> None:
        self._recorder.check_hard(name, ok, detail)

    def check_soft(self, name: str, ok: bool, detail: str = "") -> None:
        self._recorder.check_soft(name, ok, detail)

    def check_l3(self, name: str, ok: bool, detail: str = "") -> None:
        self._recorder.check_l3(name, ok, detail)

    @property
    def recorder(self) -> CheckRecorder:
        return self._recorder
