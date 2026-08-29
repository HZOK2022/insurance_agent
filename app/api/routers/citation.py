from fastapi import APIRouter, HTTPException

from app.api.schemas.citation import CitationResponse
from app.api.services import citation_service, container

router = APIRouter(prefix="/api/sessions", tags=["citation"])


@router.get("/{sid}/citation/{chunk_id}", response_model=CitationResponse)
def citation(sid: str, chunk_id: str):
    res = citation_service.get_citation(container.get_store(), sid, chunk_id)
    if res is None:
        raise HTTPException(status_code=404, detail="chunk 未找到")
    return res