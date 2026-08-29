from pydantic import BaseModel, Field

class SessionCreate(BaseModel):
    user_id: str = Field(default="", description="客服工号(起步可选)")

class SessionSummary(BaseModel):
    id: str
    title: str = "新会话"
    user_id: str = ""
    created_at: str