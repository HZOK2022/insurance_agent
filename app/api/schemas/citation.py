from pydantic import BaseModel

class CitationResponse(BaseModel):
    content: str
    source: str
    doc_id: str
    version: str
    section: str = ""