"""ChromaDB-backed knowledge base with hybrid (semantic + BM25 keyword) search and metadata filtering."""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from rag.embeddings import Embedder, tokenize

logger = logging.getLogger(__name__)

DOC_TYPES = (
    "incident",
    "runbook",
    "sop",
    "postmortem",
    "infrastructure",
    "deployment",
    "dependency",
    "ticket",
)
CHUNK_CHARS = 1200
RRF_K = 60


@dataclass
class SearchHit:
    id: str
    parent_id: str
    text: str
    metadata: dict[str, Any]
    score: float
    semantic_score: float | None = None
    keyword_score: float | None = None
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.parent_id,
            "chunk_id": self.id,
            "text": self.text,
            "metadata": self.metadata,
            "score": round(self.score, 4),
            "semantic_score": None if self.semantic_score is None else round(self.semantic_score, 4),
            "keyword_score": None if self.keyword_score is None else round(self.keyword_score, 4),
        }


def chunk_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        while len(para) > limit:  # very long paragraph
            chunks.append(para[:limit])
            para = para[limit:]
        if current and len(current) + len(para) + 2 > limit:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


def _clean_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            clean[key] = ",".join(str(v) for v in value)
        elif isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


class _BM25:
    def __init__(self, docs: list[tuple[str, list[str]]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.ids = [d[0] for d in docs]
        self.tfs = [Counter(d[1]) for d in docs]
        self.lengths = [len(d[1]) for d in docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if docs else 0.0
        df: Counter[str] = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def score(self, query_tokens: list[str]) -> list[tuple[str, float]]:
        scores = []
        for doc_id, tf, length in zip(self.ids, self.tfs, self.lengths):
            s = 0.0
            for term in query_tokens:
                f = tf.get(term)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * length / (self.avg or 1))
                s += self.idf.get(term, 0.0) * f * (self.k1 + 1) / denom
            if s > 0:
                scores.append((doc_id, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores


class KnowledgeBase:
    def __init__(
        self,
        embedder: Embedder,
        persist_dir: str = "./data/chroma",
        host: str = "",
        port: int = 8000,
        collection_prefix: str = "kb",
        client: Any | None = None,
    ) -> None:
        self.embedder = embedder
        if client is not None:
            self._client = client
        elif host:
            self._client = chromadb.HttpClient(host=host, port=port, settings=ChromaSettings(anonymized_telemetry=False))
        else:
            self._client = chromadb.PersistentClient(path=persist_dir, settings=ChromaSettings(anonymized_telemetry=False))
        name = re.sub(r"[^a-zA-Z0-9_\-]", "_", f"{collection_prefix}_{embedder.id}")[:63]
        self._collection = self._client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
        self.collection_name = name
        self._lock = threading.RLock()
        self._bm25: _BM25 | None = None
        self._bm25_docs: dict[str, tuple[str, dict[str, Any]]] = {}
        self._bm25_built = 0.0
        self._dirty = True

    # ------------------------------------------------------------------ write
    def upsert(self, doc_id: str, text: str, doc_type: str, metadata: dict[str, Any] | None = None) -> int:
        """Index a document (split into chunks). Replaces any previous version of doc_id."""
        if not text.strip():
            raise ValueError("document text is empty")
        meta = _clean_metadata({**(metadata or {}), "doc_type": doc_type, "parent_id": doc_id})
        chunks = chunk_text(text)
        ids = [f"{doc_id}#{i}" for i in range(len(chunks))]
        embeddings = self.embedder.embed(chunks)
        with self._lock:
            self._delete_chunks(doc_id)
            self._collection.upsert(
                ids=ids,
                documents=chunks,
                embeddings=embeddings,
                metadatas=[{**meta, "chunk": i} for i in range(len(chunks))],
            )
            self._dirty = True
        return len(chunks)

    def _delete_chunks(self, doc_id: str) -> None:
        self._collection.delete(where={"parent_id": doc_id})

    def delete(self, doc_id: str) -> None:
        with self._lock:
            self._delete_chunks(doc_id)
            self._dirty = True

    # ------------------------------------------------------------------- read
    def count(self) -> int:
        return self._collection.count()

    def get(self, doc_id: str) -> dict[str, Any] | None:
        res = self._collection.get(where={"parent_id": doc_id}, include=["documents", "metadatas"])
        if not res["ids"]:
            return None
        pairs = sorted(zip(res["metadatas"], res["documents"]), key=lambda p: p[0].get("chunk", 0))
        return {"id": doc_id, "text": "\n\n".join(p[1] for p in pairs), "metadata": pairs[0][0]}

    def list_documents(self, doc_type: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"include": ["metadatas"]}
        if doc_type:
            kwargs["where"] = {"doc_type": doc_type}
        res = self._collection.get(**kwargs)
        seen: dict[str, dict[str, Any]] = {}
        for meta in res["metadatas"]:
            if meta.get("chunk", 0) == 0:
                seen[meta["parent_id"]] = meta
        docs = [{"id": pid, "metadata": m} for pid, m in seen.items()]
        docs.sort(key=lambda d: d["id"])
        return docs[:limit]

    @staticmethod
    def _where(doc_types: list[str] | None, filters: dict[str, Any] | None) -> dict[str, Any] | None:
        clauses: list[dict[str, Any]] = []
        if doc_types:
            clauses.append({"doc_type": {"$in": list(doc_types)}})
        for key, value in (filters or {}).items():
            clauses.append({key: value})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    @staticmethod
    def _matches(meta: dict[str, Any], doc_types: list[str] | None, filters: dict[str, Any] | None) -> bool:
        if doc_types and meta.get("doc_type") not in doc_types:
            return False
        return all(meta.get(k) == v for k, v in (filters or {}).items())

    def _ensure_bm25(self) -> None:
        with self._lock:
            if self._bm25 is not None and not self._dirty and time.time() - self._bm25_built < 60:
                return
            res = self._collection.get(include=["documents", "metadatas"])
            self._bm25_docs = {i: (d, m) for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])}
            self._bm25 = _BM25([(i, tokenize(d + " " + str(m.get("title", "")))) for i, (d, m) in self._bm25_docs.items()])
            self._bm25_built = time.time()
            self._dirty = False

    def search(
        self,
        query: str,
        k: int = 5,
        mode: str = "hybrid",
        doc_types: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        if not query.strip() or self.count() == 0:
            return []
        pool = max(k * 4, 20)
        ranked: dict[str, SearchHit] = {}
        semantic_rank: list[str] = []
        keyword_rank: list[str] = []

        if mode in ("hybrid", "semantic"):
            try:
                emb = self.embedder.embed([query])[0]
                res = self._collection.query(
                    query_embeddings=[emb],
                    n_results=min(pool, self.count()),
                    where=self._where(doc_types, filters),
                    include=["documents", "metadatas", "distances"],
                )
                for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
                    hit = ranked.setdefault(cid, SearchHit(cid, meta.get("parent_id", cid), doc, meta, 0.0))
                    hit.semantic_score = max(0.0, 1.0 - float(dist))
                    semantic_rank.append(cid)
            except Exception:
                if mode == "semantic":
                    raise
                logger.exception("Semantic search failed; using keyword search only")

        if mode in ("hybrid", "keyword"):
            self._ensure_bm25()
            assert self._bm25 is not None
            for cid, score in self._bm25.score(tokenize(query)):
                doc, meta = self._bm25_docs[cid]
                if not self._matches(meta, doc_types, filters):
                    continue
                hit = ranked.setdefault(cid, SearchHit(cid, meta.get("parent_id", cid), doc, meta, 0.0))
                hit.keyword_score = score
                keyword_rank.append(cid)
                if len(keyword_rank) >= pool:
                    break

        for rank_list, label in ((semantic_rank, "semantic"), (keyword_rank, "keyword")):
            for pos, cid in enumerate(rank_list):
                ranked[cid].score += 1.0 / (RRF_K + pos + 1)
                ranked[cid].sources.append(label)

        # best chunk per parent document
        best: dict[str, SearchHit] = {}
        for hit in sorted(ranked.values(), key=lambda h: h.score, reverse=True):
            best.setdefault(hit.parent_id, hit)
        hits = list(best.values())[:k]
        for hit in hits:  # normalise fused score to 0..1 relative to the best possible RRF score
            hit.score = hit.score / (2.0 / (RRF_K + 1)) if mode == "hybrid" else hit.score * (RRF_K + 1)
        return hits
