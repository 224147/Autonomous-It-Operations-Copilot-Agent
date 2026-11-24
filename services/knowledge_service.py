"""Knowledge base management and search."""
from __future__ import annotations

from typing import Any

from rag.store import KnowledgeBase
from services.audit_service import AuditService


class KnowledgeError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class KnowledgeService:
    def __init__(self, kb: KnowledgeBase | None, audit: AuditService) -> None:
        self._kb, self._audit = kb, audit

    @property
    def kb(self) -> KnowledgeBase:
        if self._kb is None:
            raise KnowledgeError("Knowledge base is not available", 503)
        return self._kb

    def search(self, query: str, k: int, mode: str, doc_types: list[str], filters: dict[str, Any]) -> list[dict[str, Any]]:
        hits = self.kb.search(query, k=k, mode=mode, doc_types=doc_types or None, filters=filters or None)
        return [h.to_dict() for h in hits]

    def list_documents(self, doc_type: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return self.kb.list_documents(doc_type, limit)

    def get(self, doc_id: str) -> dict[str, Any]:
        doc = self.kb.get(doc_id)
        if doc is None:
            raise KnowledgeError("Document not found", 404)
        return doc

    def add(self, doc_id: str, doc_type: str, title: str, content: str, metadata: dict[str, Any], actor: str,
            role: str = "") -> int:
        meta = {k: v for k, v in metadata.items() if k not in ("doc_type", "parent_id", "chunk")}
        if "steps" in meta and not isinstance(meta["steps"], str):
            import json

            meta["steps"] = json.dumps(meta["steps"])
        meta["title"] = title
        meta["source"] = "api"
        chunks = self.kb.upsert(doc_id, f"{title}\n\n{content}", doc_type, meta)
        self._audit.log("knowledge.upserted", actor, role, "knowledge", doc_id, {"doc_type": doc_type, "chunks": chunks})
        return chunks

    def delete(self, doc_id: str, actor: str, role: str = "") -> None:
        self.get(doc_id)
        self.kb.delete(doc_id)
        self._audit.log("knowledge.deleted", actor, role, "knowledge", doc_id)

    def stats(self) -> dict[str, Any]:
        kb = self.kb
        return {"chunks": kb.count(), "collection": kb.collection_name, "embedder": kb.embedder.id}
