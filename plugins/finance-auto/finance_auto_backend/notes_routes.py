"""HTTP layer for the M3 Biz Stage 3 report-notes feature.

Eight endpoints under the plugin prefix:

* ``POST   /orgs/{org_id}/notes/generate``                      — generate the document.
* ``GET    /orgs/{org_id}/notes/documents``                     — list documents per org.
* ``GET    /orgs/{org_id}/notes/documents/{doc_id}``            — document metadata.
* ``GET    /orgs/{org_id}/notes/documents/{doc_id}/notes``      — list per-section notes.
* ``PATCH  /orgs/{org_id}/notes/{note_id}``                     — update content (409 on
                                                                   version mismatch).
* ``POST   /orgs/{org_id}/notes/documents/{doc_id}/finalize``   — flip status to finalized.
* ``GET    /orgs/{org_id}/notes/documents/{doc_id}/export``     — markdown / docx bundle.
* ``GET    /notes/templates``                                   — list the 8 seeded templates.

The router stays a thin shim — the heavy lifting lives in
``services/notes_generator.NotesGenerator``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .rbac import require_permission
from .services.notes_generator import NotesGenerator, NotesGeneratorError

if TYPE_CHECKING:
    from .routes import FinanceAutoService


class _GenerateRequest(BaseModel):
    period_id: str = Field(..., min_length=1, description="会计期间 ID，如 2025-FY")
    sections: list[str] | None = Field(
        default=None,
        description="可选：要生成的章节子集；缺省则覆盖 8 大类全部",
    )
    user_id: str = Field(default="local", description="发起人 user_id，默认本地用户")


class _PatchNoteRequest(BaseModel):
    content: str = Field(..., description="完整覆盖正文内容")
    version: int = Field(..., ge=1, description="客户端持有的版本号；用于乐观锁")


def register_notes_endpoints(router: APIRouter, service: "FinanceAutoService") -> None:
    gen = NotesGenerator(service)

    @router.post(
        "/orgs/{org_id}/notes/generate",
        status_code=201,
        summary="生成报表附注文档 (8 大类)",
    )
    async def generate_notes(
        org_id: str,
        payload: _GenerateRequest,
        _user: str = Depends(require_permission("notes", "generate")),
    ) -> dict[str, Any]:
        try:
            result = await gen.generate(
                org_id=org_id,
                period_id=payload.period_id,
                sections=payload.sections,
                user_id=payload.user_id,
            )
        except NotesGeneratorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "document_id": result["document_id"],
            "org_id": result["org_id"],
            "period_id": result["period_id"],
            "status": result["status"],
            "notes_count": result["total"],
            "notes": result["notes"],
        }

    @router.get(
        "/orgs/{org_id}/notes/documents",
        summary="列出某账套的附注文档",
    )
    async def list_documents(
        org_id: str, period_id: str | None = Query(default=None)
    ) -> dict[str, Any]:
        # 404 cleanly if the org id is bogus.
        await service.get_org(org_id)
        docs = await gen.list_documents(org_id=org_id, period_id=period_id)
        return {"documents": docs, "total": len(docs)}

    @router.get(
        "/orgs/{org_id}/notes/documents/{doc_id}",
        summary="读取某份附注文档元信息",
    )
    async def get_document(org_id: str, doc_id: int) -> dict[str, Any]:
        await service.get_org(org_id)
        try:
            return await gen.get_document(document_id=doc_id)
        except NotesGeneratorError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/orgs/{org_id}/notes/documents/{doc_id}/notes",
        summary="读取某份附注文档下的全部 note",
    )
    async def list_notes(org_id: str, doc_id: int) -> dict[str, Any]:
        await service.get_org(org_id)
        try:
            notes = await gen.list_notes(document_id=doc_id)
        except NotesGeneratorError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"document_id": doc_id, "notes": notes, "total": len(notes)}

    @router.patch(
        "/orgs/{org_id}/notes/{note_id}",
        summary="编辑某条附注内容（乐观锁，409 on version 冲突）",
    )
    async def patch_note(
        org_id: str,
        note_id: int,
        payload: _PatchNoteRequest,
        _user: str = Depends(require_permission("notes", "edit")),
    ) -> dict[str, Any]:
        await service.get_org(org_id)
        # ``update_note`` already raises 404 / 409 HTTPException itself.
        return await gen.update_note(
            note_id=note_id,
            content=payload.content,
            expected_version=payload.version,
        )

    @router.post(
        "/orgs/{org_id}/notes/documents/{doc_id}/finalize",
        summary="将附注文档置为 finalized（draft → in_review → finalized 的终态）",
    )
    async def finalize(
        org_id: str,
        doc_id: int,
        _user: str = Depends(require_permission("notes", "edit")),
    ) -> dict[str, Any]:
        await service.get_org(org_id)
        try:
            return await gen.finalize_document(document_id=doc_id)
        except NotesGeneratorError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/orgs/{org_id}/notes/documents/{doc_id}/export",
        summary="导出附注文档（markdown bundle 或 .docx 二进制）",
    )
    async def export_document(
        org_id: str,
        doc_id: int,
        format: str = Query(default="md", pattern="^(md|docx)$"),
    ) -> Response:
        await service.get_org(org_id)
        try:
            blob = await gen.export_docx(document_id=doc_id)
        except NotesGeneratorError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        media_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if format == "docx" and not blob.startswith(b"<bundle>")
            else "application/octet-stream"
        )
        filename = f"notes_{doc_id}.{format}"
        return Response(
            content=blob,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get(
        "/notes/templates",
        summary="列出附注模板（按 accounting_standard 过滤）",
    )
    async def list_templates(
        accounting_standard: str | None = Query(default=None),
    ) -> dict[str, Any]:
        templates = await gen.list_templates(accounting_standard=accounting_standard)
        return {"templates": templates, "total": len(templates)}


__all__ = ["register_notes_endpoints"]
