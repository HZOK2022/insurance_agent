# 18 混合检索融合:RRF 与"阶梯显示什么"

> 对应决策:DECISIONS **D92**;代码:`app/retrieval/hybrid.py`、`app/retrieval/search_tool.py`、`web/src/App.tsx`。

## 这一阶段解决什么问题

原 `fuse_and_pick` 用 **min-max 归一后按权重相加**。它依赖"单路分数的分布",有两类失效:

**一、单路 collapse。** dense 只命中一个 gold 块时 `min == max`,`_normalize` 直接归 0,这个块被 BM25 那边一大批高分块挤出候选池——明明是稠密第一名,融合后垫底。

**二、跨 modality 分数不可比。** dense 是余弦相似度(0~1),BM25 是无上界值(实测能到 8、9)。把它们放在同一杆秤上乘权重,`hybrid_bm25_weight=0.5` 这个"0.5"其实没有可比含义——它只是两个归一化结果的插值系数,不是"两路各占一半重要性"。

## 对应 dsh 源码

无直接对应(检索层为本项目自写,dsh 未涉及 RAG)。融合算法本身是信息检索领域的通用方法,非 dsh 参照。

## 设计要点

### 1. 换成 RRF:按排名融合,不看分数

```python
score = Σ 1 / (k + rank)      # k=60,业界惯例常量
```

好处直接对应上面两类失效:排名不受分数分布影响(collapse 不存在),也不需要两路分数可比(只看位置)。双路都命中的块自然叠加更高,这是 RRF 最想抓的信号。

### 2. 最大的教训:东西早就写好了,只是没接线

动手前梳理影响面时发现三样东西**全部已就位**:

- `rrf_fuse_and_pick` 早已实现在 `hybrid.py:114`
- `config.hybrid_fusion = "rrf"` 已定义,注释还写着"两种均已实现,可切换对比"
- `config.hybrid_rrf_k = 60` 已定义,连同 env 映射(`HYBRID_FUSION` / `HYBRID_RRF_K`)和取值校验

**但 `search_tool.py` 硬编码调 `fuse_and_pick`,从没读过这两个配置。** 所以真正的工作量不是"实现 RRF",而是**把线接上**——搜一下再动手,能省掉大半工作量。

这也和项目里另外几处同病:`tool_timeout_seconds`、Redis、`daily_token_budget_per_user` 都是"配好了没接"。**看到配置项先 grep 一遍有没有人读它**,是个便宜的习惯。

### 3. 阶梯展示从"分数"改成"rank"

这是本阶段真正的设计选择,不只是顺手改。

RRF 之后融合分落在 **0.01~0.03** 量级。而前端 `fs = (v) => Number(v).toFixed(2)` 是固定两位小数:

| 块 | RRF 分 | `toFixed(2)` |
|---|---|---|
| b1(BM25 第 1) | 0.0164 | `0.02` |
| d2(稠密第 2) | 0.0161 | `0.02` ← 和上面一样 |

信息全废。但**更根本的理由不是精度**:RRF 的输入就是排名,显示 rank 是展示**源头数据**,显示分数是展示**二手派生值**。

改成 `d:#3 b:#1 f:#2 r:#1` 之后还能读出分数表达不出的东西——**位移**:

```
f:#2  r:#1     ← 一眼看出:重排把它从第 2 提到第 1
f:0.0164  r:0.91   ← 看不出"提升了一位"
```

原始分不丢,降级到 title 悬停显示。

### 4. rank 必须由后端记录

前端算不出来——**bm25-only 的块在 dense 里压根没有位置**。所以新增四个字段 `dense_rank / bm25_rank / fused_rank / rerank_rank`,缺失为 `None`(前端渲染 `—`,与老事件兼容)。

`fused_rank` 也不是冗余:`search_tool.py` 末尾的 product/category 软偏置会重排最终顺序,`fused_rank` 记录的是**偏置前**的位置。

## Python 实现

