# 观测接入方案（内网部署）· 设计稿

> 关联：D89（原子性边界）· D79（检索四段分值）· D88（llm_call 步级计量）
> 状态：**待决策**，未实施。内网环境（是否有 Docker、资源配额）确认后再定档。

---

## 一、先纠正一个前提：采集其实已经齐了

讨论「要不要上 Phoenix」之前，先核实项目现状——**结论是：核心采集早就落地了，不缺数据，缺的是可视化与通用评测指标。**

| 能力 | 是否已落地 | 代码位置 |
|---|---|---|
| 检索四段计时（embed/dense/BM25/rerank） | ✅ 已落 events | `search_tool.py:40-87` 出参 `timings` → `insurance.py:224-235` 进 `tool_meta.retrieval_timings_ms` → `agent_loop.py:585` 写入 retrieval 事件 `timings` → `events.py:41` 已校验 |
| 步级 LLM 计量（token / ttft / run_ms） | ✅ 已落 events | D88 新增 `llm_call` 事件 |
| 检索分数阶梯（dense/BM25/融合/rerank 四段分值） | ✅ 已保留 | D79 |
| 轮级 trace + 坏轮九分类 | ✅ 已有 | D78 异常定位器 |
| 坏例快照（完整 prompt/对话/输出） | ✅ 已有 | `_snap_bad` |
| **可视化：耗时瀑布 / 时间占比** | ❌ 缺 | 前端无 |
| **通用评测指标**（faithfulness / relevancy / context_precision） | ❌ 缺 | 自研 judge 有偏（κ=0.79，faithfulness 仅 0.53） |
| 偶发 vs 必现（同 query 多次对比） | ❌ 缺 | — |
| 语义聚类（哪些问题问得多/答不好） | ❌ 缺 | — |

**含义**：上 Phoenix 的增量价值主要在**可视化**和**通用 evals**，不是采集。这直接改变了性价比判断——原本以为要补采集（2-3 天），实际只需要补展示。

---

## 二、真正缺的四项，与对应的补法

| 缺口 | 轻方案 | 重方案（Phoenix） |
|---|---|---|
| ① 耗时可视化 | 前端加耗时瀑布图，**约 1 天**，零新增服务零运维 | Phoenix span 树自带，**0.5 天接入 + 长期运维一个服务** |
| ② 通用评测指标 | 引入 **RAGAS / DeepEval** 跑离线评测（pip 装，无服务），**约 1 天** | Phoenix evals 自带，**含 UI**，但要在其平台内做 |
| ③ 偶发 vs 必现 | 评估集加 `--repeat N`（已落地，D87） | Phoenix 可对比多次 trace |
| ④ 语义聚类 | 自写离线脚本（对 query 聚类），**约 0.5 天** | Phoenix datasets + 标注 |

**关键判断**：①②③ 都能用**不新增服务**的方式解决。Phoenix 的优势是**开箱即用 + 团队共享**，不是能力上的不可替代。

---

## 三、三个档位，按内网条件选

### 档位 A · 轻量自建（推荐先做）

- 前端加**耗时瀑布图**（用已有 `timings` + `llm_call`），回答「时间花在哪」
- 离线跑 **RAGAS** 补通用评测指标，与自研**程序化引用校验**并存
- 语义聚类写个离线脚本
- **工作量：约 2.5 天 | 新增服务：0 | 运维成本：0**
- 适用：内网无 Docker / 资源紧张 / 只有你一个人用

### 档位 B · Phoenix（推荐，若内网有 Docker）

- `docker run -p 6006:6006 -p 4317:4317 arizephoenix/phoenix:latest`（SQLite 起步，长期留存换 Postgres）
- 在 agent_loop / retrieval 加 OTel span，**现有 events 采集完全不动**
- 用其 evals 补通用指标
- **工作量：约 2-3 天 | 新增服务：1 | 资源：2C/2G**
- 适用：有 Docker、团队多人要看、想要 prompt 版本管理

### 档位 C · Langfuse（**不建议**）

- 7 个服务（web + worker + PG + ClickHouse + Redis + MinIO + init），4C/16G/100G 起
- 对这个单服务内部 copilot 属于杀鸡用牛刀，内网镜像离线导入是主卡点
- 仅当团队需要**多人标注 / 复杂数据集管理 / prompt 协作**时才考虑

---

## 四、若上 Phoenix：接入要点（遵守 D89）

