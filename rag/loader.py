"""Load markdown knowledge documents (YAML front matter + body) into the knowledge base."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from rag.store import DOC_TYPES, KnowledgeBase

logger = logging.getLogger(__name__)

FOLDER_TYPES = {
    "runbooks": "runbook",
    "sops": "sop",
    "postmortems": "postmortem",
    "infrastructure": "infrastructure",
    "incidents": "incident",
}


def parse_document(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
    return meta, body


def seed_knowledge_base(kb: KnowledgeBase, directory: str) -> int:
    """Index every *.md file under `directory`. Idempotent (documents are upserted by id)."""
    root = Path(directory)
    if not root.is_dir():
        logger.info("Knowledge seed directory %s not found; skipping", root)
        return 0
    loaded = 0
    for path in sorted(root.rglob("*.md")):
        try:
            meta, body = parse_document(path)
            doc_type = meta.pop("type", None) or FOLDER_TYPES.get(path.parent.name, "infrastructure")
            if doc_type not in DOC_TYPES:
                logger.warning("Skipping %s: unknown type %s", path, doc_type)
                continue
            doc_id = str(meta.pop("id", path.stem))
            if "steps" in meta:
                meta["steps"] = json.dumps(meta["steps"])
            meta.setdefault("title", path.stem.replace("-", " ").title())
            meta["source"] = "seed"
            kb.upsert(doc_id, f"{meta['title']}\n\n{body}", doc_type, meta)
            loaded += 1
        except Exception:
            logger.exception("Failed to load knowledge document %s", path)
    logger.info("Seeded %d knowledge documents", loaded)
    return loaded
