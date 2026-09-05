"""Contract endpoints embedded into every standalone vendor image.

Generated vendor copies must remain byte-identical to this file. Vendor build
contexts are deliberately standalone, so the release migration copies this
small module into each package instead of reaching outside the vendor project.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse


def install_vendor_contract(app: FastAPI, docs_root: str | Path | None = None) -> None:
    """Install stable health, readiness, and canonical documentation routes.

    Call this after vendor middleware is registered. The final middleware is
    intentionally outermost and serves contract paths directly, preventing a
    vendor's data-plane authentication/fault middleware from capturing them.
    """

    root = Path(docs_root or os.environ.get("VENDOR_DOCS_DIR", "/app/docs")).resolve()

    @app.get("/_health", include_in_schema=False)
    def integration_bench_health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/_ready", include_in_schema=False)
    def integration_bench_ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/_docs/", include_in_schema=False)
    def integration_bench_docs_index():
        index = root / "index.md"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="vendor documentation unavailable")
        return FileResponse(index, media_type="text/markdown; charset=utf-8")

    @app.get("/_docs/{document:path}", include_in_schema=False)
    def integration_bench_docs_file(document: str):
        candidate = (root / document).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="document not found") from exc
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="document not found")
        media_type, _ = mimetypes.guess_type(candidate.name)
        if candidate.suffix in {".md", ".yaml", ".yml"}:
            media_type = "text/plain; charset=utf-8"
        return FileResponse(candidate, media_type=media_type)

    @app.get("/_docs", include_in_schema=False)
    def integration_bench_docs_redirect():
        return PlainTextResponse("Canonical vendor docs: /_docs/\n", status_code=200)

    @app.middleware("http")
    async def integration_bench_contract_boundary(request: Request, call_next):
        path = request.url.path
        if path == "/_health":
            return JSONResponse({"status": "ok"})
        if path == "/_ready":
            return JSONResponse({"status": "ready"})
        if path == "/_docs":
            return PlainTextResponse("Canonical vendor docs: /_docs/\n")
        if path == "/_docs/":
            index = root / "index.md"
            if not index.is_file():
                return PlainTextResponse("vendor documentation unavailable\n", status_code=404)
            return FileResponse(index, media_type="text/markdown; charset=utf-8")
        if path.startswith("/_docs/"):
            candidate = (root / path.removeprefix("/_docs/")).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return PlainTextResponse("document not found\n", status_code=404)
            if not candidate.is_file():
                return PlainTextResponse("document not found\n", status_code=404)
            media_type, _ = mimetypes.guess_type(candidate.name)
            if candidate.suffix in {".md", ".yaml", ".yml"}:
                media_type = "text/plain; charset=utf-8"
            return FileResponse(candidate, media_type=media_type)
        if path.startswith("/_ib/"):
            return PlainTextResponse("not found\n", status_code=404)
        return await call_next(request)