1. **span 与 events 双写但不同步**：events 走 `store.append()`（事实源，同步、必须成功）；span 走 `BatchSpanProcessor`（异步、失败静默丢弃、绝不阻塞）
2. **绝不做原子性**：见 D89。验收方式是**停掉 collector 跑一轮，业务轮次正常完成且 events 条数不变**
3. **合规只认 events**：审计、引用溯源、历史角标一律查事实源，观测库只是排障辅助
4. **打点位置**（约 100-150 行，只加不改）：
   - `agent_loop.py` turn 循环 → 根 span（挂 trace_id、session_id）
   - `search_tool.search_knowledge` → 子 span，四段各一个 child span（复用现有 `timings`）
   - `llm/client.py` chat_stream → LLM span（model / ttft / tokens）
   - `_run_tool` → tool span
5. **内网离线安装**：`pip download arize-phoenix -d ./wheels` → 内网 `pip install --no-index --find-links=./wheels`（依赖含 opentelemetry-*、pandas、pyarrow，wheel 要打全）
6. **License 提醒**：Phoenix 服务端为 Elastic License 2.0（自托管商用 OK，不可转售为服务）；合规敏感单位走法务确认

---

## 五、可插拔隔离设计（关键：关=回到现状，零新增依赖）

**先纠正一个概念**：Phoenix 与自建观测**不是二选一切换关系，而是并存的可选增强**。
- events / 坏轮九分类 / `/api/metrics` / `/api/observability` —— 事实源与合规侧，**永远在**
- Phoenix span —— 额外的可视化与排障出口，**可随时关掉**

配置开关控制的是「要不要额外上报 span」，不是「用哪套观测系统」。合规审计只认 events。

### 照抄项目既有范式

- `config.py:109` `memory_enabled` ——「关=完全不参与，非侵入，行为与未加一致」
- `agent_loop.py:68` `except ImportError` ——「校验是加固不是门槛：缺依赖就跳过，绝不让功能失效」

### 配置（config.py）

```python
observability_enabled: bool = False     # 总开关，默认关（同 memory_enabled 风格）
observability_backend: str = "none"     # none | phoenix | otlp（预留换后端）
observability_endpoint: str = ""        # 如 http://phoenix:6006
observability_sample_rate: float = 1.0
```
并在 `.env` 映射表加三项。

### 实现骨架（app/observability/tracing.py，约 100 行）

```python
"""可插拔 tracing:关=零开销零依赖,开=Phoenix/OTLP。非侵入(照 memory_enabled 范式)。"""
import contextlib, logging
from app.config import cfg

log = logging.getLogger("insurance.agent")


class _NullSpan:
    def set_attribute(self, *a, **k): pass
    def set_status(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


@contextlib.contextmanager
def _null_span(name, **attrs):
    yield _NullSpan()


_impl = None
_inited = False


def _resolve():
    """延迟解析:关 / 缺依赖 / 初始化失败 → 一律降级空实现,绝不影响业务。"""
    global _impl, _inited
    if _inited:
        return _impl
    _inited = True
    _impl = _null_span
    if not getattr(cfg, "observability_enabled", False):
        return _impl
    try:                                  # 延迟 import:不在模块顶层,缺包也不崩
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:
        log.warning("observability disabled: opentelemetry not installed")
        return _impl
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=f"{cfg.observability_endpoint}/v1/traces")))
    trace.set_tracer_provider(provider)
    _t = trace.get_tracer("insurance-agent")

    def _span(name, **attrs):
        return _t.start_as_current_span(name, attributes={k: str(v) for k, v in attrs.items()})

    _impl = _span
    return _impl


def span(name, **attrs):
    return _resolve()(name, **attrs)
```

业务侧打点写法固定不变（4 处）：

```python
with span("retrieval.dense", top_k=k, hits=len(hits)):
    ...
```

关掉开关时 `span()` 返回空 contextmanager，**零开销、零依赖、零行为变化**。

### span 的意义是什么（先把价值构成讲清楚）

span 的核心价值是 **结构 + 时间 + 可聚合属性**，**不是内容存储**：

| 价值类型 | 归属 | 本项目现状 | 增量 |
|---|---|---|---|
| ① 调用结构（嵌套/依赖/串行并行） | span 擅长 | events 已有 step / tool_call 层级，且调用结构固定 | 低 |
| ② 分段耗时 | span 擅长 | events 已有 `timings` 四段 + `llm_call` | 低 |
| ③ 可聚合属性（model / status / top_k 分组统计） | span 擅长 | events 部分有 | **有增量** |
| ④ 内容原文（prompt / 回答 / 检索片段） | **归 events** | 已有且更强：**版本绑定 + 合规可审计** | span 不必存 |

**结论**：本项目 span 的增量主要落在 ③，① ② 已被 events 覆盖，④ 本来就该在 events。
→ **Phoenix 的真正价值是「开箱即用的 UI + 通用 evals」，而不是「补采集」。**
→ 也说明：**若只为可视化，前端加瀑布图（1 天）即可，不必引入 span 体系。**

