from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.database import SessionLocal
from app.schemas import IngestResponse
from app.services.ollama import OllamaClient
from app.services.rag import ingest_bytes

router = APIRouter(prefix="/v1", tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    title: str | None = Form(None),
    source: str | None = Form(None),
) -> IngestResponse:
    if file is not None and file.filename:
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "empty file")
        filename = file.filename or "upload.bin"
        data = raw
    elif text is not None and text.strip():
        data = text.encode("utf-8")
        filename = "paste.txt"
    else:
        raise HTTPException(400, "provide file or text")

    db = SessionLocal()
    client = OllamaClient()
    try:
        doc_id, n = ingest_bytes(db, client, filename, data, title, source)
        return IngestResponse(document_id=UUID(str(doc_id)), chunks_created=n)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        client.close()
        db.close()
