# 12 工具契约:参数校验前置 + 让失败对模型也是一等信号

> 对应决策:DECISIONS **D63**;代码:`app/loop/agent_loop.py`(`validate_tool_arguments` / `tool_parameters_schema` / `_run_tool`)、`tests/test_tool_args.py`。

## 这一阶段解决什么问题

**不是"让报错信息好看点"这种装饰性需求,而是堵一条会产出带引用错误答案的通道。**

改造前 `_run_tool` 的执行路径只有三步:查表 → 调 handler → 捕获异常。schema 只用于**发给 LLM**,从不校验 LLM 返回的参数。而 handler 里普遍是这种写法:

```python
query = (args or {}).get("query") or ""     # insurance.py
```

模型把参数名写成 `q` 或 `keyword`,`query` 就变成空串 —— **不抛异常、不返空、真的去检索**。空串照样能被 embedding,Qdrant 照样返回 top_k 个向量最近邻。模型拿到一堆格式完美、语义无关的结果,于是正常作答,还规规矩矩挂上 [1][2][3] 引用角标。

产出物是:**有引用、语气自信、内容错误**的答案。这是保险客服最不能接受的输出形态。

而且 D48 的 RAG 投毒防护**防不住它** —— 投毒防护拦的是"外部内容伪装成指令",而这里是系统自己给自己喂了垃圾,格式完全合法。

**硬证据**:STATUS 三点五「评估驱动修复 ③retrieval query=None」—— 这在评估时真实发生过。当时的修法是在 handler 里补 `or ""`,属于打补丁;schema 校验才是治本。

一句话:**假成功比崩溃危险得多。崩溃至少有 `tool_error` 可收敛,假成功连失败都算不上。**

## 对应 dsh 源码

- `packages/core/tools/src` —— dsh 的工具管线是 **校验 → 执行 → 回填** 三段,我们此前只实现了后两段
- `packages/core/agent-loop/src/tool-calls.ts` —— 工具调用块的组装与回喂
- dsh 的失败语义是 **一等错误结果**(`isError` + `error.code`),即失败和成功一样是结构化的返回值,不是异常

## 设计要点

### 1. 校验放在进 handler 之前,不通过就一次都不执行

这是整个改动的核心。校验的价值不在"报错",在**拦截** —— 不让一个注定产出垃圾的调用发生。

### 2. 诊断必须可操作(模型能照着改)

对比一下模型收到什么:

```
改前:  工具「search_knowledge」调用失败,未取得结果,请基于已有资料回答。
改后:  【工具调用失败】参数不合契约:缺少必填字段 query。请补齐后重新调用,不要用相近的字段名代替。
```

前者模型只知道"失败了",不知道改哪里,只能盲目重试或放弃;在算费场景,放弃就意味着**自己估一个数字**。后者直接告诉它补什么。

只翻译最高频的三类(`required` / `type` / `enum`),其余原样透出 `e.message`;所有值经 `_short()` 截断,避免诊断文案反过来把上下文撑爆。

### 3. 放行条件:校验是加固,不是门槛

三种情况一律放行,退化为改动前的行为:

| 情况 | 为什么放行 |
|---|---|
| 缺 `jsonschema` 依赖 | 加固不该变成硬依赖,不能让可选库缺席导致工具集体失效 |
| 工具没声明 `parameters` | 兼容旧写法(如 `test_tool_error.py` 里的工具) |
| 校验过程自身抛异常 | 校验器出 bug 不该拖垮主流程 |

这符合项目的一贯取舍:**基础设施缺失时降级,而不是拒绝服务**(对比事件注册表的 fail-closed —— 那个保护的是数据完整性,必须硬)。

### 4. 放弃的替代方案

- **不改 schema 追加 `additionalProperties: false`** —— 会改动发给 LLM 的 schema 内容,触发回放测试 churn;而当前 `required` 已经能拦住"参数名写错"这一主要场景,收益不抵成本。
- **不在 `_run_tool` 外面包 `tool_timeout_seconds`** —— handler 跑在 FastAPI `iterate_in_threadpool` 的**工作线程**里(见 `app/loop/abort.py` 注释),所以 `signal.alarm` 只对主线程有效、用不了;`ThreadPoolExecutor` + `future.result(timeout)` 是**假熔断**:超时后调用方不等了,线程还在后台跑(资源泄露),若工具是写操作还会"用户看到失败、实际写了一半"。而且 I/O 层本就都设了 timeout(reranker 30s / embedder 30s / llm 120s),收益低、风险高。真正没设超时的只有 `qdrant_store.py:29` 的 `QdrantClient(url=url)`(靠库默认)。

### 5. 同批补上 D40 的另一半

D40 说"工具失败是一等错误结果",但当时只对 **UI 和审计**成立:

```python
yield self._emit("tool_result", {"tool": name, "ok": _tok, ..., "error": _terr})  # 只进事件
conversation.append({"role": "tool", ..., "content": pruned})                      # 只喂 content
```

**`ok` 和 `error_code` 从来没进过 conversation。** 前端轨迹 tab 能看到红色失败标记,模型那边只有一段自然语言,只能从字面猜"这算不算失败"。现在失败时加前缀 `【工具调用失败】`,不改事件 schema、不动协议,成本最低的结构化信号。

## Python 实现

