"""Per-eval artifact directory: ``artifacts/evals/<eval_id>/``."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.run_manifest import TERMINAL_STATUSES, write_run_manifest

_EVAL_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DEFAULT_OUTPUT_ROOT = Path("artifacts/evals")


def new_eval_id() -> str:
    return uuid.uuid4().hex


def validate_eval_id(value: str) -> str:
    v = (value or "").strip().lower()
    if not _EVAL_ID_RE.match(v):
        raise ValueError(f"eval_id must be 32 lowercase hex chars, got {value!r}")
    return v


@dataclass
class EvalDir:
    """``artifacts/evals/<eval_id>/`` layout for one compose-unit run."""

    root: Path
    eval_id: str
    created_at: float = field(default_factory=time.time)
    runtime_secrets: set[str] = field(default_factory=set, repr=False)
    secret_leak_locations: set[str] = field(default_factory=set)

    @classmethod
    def create(
        cls,
        *,
        output_root: Path | None = None,
        eval_id: str | None = None,
        logical_rollout_id: str | None = None,
    ) -> "EvalDir":
        eid = validate_eval_id(eval_id) if eval_id else new_eval_id()
        root = Path(output_root or DEFAULT_OUTPUT_ROOT) / eid
        root.mkdir(parents=True, exist_ok=False)
        (root / "workspace").mkdir()
        (root / "vendor-logs").mkdir()
        (root / "canonical-data").mkdir()
        (root / "participant-state").mkdir()
        ed = cls(root=root, eval_id=eid)
        ed.register_runtime_secrets(
            *(
                os.environ.get(name, "")
                for name in (
                    "ANTHROPIC_API_KEY",
                    "DEEPSEEK_API_KEY",
                    "OPENAI_API_KEY",
                    "OPENROUTER_API_KEY",
                )
            )
        )
        ed.write_meta(
            {
                "eval_id": eid,
                "attempt_id": eid,
                "logical_rollout_id": logical_rollout_id or eid,
                "status": "created",
                "created_at": ed.created_at,
            }
        )
        return ed

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def repo(self) -> Path:
        return self.workspace / "repo"

    @property
    def compose_file(self) -> Path:
        return self.root / "compose.yaml"

    @property
    def vendor_cfg_file(self) -> Path:
        return self.root / "vendor_cfg.json"

    @property
    def vendor_logs_dir(self) -> Path:
        return self.root / "vendor-logs"

    @property
    def canonical_data_dir(self) -> Path:
        return self.root / "canonical-data"

    @property
    def participant_state_dir(self) -> Path:
        return self.root / "participant-state"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    @property
    def transcript_path(self) -> Path:
        return self.root / "transcript.jsonl"

    @property
    def agent_log_path(self) -> Path:
        return self.root / "agent.log"

    @property
    def vendor_log_path(self) -> Path:
        return self.root / "vendor.log"

    @property
    def patch_path(self) -> Path:
        return self.root / "patch.diff"

    @property
    def verdict_path(self) -> Path:
        return self.root / "verdict.json"

    @property
    def run_manifest_path(self) -> Path:
        return self.root / "run-manifest.json"

    @property
    def secret_leak_detected(self) -> bool:
        return bool(self.secret_leak_locations)

    def register_runtime_secrets(self, *values: str) -> None:
        """Register real runtime credentials for in-memory redaction only."""
        for value in values:
            secret = str(value or "")
            if len(secret.encode("utf-8")) >= 8:
                self.runtime_secrets.add(secret)

    def _redact_text(self, text: str, location: str) -> str:
        redacted = text
        for secret in sorted(self.runtime_secrets, key=len, reverse=True):
            if secret in redacted:
                redacted = redacted.replace(secret, "[REDACTED_RUNTIME_SECRET]")
                self.secret_leak_locations.add(location)
        return redacted

    def _redact_bytes(self, content: bytes, location: str) -> bytes:
        redacted = content
        for secret in sorted(self.runtime_secrets, key=len, reverse=True):
            encoded = secret.encode("utf-8")
            if encoded in redacted:
                redacted = redacted.replace(encoded, b"[REDACTED_RUNTIME_SECRET]")
                self.secret_leak_locations.add(location)
        return redacted

    def scrub_runtime_secrets(self, root: Path) -> None:
        """Remove registered credentials from retained rollout-local files."""
        if not self.runtime_secrets or not root.exists():
            return
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
            if path.is_symlink():
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            redacted = self._redact_bytes(content, str(path.relative_to(self.root)))
            if redacted != content:
                path.write_bytes(redacted)

    def write_meta(self, update: dict[str, Any]) -> None:
        data: dict[str, Any] = {}
        if self.meta_path.is_file():
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
        data.update(update)
        data["eval_id"] = self.eval_id
        data["updated_at"] = time.time()
        rendered = self._redact_text(json.dumps(data, indent=2) + "\n", "meta.json")
        if self.secret_leak_detected:
            data.update(
                {
                    "resolved": False,
                    "reward": None,
                    "failure_class": "candidate_runtime_failure",
                    "error": "runtime secret exfiltration detected and redacted",
                    "secret_redaction": {
                        "detected": True,
                        "locations": sorted(self.secret_leak_locations),
                    },
                }
            )
            rendered = self._redact_text(json.dumps(data, indent=2) + "\n", "meta.json")
        self.meta_path.write_text(rendered, encoding="utf-8")
        if data.get("status") in TERMINAL_STATUSES and data.get("provenance"):
            write_run_manifest(self.root, json.loads(self.meta_path.read_text(encoding="utf-8")))

    def append_transcript(self, event: dict[str, Any]) -> None:
        event = {**event, "ts": time.time()}
        with self.transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(self._redact_text(json.dumps(event) + "\n", "transcript.jsonl"))

    def append_agent_log(self, text: str) -> None:
        text = self._redact_text(text, "agent.log")
        with self.agent_log_path.open("a", encoding="utf-8") as fh:
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")

    def write_patch(self, patch_text: str) -> Path:
        self.write_patch_bytes(patch_text.encode("utf-8"))
        return self.patch_path

    def write_patch_bytes(self, patch: bytes) -> Path:
        self.patch_path.write_bytes(self._redact_bytes(patch, "patch.diff"))
        return self.patch_path

    def write_verdict_dict(self, verdict: dict[str, Any]) -> Path:
        rendered = self._redact_text(json.dumps(verdict, indent=2) + "\n", "verdict.json")
        self.verdict_path.write_text(rendered, encoding="utf-8")
        return self.verdict_path

    def write_vendor_log(self, text: str) -> None:
        self.vendor_log_path.write_text(self._redact_text(text, "vendor.log"), encoding="utf-8")
