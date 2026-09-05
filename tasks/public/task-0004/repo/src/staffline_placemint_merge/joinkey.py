"""Normalize candidate full name for cross-vendor matching. COMPLETE."""

from __future__ import annotations


def join_key(full_name: str) -> str:
    return " ".join(full_name.strip().split()).lower()
