# Python 项目骨架(docs/project-skeleton.md)

## dsh 源码位置(只读参照)

| 项 | 值 |
|---|---|
| 本地 checkout(当前机器) | `D:\LLM\deepseek-harness-master`(解压副本,非 git 仓库) |
| 公开仓库(可克隆/查历史) | `https://github.com/deepseek-ai/deepseek-harness` |
| 参照版本 | `0.1.2-alpha.1`(apps/cli/package.json) |
| 用法 | 按下方模块对照表打开对应包文件;只读参照,不复制代码、不成为运行依赖 |


## 目录树

```
insurance-agent/
├── README.md
├── .env.example
├── requirements.txt              # fastapi uvicorn pydantic openai qdrant-client redis
├── config/
│   └── config.py                 # 读取 .env,集中所有上限/审批/预算阈值
├── app/
│   ├── main.py                   # ⑦ FastAPI 入口(HTTP+SSE,鉴权,限流中间件)
│   ├── loop/                     # ① Agent Loop(抄 core/agent-loop)
│   │   ├── loop.py               #    turn/step 状态机
│   │   └── limits.py             #    步数/token/超时上限,取消传播
│   ├── llm/                      # ② LLM 客户端(抄 llm/llm + llm-deepseek + token-meter)
│   │   ├── client.py             #    DeepSeek 流式,重试/熔断
│   │   ├── structured.py         #    结构化输出 answer+citations,畸形 JSON 容错
│   │   └── meter.py              #    token 计量
│   ├── tools/                    # ③ 工具层(抄 core/tools 管线)
│   │   ├── registry.py           #    Map<name,handler>+schema
│   │   ├── pipeline.py           #    校验→执行→回填(截断,完整结果上限)
│   │   └── builtin/              #    外部 API 工具:读放行/写审批
│   ├── session/                  # ④ 会话与上下文(抄 core/session + persistence-sqlite)
│   │   ├── store.py              #    SQLite append-only store(WAL,版本 fail-closed)
│   │   ├── events.py             #    事件类型注册表(Pydantic 校验)
│   │   ├── derive.py             #    从日志组装上下文,窗口裁剪
│   │   └── resume.py             #    崩溃恢复
│   ├── retrieval/                # ⑧ 知识检索(核心,自写)
│   │   ├── embedder.py           #    bge-m3 本地嵌入 + Redis 缓存
│   │   ├── qdrant_store.py       #    upsert/混合检索/版本过滤
│   │   ├── search_tool.py        #    search_knowledge 工具定义
│   │   └── ingest/               #    三条摄取管道
│   │       ├── documents.py      #    条款 PDF/Word 解析+结构分块
│   │       ├── structured.py     #    结构化数据桥接
│   │       └── faq.py            #    FAQ 审核入库
│   ├── guardrails/               # ⑤ 安全护栏(抄 sandbox + user-approval)
│   │   ├── approval.py           #    持久化审批流(读放行/写审批)
│   │   ├── policies.py           #    白/黑名单,输出预算
│   │   └── isolation.py          #    执行隔离(cwd 监狱起步)
│   ├── audit/                    # ⑨ 审计/追溯层(events 查询视图)
│   │   ├── recorder.py           #    检索快照入日志
│   │   └── queries.py            #    历史检索/审计导出
│   └── observability/            # ⑥ 可观测(抄 session-telemetry)
│       ├── trace.py              #    trace_id,结构化日志(structlog)
│       └── metrics.py            #    延迟/token/成本
├── tests/
│   ├── replay/                   # 录制-回放测试(抄 test-support/llm-replay)
│   └── unit/
├── scripts/
│   ├── ingest_kb.py              # 知识库摄取 CLI
│   └── replay_test.py            # 回放测试入口
└── docs/                         # 本文档 + 架构图
```

## 每个模块对应的 dsh 源码参照(照着抄)

| 模块 | dsh 参照 | 抄什么 | Python 怎么写 |
|---|---|---|---|
| loop | `packages/core/agent-loop/src/index.ts` | turn/step 状态机、取消传播 | asyncio 状态机 |
| llm | `packages/llm/llm`、`llm-deepseek`、`token-meter` | 适配器形态、流式、计量 | openai SDK |
| tools | `packages/core/tools/src` | 校验→执行→回填管线、完整结果上限 | Pydantic + 自写管线 |
| session | `packages/core/session`、`session-persistence-sqlite` | append-only、版本 fail-closed、日志即真相 | sqlite3 + 注册表 |
| guardrails | `packages/sandbox/*`、`interaction/user-approval`、`permission-presets` | 审批持久化、白名单 | 自写 approval |
| observability | `session-telemetry`、`session-telemetry-otel` | 结构化日志+trace | structlog |
| 测试 | `test-support/llm-replay`、`llm-mock-server`、`test:snapshot` 脚本 | 录制-回放、无 key 回归 | VCR 模式自写 |

## 第一天必做(顺序)

1. `app/session/events.py` + `store.py`:事件注册表 + append-only + 版本 fail-closed(全系统地基)
2. `tests/replay/`:录制一个真实问答,写回放测试骨架(没有回放测试,后面每次改 prompt/条款都提心吊胆)
3. `config/config.py`:把所有上限/预算集中,禁止散落硬编码
4. 引用验收用例:`{answer, citations}` 解析 → 角标 → SQLite 原文,写成一个端到端测试