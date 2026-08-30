from pydantic import BaseModel, Field

class PromptRequest(BaseModel):
    text: str = Field(..., min_length=1, description="客服提问")
    client_time: str | None = None
    model: str | None = Field(default=None, description="模型:deepseek-v4-flash / deepseek-v4-pro")