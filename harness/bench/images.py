"""Standalone vendor image resolution, build, pull, and lock validation.

Local development uses one mutable ``<vendor>:local`` image per vendor. Scored
and CI runs use only immutable registry digests from ``images.lock.json``.
There is deliberately no shared or fallback vendor image.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable


class ImageError(RuntimeError):
    """A vendor image is missing, mutable, or not represented by the lock."""


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk upward from ``start``, cwd, and the installed package."""
    starts = [(start or Path.cwd()).resolve(), Path(__file__).resolve().parent]
    for origin in starts:
        cur = origin
        for _ in range(12):
            if (cur / "images.lock.json").is_file():
                return cur
            if (cur / "harness" / "pyproject.toml").is_file() and (cur / "vendors").is_dir():
                return cur
            if cur.parent == cur:
                break
            cur = cur.parent
    return None


def lockfile_path(repo_root: Path | None = None) -> Path:
    root = repo_root or find_repo_root()
    if root is None:
        raise ImageError("cannot find the monorepo or images.lock.json")
    path = root / "images.lock.json"
    if not path.is_file():
        raise ImageError(f"missing image lock: {path}")
    return path


def load_images_lock(repo_root: Path | None = None) -> dict[str, Any]:
    path = lockfile_path(repo_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImageError(f"invalid image lock {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ImageError(f"{path} must use images lock schema_version 1")
    vendors = data.get("vendors")
    if not isinstance(vendors, dict) or not vendors:
        raise ImageError(f"{path} must contain a non-empty vendors mapping")
    return data


def image_mode(repo_root: Path | None = None) -> str:
    """Return ``local`` or ``locked`` for the current repository shape.

    Private source checkouts default to mutable local images. Public exports do
    not ship vendor source, so their only runnable default is the immutable
    registry lock.
    """
    explicit = os.environ.get("IB_IMAGE_MODE", "").strip().lower()
    if explicit:
        if explicit not in {"local", "locked"}:
            raise ImageError("IB_IMAGE_MODE must be 'local' or 'locked'")
        return explicit
    if os.environ.get("IB_SCORED_RUN") == "1" or os.environ.get("CI") == "true":
        return "locked"
    root = repo_root or find_repo_root()
    if root is not None and not (root / "vendors").is_dir():
        return "locked"
    return "local"


def _vendor_entry(vendor_id: str, repo_root: Path | None = None) -> dict[str, Any]:
    vendors = load_images_lock(repo_root)["vendors"]
    try:
        entry = vendors[vendor_id]
    except KeyError as exc:
        raise ImageError(f"vendor {vendor_id!r} is absent from images.lock.json") from exc
    if not isinstance(entry, dict):
        raise ImageError(f"image lock entry for {vendor_id!r} must be an object")
    return dict(entry)


def local_tag(vendor_id: str, entry: dict[str, Any] | None = None) -> str:
    value = (entry or _vendor_entry(vendor_id)).get("local_tag")
    return str(value or f"{vendor_id}:local")


def registry_ref(vendor_id: str, entry: dict[str, Any] | None = None) -> str:
    """Return the immutable ``registry/repository@sha256:…`` reference."""
    value = entry or _vendor_entry(vendor_id)
    ref = str(value.get("registry_ref") or "").strip()
    digest = str(value.get("digest") or "").strip()
    if not ref or not digest:
        raise ImageError(f"vendor {vendor_id!r} has not been promoted: registry_ref/digest missing")
    if not digest.startswith("sha256:"):
        digest = f"sha256:{digest}"
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ImageError(f"vendor {vendor_id!r} has invalid digest {digest!r}")
    base = ref.split("@", 1)[0]
    return f"{base}@{digest}"


# Docker may transiently fail while another Compose project is being removed.
_ABSENT_MARKERS = ("no such image", "no such object", "not found")


def _docker_image_exists(ref: str) -> bool:
    if shutil.which("docker") is None:
        return False
    last = ""
    for attempt in range(4):
        try:
            proc = subprocess.run(
                ["docker", "image", "inspect", ref],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            last = "docker image inspect timed out"
        else:
            if proc.returncode == 0:
                return True
            last = (proc.stderr or proc.stdout or "").strip()
            if any(marker in last.lower() for marker in _ABSENT_MARKERS):
                return False
        if attempt < 3:
            time.sleep(0.25 * (2**attempt))
    raise ImageError(f"Docker daemon fault while inspecting {ref!r}: {last or '(no output)'}")


def resolve_vendor_image(
    vendor_id: str,
    *,
    repo_root: Path | None = None,
    mode: str | None = None,
) -> str:
    """Resolve one vendor without falling back across local/locked modes."""
    selected = mode or image_mode(repo_root)
    if selected not in {"local", "locked"}:
        raise ImageError(f"unknown image mode {selected!r}")

    override_key = f"IB_VENDOR_IMAGE_{vendor_id.upper().replace('-', '_')}"
    override = os.environ.get(override_key, "").strip()
    if override:
        if selected == "locked" and "@sha256:" not in override:
            raise ImageError(f"{override_key} must be digest-pinned in locked mode")
        return override

    entry = _vendor_entry(vendor_id, repo_root)
    if selected == "locked":
        return registry_ref(vendor_id, entry)

    ref = local_tag(vendor_id, entry)
    if shutil.which("docker") is None:
        raise ImageError("docker not found on PATH")
    if not _docker_image_exists(ref):
        raise ImageError(
            f"local vendor image {ref!r} is missing. Run "
            f"`bench build-vendors --vendor {vendor_id}` or select locked mode."
        )
    return ref


def _selected_vendor_ids(repo_root: Path, vendor_ids: Iterable[str] | None) -> list[str]:
    known = sorted(load_images_lock(repo_root)["vendors"])
    selected = list(dict.fromkeys(vendor_ids or known))
    unknown = sorted(set(selected) - set(known))
    if unknown:
        raise ImageError(f"unknown vendors: {', '.join(unknown)}")
    return selected


def build_vendor_images(
    vendor_ids: Iterable[str] | None = None,
    *,
    repo_root: Path | None = None,
    platform: str | None = None,
) -> dict[str, str]:
    """Build selected standalone vendor contexts into their local tags."""
    root = repo_root or find_repo_root()
    if root is None:
        raise ImageError("cannot find monorepo root")
    if shutil.which("docker") is None:
        raise ImageError("docker not found on PATH")
    built: dict[str, str] = {}
    for vendor_id in _selected_vendor_ids(root, vendor_ids):
        context = root / "vendors" / vendor_id
        if not (context / "Dockerfile").is_file():
            raise ImageError(f"vendor build context missing: {context}")
        tag = local_tag(vendor_id, _vendor_entry(vendor_id, root))
        cmd = ["docker", "build", "--tag", tag]
        if platform:
            cmd.extend(["--platform", platform])
        cmd.append(str(context))
        proc = subprocess.run(cmd, cwd=root)
        if proc.returncode != 0:
            raise ImageError(f"failed to build vendor {vendor_id!r}")
        built[vendor_id] = tag
    return built


def pull_vendor_images(
    vendor_ids: Iterable[str] | None = None,
    *,
    repo_root: Path | None = None,
) -> dict[str, str]:
    """Pull selected immutable vendor refs; never use mutable local aliases."""
    root = repo_root or find_repo_root()
    if root is None:
        raise ImageError("cannot find monorepo root")
    if shutil.which("docker") is None:
        raise ImageError("docker not found on PATH")
    pulled: dict[str, str] = {}
    for vendor_id in _selected_vendor_ids(root, vendor_ids):
        ref = registry_ref(vendor_id, _vendor_entry(vendor_id, root))
        proc = subprocess.run(["docker", "pull", ref])
        if proc.returncode != 0:
            raise ImageError(f"failed to pull locked vendor {vendor_id!r}")
        pulled[vendor_id] = ref
    return pulled


def validate_images_lock(
    *, repo_root: Path | None = None, require_promoted: bool = False
) -> list[str]:
    """Return validation errors for lock completeness and vendor coverage."""
    root = repo_root or find_repo_root()
    if root is None:
        return ["cannot find monorepo root"]
    try:
        data = load_images_lock(root)
    except ImageError as exc:
        return [str(exc)]
    entries = data["vendors"]
    disk = {p.name for p in (root / "vendors").iterdir() if p.is_dir()}
    errors = [f"missing lock entry: {v}" for v in sorted(disk - set(entries))]
    errors += [f"lock entry has no vendor directory: {v}" for v in sorted(set(entries) - disk)]
    required = {"platform", "source_revision", "docs_hash", "contract_version", "local_tag"}
    for vendor_id, entry in sorted(entries.items()):
        for field in sorted(required):
            if not entry.get(field):
                errors.append(f"{vendor_id}: missing {field}")
        if require_promoted:
            for field in ("registry_ref", "digest", "built_at", "sbom", "signature"):
                if not entry.get(field):
                    errors.append(f"{vendor_id}: missing promoted field {field}")
            try:
                registry_ref(vendor_id, dict(entry))
            except ImageError as exc:
                errors.append(str(exc))
    return errors