### ⚠️ PII：按环境分级，不是一刀切「绝不带原文」

（初稿曾写「绝不能带原文」，过于绝对，已修正为分级策略。）

| 环境 | 策略 | 理由 |
|---|---|---|
| **生产**（真实用户数据） | 只记结构化属性；或**脱敏后摘要**（query 前 20 字 + hash） | 保险场景含手机号/身份证/保单号，泄露后果严重；且已有 PII 脱敏护栏，塞原文进 span = 绕过护栏 |
| **预发 / 测试**（内网、脱敏数据） | **可带原文** | 排障效率天差地别，风险可控 |
| **疑难排障** | 采样 1–5% 临时开启，事后关闭 | 兼顾可诊断性与合规 |

- ✅ 生产允许：耗时、token、top_k、命中数、chunk_id、doc_id、错误码、脱敏摘要
- ❌ 生产禁止：query 原文、回答正文、system prompt、手机号 / 身份证 / 保单号
- 实现上用 `observability_capture_content: bool` + `observability_sample_rate` 两个开关控制，默认关。

**注意**：Phoenix 的 evals 用的是 **dataset**（评测集），不是 span，所以「生产不带原文」**不影响 evals 的价值**——这是两码事。

### Phoenix 能替代什么（能力地图）

| 层级 | 组件 | 能否被 Phoenix 替代 |
|---|---|---|
| **可替代**<br>（Phoenix 更强） | 观测总览仪表盘 `/api/observability` | ✅ 可 |
| | 时间序列大盘 `/api/metrics/timeseries` | ✅ 可 |
| | span 树可视化 | ✅ 可（当前本来就没有，属新增） |
| **部分替代** | 告警 `check_metrics.py` | ⚠️ Phoenix 有基础告警，但**定制阈值与 trace_id 直达需保留** |
| | 坏轮检测 | ⚠️ 通用异常检测可替代，但**九分类（漏召/排序截断/模型没用等）是领域定制**，Phoenix 没有 |
| **不可替代** | events 事实源 | ❌ **合规铁律**，审计只认它 |
| | trace_id 体系（D85） | ❌ 业务标识，前端与审计都依赖 |
| | 引用溯源 / 角标 | ❌ 这是**业务功能**不是观测 |
| | 坏例快照 `_snap_bad` | ❌ 合规留证，且「好轮不存」的成本策略是定制的 |
| | 成本计量 | ❌ token 计费口径是业务定制 |

### 「配置切换」是伪需求：两个后端能力不对等

想法是「做成独立系统，配置切换 builtin / phoenix」。但**能对等切换的前提是能力对等，而这里不对等**：

- 切到 Phoenix → 丢掉坏轮九分类、trace 直达、成本计量口径
- 切回 builtin → 丢掉 span 树与开箱仪表盘

**不对等的系统之间做切换开关，用户永远在「功能降级」而不是「换实现」。**

附带成本：**维护两套 = 测试矩阵翻倍**（当前 354 项，再加一个后端维度），且切换路径本身最容易成为没人走的死代码。

### 正确做法：分层，而不是切换

| 层 | 归属 | 是否参与切换 |
|---|---|---|
| **通用观测**（span、时间序列、通用仪表盘） | 可选后端 | ✅ 可切（Phoenix / 其他 OTel backend） |
| **领域定制**（坏轮九分类、trace 直达、成本计量、坏例快照） | **永远自建** | ❌ 不参与切换 |
| **合规事实源**（events） | **永远自建** | ❌ 不参与切换 |

因此落地形态是**「并存 + 可选增强」**而非「二选一切换」：
- 自建观测页保留（它跟业务深度耦合）
- Phoenix 作为**额外视图**，开关控制是否启用（开关关闭时行为与现状完全一致）
- 合规链路全程走 events，与 Phoenix 无关

> 若确实需要「关掉自建观测页、只用 Phoenix」的开关，建议只切**展示入口**（前端 tab 显隐 / 路由指向），**不要切采集与判定的实现**——后者的切换没有收益只有风险。

## 六、验收标准

- [ ] 观测服务停止时，跑一轮问答：业务正常完成，events 条数与开启时一致（D89 核心验收）
- [ ] 开启时能在 UI 看到一轮的完整 span 树，含检索四段耗时
- [ ] 通用评测指标（faithfulness 等）可复现，与自研 judge 结论做交叉验证
- [ ] 全量 354 项测试仍全绿（打点不得改变现有行为）
- [ ] 回放测试通过（改了 prompt / 工具 schema 必跑）
