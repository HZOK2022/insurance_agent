"""会话事件(日志)模型。"""
from pydantic import BaseModel

class EventModel(BaseModel):
    seq: int
    type: str
    ts: str
    payload: dict