```python
# 记录四路 rank(dense_hits / bm25 结果已是按分降序)
dense_rank = {h["chunk_id"]: i + 1 for i, h in enumerate(dense_hits)}
_bm25_res = hybrid.search(query, top_k * 2)
bm25_map = dict(_bm25_res)
bm25_rank = {cid: i + 1 for i, (cid, _s) in enumerate(_bm25_res)}

# 按 config 分流,两种融合都保留
if fusion == "weighted":
    fused = fuse_and_pick(dense_map, bm25_map, hybrid_weight, top_k)
else:
    fused = rrf_fuse_and_pick(dense_map, bm25_map, k=rrf_k, top_k=top_k)
fused_rank = {cid: i + 1 for i, (cid, _s) in enumerate(fused)}
```

**注意** `bm25_map = dict(hybrid.search(...))` 这种写法会丢顺序信息——虽然 Python dict 保序,但 rank 必须显式记录,别指望隐式保序。

## 验收测试

`tests/test_hybrid.py::RrfWiringTest` 4 项。关键是**怎么证明默认确实走了 RRF**——靠**分数区间**而不是具体值:

```python
def test_default_fusion_is_rrf(self):
    b1 = self._b1(self._run())
    self.assertGreater(b1["fused_score"], 0)
    self.assertLess(b1["fused_score"], 0.04)      # RRF 上限 2/(60+1)≈0.0328

def test_weighted_fusion_switchable(self):
    b1 = self._b1(self._run(fusion="weighted"))
    self.assertGreater(b1["fused_score"], 0.04)   # min-max 归一落在 [0,1]
```

两者量级差一个数量级,足以区分。实测同一查询下:`b1` 在 RRF 是 **0.0164**、在 weighted 是 **0.5**。

另两项:四路 rank 字段齐全且 `b1` 稠密漏召时 `dense_rank is None`;rerank 位移 `fused_rank=2 → rerank_rank=1`。

全量 **396 项 OK**;`tsc --noEmit` 绿。

## 手动测试

1. 后端起服务,问一个知识问题
2. 轨迹 tab 展开检索块,看阶梯是否显示 `d:#1 b:#2 f:#1 r:#2` 形式
3. 悬停阶梯看 tooltip,应有四路原始分(3 位小数)
4. `.env` 设 `HYBRID_FUSION=weighted` 重启,对比同一问题的召回顺序差异

**注意**:改了前端,必须 `npm run build` 后**硬刷新(Ctrl+F5)**才看得到。本次构建产物 `index-DwpHS06h.js`,已核对 `dist/index.html` 引用一致。

## 你学到了什么

- **RRF 的价值不在"更准",在"不依赖分数分布"**——它把融合问题从"怎么让两种分数量纲可比"降维成"怎么合并两个排名"
- **配置存在 ≠ 生效**。动手前先 grep 配置项有没有被读,能发现"活没干完"和"已经干完"两种截然不同的情况
- **展示派生值不如展示源头值**。融合分是从 rank 算出来的,那就直接显示 rank
- **精度问题是症状,不是病根**。`toFixed(2)` 显示不了 0.0164 只是表象,真正的判断是"这个数字对用户可解释吗"

## 踩坑记录

1. **`low = sc < 0.3` 判低分的隐藏 bug。** 前端原本用分数阈值给低分块加灰,RRF 分上限才 0.033 → **所有块都会被判成低分变灰**。这种"阈值依赖绝对量纲"的写法在换算法时必然失效。改为:有 rank 就按排名判(>3),没 rank 才退回分数判据。**教训:任何硬编码的分数阈值,在改融合/评分算法时都要重新检视。**

2. **`dict(hybrid.search(...))` 丢顺序。** 虽然 Python 3.7+ dict 保序,但依赖隐式保序来推 rank 是脆弱的(万一哪天中间插了个排序或集合操作就静默出错)。显式枚举 index 更稳。

3. **Git Bash 下 `npm run build` 报 `Cannot read properties of undefined (reading 'stdin')`**,且 PowerShell 调用返回 exit 0 却**没有真正构建**(dist 时间戳没变,产物里搜不到新代码)。**绕法:用 node 直接调 vite**——`node node_modules/vite/bin/vite.js build`。**而且每次都要核对 dist 时间戳 + 产物内容 + index.html 引用,不能只看 exit code。**
