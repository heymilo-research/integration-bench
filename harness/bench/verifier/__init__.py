"""Verifier scenario SDK for the M1 file-and-env contract.

Public surface for scenario authors (verifier/scenarios/<name>.py):

    from bench.verifier import builtin_l2

    async def run(ctx):
        ctx.app.run()
        ctx.vendor("recruitos").recreate(checkpoint=5)
        ...
        ctx.check_l1("candidates_match_fixture", ok, detail)
        await builtin_l2(ctx)
"""

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.context import AppHandle, VerifierContext, VendorHandle

__all__ = [
    "builtin_l2",
    "AppHandle",
    "VerifierContext",
    "VendorHandle",
]
