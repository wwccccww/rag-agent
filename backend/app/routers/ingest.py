from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import settings
from app.database import SessionLocal
from app.schemas import IngestResponse
from app.services.ollama import OllamaClient
from app.kb import normalize_doc_type
from app.services.kb_acl import effective_kb_collection
from app.services.rag import ingest_bytes
from app.services.text_extract import fetch_url

router = APIRouter(prefix="/v1", tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    url: str | None = Form(None),
    title: str | None = Form(None),
    source: str | None = Form(None),
    kb_collection: str | None = Form(None),
    doc_type: str | None = Form(None),
    user_id: str = Form("demo"),
) -> IngestResponse:
    db = SessionLocal()
    client = OllamaClient()
    try:
        if file is not None and file.filename:
            raw = await file.read()
            if not raw:
                raise HTTPException(400, "empty file")
            max_bytes = settings.max_upload_mb * 1024 * 1024
            if len(raw) > max_bytes:
                raise HTTPException(413, f"文件过大（{len(raw) // 1024 // 1024} MB），上限为 {settings.max_upload_mb} MB")
            filename = file.filename or "upload.bin"
            data = raw
            ingest_source = source or filename
            ingest_title = title or filename

        elif url is not None and url.strip():
            try:
                page_text, page_title = fetch_url(url.strip())
            except Exception as e:
                raise HTTPException(400, f"URL 抓取失败: {e}") from e
            data = page_text.encode("utf-8")
            filename = "webpage.txt"
            ingest_source = source or url.strip()
            ingest_title = title or page_title or url.strip()

        elif text is not None and text.strip():
            data = text.encode("utf-8")
            filename = "paste.txt"
            ingest_source = source or "paste"
            ingest_title = title or "粘贴文本"

        else:
            raise HTTPException(400, "请提供 file、url 或 text 其中之一")

        try:
            dtype = normalize_doc_type(doc_type)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

        try:
            coll = effective_kb_collection(db, user_id.strip() or "demo", kb_collection)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

        doc_id, n = ingest_bytes(
            db, client, filename, data, ingest_title, ingest_source, kb_collection=coll, doc_type=dtype
        )
        return IngestResponse(document_id=UUID(str(doc_id)), chunks_created=n)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        client.close()
        db.close()
