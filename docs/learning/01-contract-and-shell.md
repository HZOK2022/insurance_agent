# 01 阶段0:契约先行 + React 前端壳 + mock

> 本阶段目标:在没有任何后端功能前,前端就能"演"一次对话。学到的是**契约与实现解耦**——前端只依赖 wire contract,后端之后照契约填真实现。

## 这一阶段解决什么问题
没有契约,前端等后端;等后端全写完才见 UI,反馈太慢。契约先行让前端/后端可并行,且为「每阶段可手动测试」打底。

## 对应 dsh 源码
- 契约思想:`packages/api/gateway`(JSON-RPC + SSE 四象限)
- 前端三列布局:`packages/client/ui-layout/src/client/AppFrame.tsx` + `columns.ts`
- 设计 token:`packages/client/ui-theme/src/styles/design-platform.css` + `base.css`

## 设计要点
- 前端依赖 `docs/contract.md`(REST + SSE 事件帧 + 引用角标),后端阶段1起实现
- 事件类型 = 会话日志注册表(docs/sqlite-schema.md 与 contract.md 一致)
- React + Vite + TS 单页,借鉴 dsh 三列框架与 token,不引入前端 Cordis/slot(D15)

## Python 实现(mock)
```python
# app/main.py —— 阶段0 mock:POST prompt 后 SSE 推 canned 事件(契约帧格式)
# 阶段4 换成真实 loop,帧格式不变
```

## 验收测试
- 前端壳可打开并"演"一次假对话(本机 npm run dev)
- 契约文档成文(docs/contract.md)

## 手动测试:前端怎么玩
1. `cd web && npm install && npm run dev` → http://localhost:5173
2. 发"重疾险责任免除" → 看到:检索工具卡片 → 流式回答 → 引用 [1] 角标
3. 点 [1] → 右侧溯源面板高亮对应来源

## 你学到了什么
- 契约(HTTP+SSE 帧)与实现解耦,前端可先行
- dsh 三列布局:sidebar|center|details,列几何/拖拽/窄屏收起
- 引用角标 → chunk_id → 原文溯源 的完整链路

## 踩坑记录
- 沙箱里 npm install 被拦(全局缓存路径在工作区外报 EPERM),前端构建只能本机跑
- 原生抓取 ins-replica 复刻不够,按 dsh 源码(columns.ts/token)重做才符合预期