```python
def validate_tool_arguments(tool: dict, args: Any) -> str | None:
    """返回 None = 通过;否则返回喂给模型的诊断文案(不抛异常、不泄漏内部细节)。"""
    if _jsonschema is None:
        return None                                  # 缺依赖 → 放行
    params = tool_parameters_schema(tool)
    if not params:
        return None                                  # 没声明 parameters → 放行
    if not isinstance(args, dict):
        return (f"参数不合契约:参数必须是 JSON 对象,实际收到 {type(args).__name__};"
                f"请按工具说明重新调用,不要省略参数名。")
    try:
        _jsonschema.validate(instance=args, schema=params)
    except _JsonSchemaError as e:
        return _describe_schema_error(e)             # 中文可操作诊断
    except Exception:
        logger.exception("工具参数校验自身异常,放行")
        return None
    return None
```

在 `_run_tool` 里前置:

```python
tool = self.tools.get(name)
if not tool:
    return "（无此工具）", None, False, "unknown_tool"
_bad_args = validate_tool_arguments(tool, args)
if _bad_args:
    logger.warning("工具参数不合契约 tool=%s args=%s → invalid_arguments", name, _short(args))
    return _bad_args, None, False, "invalid_arguments"   # handler 一次都不执行
```

`tool_parameters_schema()` 兼容两种形态:OpenAI 原生 `{"function": {"parameters": ...}}` 与裸 `{"parameters": ...}`。

## 验收测试

`tests/test_tool_args.py`,16 项,分三层:

**纯函数层**(`SchemaExtractionTest`)—— schema 提取的三种形态。

**校验行为层**(`ValidateBehaviorTest`):
- `test_missing_required_is_blocked` / `test_wrong_type_is_blocked` / `test_enum_violation_is_blocked`
- `test_non_dict_args_is_blocked` —— `parse_tool_arguments` 解析失败会返回原始**字符串**,不是 dict
- `test_tool_without_parameters_is_not_blocked` —— 不误伤没声明 schema 的工具
- `test_missing_jsonschema_degrades_to_pass` —— mock 掉依赖,验证降级

**集成层**(`GuardIntegrationTest`):
- `test_wrong_field_name_never_reaches_handler` —— **核心**:断言 `seen == []`,参数不合契约时 handler 绝不能被执行
- `test_diagnosis_is_actionable_for_model` —— 回喂内容含字段名 + `【工具调用失败】` 前缀
- `test_invalid_args_still_logged_for_audit` —— 铁律 1:被拒的参数仍留在 `tool_call` 事件里可审计
- `test_handler_exception_still_maps_to_tool_error` —— 校验不替换原有异常分类,抛异常仍归 `tool_error` 而非 `invalid_arguments`
- `test_turn_completes_after_invalid_args` —— 被拒后 loop 必须收敛,不能悬挂

全量:rag_env **251 项 OK**(235 + 16,无回归)。

## 手动测试

1. 装依赖:`rag_env\Scripts\python.exe -m pip install jsonschema`
2. 起后端:`rag_env\Scripts\python.exe run.py`
3. 聊天页问一个知识问题,观察后端日志 —— 正常情况下不该出现 `工具参数不合契约`(说明模型参数填对了)
4. 想验证拦截效果:临时把 `insurance.py` 的 `SEARCH_TOOL` 的 `required` 改成 `["query", "not_existed"]`,再问同一个问题,应看到模型收到"缺少必填字段"后**自己改对并重试**,而不是编答案

## 你学到了什么

- **假成功比崩溃危险**:一个不报错但语义错误的返回,会让模型产出带引用的错误答案;防御的重点是拦截,不是兜底
- **校验器要设计放行条件**:可选加固缺依赖时应该降级,不能变成硬门槛
- **诊断的可操作性 = 模型的自我修复能力**:告诉它"失败了"没用,告诉它"缺 query"才有意义
- **结构化信号要检查流向**:`ok` / `error_code` 进了事件不等于进了模型;UI 可见和模型可见是两件事
- **先读代码再下判断**:见踩坑记录 1

## 踩坑记录

1. **把"参数错 → 崩溃 → 固定文案"当成唯一路径,是错的。** 我一开始推断 `calculate_premium` 漏传 `items` 会崩溃,进而让模型"被迫手算保费"。读了 `premium.py:205-227` 才发现它的手写防御相当完整:产品为空、产品不存在、`int(age)` 失败、`items` 非列表 —— **全是正常返回 dict,不抛异常**,而且文案是"请至少指定一个方案(items)"这种模型能看懂的提示。真正常常`_run_tool` except 分支的只有 handler 内部真崩溃这一种,属低频。
   **教训**:别用"工具调用失败"这句笼统文案去反推实现,要看每个 handler 实际怎么取值。两个工具的参数健壮性可以天差地别。

2. **`ok` / `error_code` 不进 conversation 是读 392-397 行时才发现的。** D40 做了一半 —— 对 UI 是一等错误结果,对模型不是。查"失败是否被感知"时,要同时看**事件流**和**回喂模型的 conversation**两条路径。

3. **`jsonschema` 在 rag_env 里没装。** 装之前先确认了 schema 的三个事实:两个工具的 `query` 都是 required(所以"参数名写错"确实拦得住)、schema 只用了 type/properties/required/items 这几个关键字、jsonschema 是纯 Python 无重依赖。

4. **测试里用自建 schema,不 import 业务模块。** `app.businesses.insurance` 会带出 `retrieval.search_tool` 一整条重依赖链,拖慢全量测试;而这里测的是**校验机制**,不是具体业务 schema,自建同构 schema 更快更稳。
