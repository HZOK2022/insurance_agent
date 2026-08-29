from typing import Iterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.schemas.prompt import PromptRequest
from app.api.services import agent_service, container
from app.utils.sse import event_frame

router = APIRouter(prefix="/api/sessions", tags=["prompt"])


@router.post("/{sid}/prompt")
def prompt(sid: str, body: PromptRequest):
    def gen() -> Iterator[str]:
        for ev in agent_service.run_prompt(container.get_loop(), container.get_store(), sid, body.text):
            yield event_frame(ev)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
