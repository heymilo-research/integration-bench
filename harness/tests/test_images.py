"""Standalone image-lock resolution tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench import images


def _lock(tmp_path: Path, *, promoted: bool = True) -> None:
    digest = "sha256:" + "a" * 64 if promoted else None
    data = {
        "schema_version": 1,
        "vendors": {
            "bullpen": {
                "local_tag": "bullpen:local",
                "registry_ref": "ghcr.io/heymilo/ib-vendor-bullpen",
                "digest": digest,
                "platform": "linux/amd64",
                "source_revision": "abc123",
                "docs_hash": "sha256:docs",
                "contract_version": "1.0.0",
                "built_at": "2026-08-14T00:00:00Z" if promoted else None,
                "sbom": "ghcr.io/example/sbom" if promoted else None,
                "signature": "cosign:abc" if promoted else None,
            }
        },
    }
    (tmp_path / "images.lock.json").write_text(json.dumps(data), encoding="utf-8")


def test_load_and_resolve_immutable_ref(tmp_path: Path) -> None:
    _lock(tmp_path)
    loaded = images.load_images_lock(tmp_path)
    entry = loaded["vendors"]["bullpen"]
    assert images.local_tag("bullpen", entry) == "bullpen:local"
    assert images.registry_ref("bullpen", entry) == (
        "ghcr.io/heymilo/ib-vendor-bullpen@sha256:" + "a" * 64
    )
    assert images.resolve_vendor_image("bullpen", repo_root=tmp_path, mode="locked").endswith(
        "@sha256:" + "a" * 64
    )


def test_locked_mode_never_falls_back_to_local(tmp_path: Path) -> None:
    _lock(tmp_path, promoted=False)
    with pytest.raises(images.ImageError, match="has not been promoted"):
        images.resolve_vendor_image("bullpen", repo_root=tmp_path, mode="locked")


def test_local_override_is_per_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_IMAGE_MODE", "local")
    monkeypatch.setenv("IB_VENDOR_IMAGE_BULLPEN", "bullpen:test")
    assert images.resolve_vendor_image("bullpen") == "bullpen:test"


def test_locked_override_must_be_digest_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_VENDOR_IMAGE_BULLPEN", "bullpen:latest")
    with pytest.raises(images.ImageError, match="digest-pinned"):
        images.resolve_vendor_image("bullpen", mode="locked")


def test_scored_runs_default_to_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IB_IMAGE_MODE", raising=False)
    monkeypatch.setenv("IB_SCORED_RUN", "1")
    assert images.image_mode() == "locked"


def test_public_export_without_vendor_source_defaults_to_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lock(tmp_path)
    monkeypatch.delenv("IB_IMAGE_MODE", raising=False)
    monkeypatch.delenv("IB_SCORED_RUN", raising=False)
    monkeypatch.delenv("CI", raising=False)

    assert images.image_mode(tmp_path) == "locked"
    assert images.resolve_vendor_image("bullpen", repo_root=tmp_path).endswith(
        "@sha256:" + "a" * 64
    )


def test_source_checkout_with_vendor_directory_defaults_to_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lock(tmp_path)
    (tmp_path / "vendors").mkdir()
    monkeypatch.delenv("IB_IMAGE_MODE", raising=False)
    monkeypatch.delenv("IB_SCORED_RUN", raising=False)
    monkeypatch.delenv("CI", raising=False)

    assert images.image_mode(tmp_path) == "local"
