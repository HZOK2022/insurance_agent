from typing import Iterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.schemas.prompt import PromptRequest
from app.api.services import agent_service, container
from app.loop.abort import request as request_abort, reserve as reserve_abort
from app.utils.sse import event_frame

router = APIRouter(prefix="/api/sessions", tags=["prompt"])


@router.post("/{sid}/prompt")
def prompt(sid: str, body: PromptRequest):
    # 先"预约"中止位(handler 同步段,StreamingResponse 构造前):
    # SSE 生成器要等客户端首拉 body 才开始迭代,若等生成器跑起来再登记,用户在
    # LLM 首 token 前点"停止"时 abort 会落在登记之前 → 置位丢失 → turn 白跑。
    reserve_abort(sid)

    def gen() -> Iterator[str]:
        for ev in agent_service.run_prompt(container.get_store(), container.get_llm(), container.get_insurance_bundle(), sid, body.text, model=body.model):
            yield event_frame(ev)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{sid}/abort")
def abort(sid: str):
    """显式"停止":给该会话进行中的回合置中止位。

    为什么不是"前端断开 SSE 就行":Starlette 的 iterate_in_threadpool 不保证在客户端断开时
    close() 底层同步生成器(实测该版本),GeneratorExit 可能不触发 → 后端这一轮会跑完(白烧 token)。
    置位后 loop 在下一个 step 边界/下一个 chunk 处收尾,保留已流出的部分回答,并照常写
    turn_end(reason=interrupted)—— 前端不断流,能收到完整终结事件。
    ok=False 表示该会话当前没有进行中的回合(置位无对象,调用无害)。
    """
    return {"ok": request_abort(sid), "session_id": sid}
