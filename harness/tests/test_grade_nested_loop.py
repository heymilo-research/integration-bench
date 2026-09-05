"""grade_once must work when called from an already-running asyncio loop.

PI taskset ``finalize`` is async; ``asyncio.run`` inside ``grade_once`` used to
raise and zero every reward.
"""

from __future__ import annotations

import asyncio

from bench.commands.grading_core import _run_coro_sync


def test_run_coro_sync_outside_loop() -> None:
    async def add(a: int, b: int) -> int:
        await asyncio.sleep(0)
        return a + b

    assert _run_coro_sync(add(2, 3)) == 5


def test_run_coro_sync_inside_running_loop() -> None:
    async def add(a: int, b: int) -> int:
        await asyncio.sleep(0)
        return a + b

    async def nested() -> int:
        return _run_coro_sync(add(4, 5))

    assert asyncio.run(nested()) == 9
