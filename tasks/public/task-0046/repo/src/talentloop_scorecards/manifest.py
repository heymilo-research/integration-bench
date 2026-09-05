"""Read the tenant's scorecard export.

The export is a JSON object with an ``exported_at`` stamp and a ``documents``
array. Each document looks like::

    {"doc_ref": "XX-0000",
     "candidate_id": "cand_0000",
     "filename": "placeholder.pdf",
     "content_type": "application/pdf",
     "sha256": "0000...0000",
     "author": "someone@example.invalid",
     "summary": "placeholder",
     "content_b64": "<base64 of the file bytes>"}

``note_body_for`` is the tenant's agreed note wording; keep using it so the
notes read the same as the ones their coordinators write by hand.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Document:
    doc_ref: str
    candidate_id: str
    filename: str
    content_type: str
    sha256: str
    author: str
    summary: str
    content_b64: str

    @property
    def content_bytes(self) -> bytes:
        return base64.b64decode(self.content_b64)


def load_documents(path: Path) -> list[Document]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        Document(
            doc_ref=str(row["doc_ref"]),
            candidate_id=str(row["candidate_id"]),
            filename=str(row["filename"]),
            content_type=str(row["content_type"]),
            sha256=str(row["sha256"]),
            author=str(row.get("author", "")),
            summary=str(row.get("summary", "")),
            content_b64=str(row["content_b64"]),
        )
        for row in payload.get("documents", [])
    ]


def note_body_for(doc: Document) -> str:
    """The note wording the tenant asked for: ``<doc_ref>: <summary>``."""
    return f"{doc.doc_ref}: {doc.summary}"
