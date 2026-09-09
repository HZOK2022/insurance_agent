# 部署拓扑(生产两机,MySQL 事实源)

> 生产环境:**应用一台服务器 + 数据库一台服务器**。应用连数据库服务器的 MySQL(=事实源);SQLite 仅开发/测试回退。

## 一、两机落位
```
[应用服务器 A]                                 [数据库服务器 B]
  FastAPI + agent 循环 + 检索 + LLM 客户端         MySQL(事实源)
  Qdrant(向量索引,同机 localhost,派生/可重建)       └ 单库多表(db_name):会话/事件/记忆 · 知识 · 费率 分表
  (可选) Redis(缓存/加速,可丢)                    (knowledge/premium 拆分库为演进选项,默认并入)
  (可选) Langfuse(LLM 观测层,派生)                + 备份(mysqldump)/binlog 归档/保留/删除
  /metrics + 日志→stdout/文件
```

- **应用服务器(A)**:无状态;只跑 agent 逻辑 + 派生层(Qdrant/Redis/Langfuse)。多 worker 可横向(但**单写者**:只有应用部署写 MySQL)。
- **数据库服务器(B)**:**MySQL = 唯一事实源**。**默认单库多表**:会话/事件/记忆、知识、费率在同一个库(`db_name`)分表存放;拆分 knowledge/premium 库仅多应用共享/独立备份时需要(`KNOWLEDGE_DB_NAME`/`PREMIUM_DB_NAME` 留空即并入单库)。Qdrant/Redis/Langfuse 是派生/可丢层,清了可从 MySQL/应用重建。
- **向量库(Qdrant)**:独立服务进程,**不需要独立一台机器**;本规模就**同机应用服务器(localhost)** 最快;从 MySQL 知识表可重建、不用单独备份。仅当向量量/吞吐变大才拆到 B 或独立节点。

## 二、单写者与一致性
- **只有应用服务器部署写 MySQL**(唯一写入方),避免多实例并发写冲突;append-only(/events 只 INSERT)与审计语义照搬。
- 易失层(Qdrant/Redis/Langfuse)全空,系统照常跑(黄金法则)。

## 三、观测(两机下生产标准)
- **指标**:App 暴露 Prometheus `/metrics`(标准 `prometheus_client`)→ 公司 Prometheus/ARMS 抓 → 大盘(请求量/错误率/延迟 p95/依赖健康/预算)。
- **AI 追踪/成本/质量**:Langfuse(自托管)从 events/用 SDK 推 → 跨会话 trace 大盘 + 成本 + 评估。
- **告警**:Prometheus 规则/Alertmanager 或公司告警(错误率/延迟/预算/依赖失败)→ 带 `request_id`。
- **贯穿 `request_id`**:HTTP 指标 ↔ Langfuse trace ↔ MySQL events 打通。
- **日志**:结构化 → stdout/文件 → 公司日志采集(Loki/ELK/SLS)。

## 四、关键配置(应用服务器 `.env`)
```
DB_ENABLED=true
DB_HOST=<数据库服务器IP>
DB_PORT=3306
DB_USER=<仅应用用的账号>
DB_PASS=<口令>
DB_NAME=agent          # 会话/事件/记忆
KNOWLEDGE_DB_NAME=agent_knowledge   # 空=用 DB_NAME
PREMIUM_DB_NAME=agent_premium       # 空=用 DB_NAME
QDANT_URL=http://localhost:6333     # Qdrant 同机应用服务器
REDIS_URL=redis://:...@<redis host>/2   # 可选
```
未配 `DB_HOST` 时回退 SQLite(开发/测试)。

## 五、运维
- **备份**:MySQL `mysqldump` + 二进制日志归档;保留周期/删除任务(PII 客户数据)。
- **迁移**:存量 `data/*.db`(SQLite)→ MySQL 一次性迁移脚本。
- **Qdrant 重建**:从 MySQL 知识表全量重灌(Qdrant 属派生)。
- **启动**:App 用 `systemd`/docker(`reload` 关、多 worker);启动依赖体检探 MySQL/Qdrant/Redis(失败不阻断,降级)。
