"""Knowledge base and conversational AI endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.container import Container
from backend.deps import Principal, approver, get_container, operator, viewer
from backend.schemas import ChatRequest, ChatResponse, KnowledgeDocumentIn, KnowledgeSearch
from services.knowledge_service import KnowledgeError

router = APIRouter(tags=["knowledge"])


@router.post("/knowledge/search", summary="Hybrid (semantic + keyword) search with metadata filtering")
def search(body: KnowledgeSearch, _: Principal = Depends(viewer), container: Container = Depends(get_container)) -> dict:
    try:
        results = container.knowledge.search(body.query, body.k, body.mode, body.doc_types, body.filters)
    except KnowledgeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"query": body.query, "mode": body.mode, "results": results}


@router.get("/knowledge/stats")
def stats(_: Principal = Depends(viewer), container: Container = Depends(get_container)) -> dict:
    try:
        return container.knowledge.stats()
    except KnowledgeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/knowledge/documents")
def list_documents(doc_type: str | None = None, limit: int = Query(200, ge=1, le=1000), _: Principal = Depends(viewer),
                   container: Container = Depends(get_container)) -> list[dict[str, Any]]:
    try:
        return container.knowledge.list_documents(doc_type, limit)
    except KnowledgeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/knowledge/documents/{doc_id}")
def get_document(doc_id: str, _: Principal = Depends(viewer), container: Container = Depends(get_container)) -> dict:
    try:
        return container.knowledge.get(doc_id)
    except KnowledgeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/knowledge/documents", status_code=status.HTTP_201_CREATED,
             summary="Add or replace a runbook/SOP/postmortem (approver role: runbook steps are executable)")
def upsert_document(body: KnowledgeDocumentIn, principal: Principal = Depends(approver),
                    container: Container = Depends(get_container)) -> dict:
    try:
        chunks = container.knowledge.add(body.id, body.doc_type, body.title, body.content, body.metadata,
                                         principal.email, principal.role)
    except KnowledgeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"id": body.id, "chunks": chunks}


@router.delete("/knowledge/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, principal: Principal = Depends(approver), container: Container = Depends(get_container)) -> None:
    try:
        container.knowledge.delete(doc_id, principal.email, principal.role)
    except KnowledgeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
def chat(body: ChatRequest, principal: Principal = Depends(operator), container: Container = Depends(get_container)):
    result = container.chat.reply(str(principal.user.id), body.message, body.session_id)
    container.audit.log("chat.message", principal.email, principal.role, "chat", result["session_id"],
                        {"chars": len(body.message)}, ip=principal.ip)
    return result


@router.get("/chat/{session_id}", tags=["chat"])
def chat_history(session_id: str, principal: Principal = Depends(operator),
                 container: Container = Depends(get_container)) -> list[dict[str, Any]]:
    return container.chat.history(str(principal.user.id), session_id)
