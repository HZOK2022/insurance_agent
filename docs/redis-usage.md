# Redis 加速层设计(docs/redis-usage.md)

Redis 是**可丢失**的加速层:清空它,系统照常跑,只是慢。绝不承担唯一真相。

## Key 设计

| Key | 值 | TTL | 使用者 |
|---|---|---|---|
| `emb:{sha256(text)}:{model}` | 向量(JSON/msgpack) | 30d | ②/⑧ 嵌入缓存(纯函数,零风险,优先做) |
| `toolcache:{tool}:{sha256(args)}` | 工具结果 JSON | 工具自定(5m-1h) | ③ 仅**幂等只读**工具 |
| `rl:{user_id}` | 请求计数 | 滑动窗口 | ⑦ 限流 |
| `budget:{user_id}:{date}` | 当日 token 用量(INCRBY) | 当天结束 | ⑦ 每客服预算 |
| `lock:approval:{session_id}` | 1 | 60s | ⑤ 审批去重 |
| `hot:{session_id}:recent` | 最近 N 条消息 | 1h | ④ 可选热态(非必需) |

## 缓存规则(写死,不许破例)

1. **不缓存 LLM 补全**:agent 请求带动态上下文,语义缓存会给出错误回答
2. **不缓存被依赖的读**:agent 读了缓存再写真实数据 = 脏写;只有纯只读、确定性工具可缓存
3. 缓存命中**必须落日志**(铁律 1:模型可见 ⟺ 已记录,含"用的是缓存结果")
4. 每条缓存带 TTL 与大小上限;超限直接不缓存

## 单进程阶段的取舍

- 限流/预算:用,简单可靠
- 嵌入缓存:用,收益最大
- 工具缓存:按需,先给检索/产品查询类只读工具开
- pub/sub SSE 扇出:**不需要**(单进程内 asyncio 广播即可);多实例时再迁 Redis pub/sub
- 分布式锁:**不需要**(单写者);审批去重用内存即可,Redis 锁留位

## 淘汰策略

`maxmemory-policy volatile-lru`(只淘汰带 TTL 的键,锁/限流键不被 LRU 误伤)。
