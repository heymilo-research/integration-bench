from __future__ import annotations

import socket

import pytest

from bench.ports import stable_free_port


def test_stable_free_port_is_key_stable() -> None:
    assert stable_free_port("eval-a") == stable_free_port("eval-a")


def test_stable_free_port_skips_occupied_candidate() -> None:
    first = stable_free_port("eval-b")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", first))
        assert stable_free_port("eval-b") != first


@pytest.mark.parametrize(
    ("low", "high"),
    [(0, 10), (60_000, 59_999), (20_000, 70_000)],
)
def test_stable_free_port_rejects_invalid_ranges(low: int, high: int) -> None:
    with pytest.raises(ValueError):
        stable_free_port("eval-c", low=low, high=high)
