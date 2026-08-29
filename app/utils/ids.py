"""会话 id 生成(短 uuid hex)。"""
import uuid
def new_session_id() -> str:
    return uuid.uuid4().hex[:12]