"""Embedding providers: OpenAI/Azure text-embedding-3-large when configured, deterministic local hashing otherwise."""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.\-]*")
_STOP = frozenset("a an and are as at be by for from has have in is it its of on or that the this to was were will with".split())


_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def tokenize(text: str) -> list[str]:
    """Lower-cased tokens; compound identifiers (CrashLoopBackOff, crash-loop) also contribute their parts."""
    tokens = _TOKEN_RE.findall(text.lower())
    split = _CAMEL_RE.sub(" ", text)
    if split != text:
        tokens += _TOKEN_RE.findall(split.lower())
    for token in list(tokens):
        parts = [p for p in re.split(r"[_.\-]", token) if p]
        if len(parts) > 1:
            tokens.extend(parts)
    return [t for t in tokens if t not in _STOP]


class Embedder(Protocol):
    id: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Signed feature-hashing embedder over unigrams and bigrams.

    Lexical rather than truly semantic, but deterministic, offline and dependency free. It is used when no
    embedding model is configured; configure Azure OpenAI / OpenAI to get text-embedding-3-large.
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.id = f"hash{dim}"

    def _vector(self, text: str) -> list[float]:
        tokens = tokenize(text)
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        vec = [0.0] * self.dim
        for feat in features:
            digest = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class LLMEmbedder:
    """Adapter over agents.common.llm.OpenAILLM."""

    def __init__(self, llm) -> None:  # noqa: ANN001
        self._llm = llm
        safe = re.sub(r"[^a-zA-Z0-9]+", "_", llm.name).strip("_")
        self.id = safe[:40]

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            out.extend(self._llm.embed(texts[i : i + 64]))
        return out


def build_embedder(llm) -> Embedder:  # noqa: ANN001
    if llm is not None and getattr(llm, "can_embed", False):
        return LLMEmbedder(llm)
    return HashingEmbedder()
