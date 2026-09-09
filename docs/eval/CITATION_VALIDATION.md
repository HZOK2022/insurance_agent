# 评测校准与人工抽检(LLM-judge calibration)

> docs/evaluation-and-ops.md §3 把 LLM-judge 当作 6 维主观分的裁判,但 LLM-judge 本身是有偏的(宽松/严格模式差很远;对短回答打分不稳;对自己生成的"对"更宽容)。
> 因此:  任何一次评测结果落地前,必须经人工抽检 + 双评校准;否则分数不进入基线。

## 1. 双评校准:每周 1 次

- **抽样**:从最新一次 `eval_report.json` 中按维度分层抽 10-15 条(覆盖各类别 kb/rule/calc/compare/honest/refuse/injection/grounded)
- **双评**:
  - A = LLM-judge(同 scripts/eval_agent.py 的 prompt)
  - B = 人工(按 docs/eval/RUBRIC.md 打分;6 维各 0-3)
- **校准指标**:
  - **Cohen κ**(加权,6 维各算):>= 0.6 算可信;< 0.6 视为 judge 跑偏,需修 `scripts/eval_agent.py` 的 `_JUDGE_PROMPT` 后重跑
  - **单维偏差**:A-B 绝对差均值 < 0.5;任一维偏差 >= 0.7 → 修该维的 rubric 描述
- **产物**:写到 `docs/eval/history/calibration-<date>.md`(长留,可追)

## 2. 抽检机制:每次 PR/版本变更前

- 触发:任何改 `app/loop/`、`app/prompts/`、`app/retrieval/`、`scripts/eval_agent.py`、`docs/eval/eval_set*.json` 的 PR,合并前必须抽 5 条(各维度至少 1 条)看 judge 给出 5 维主分是否合理
- 抽样方法:取本次跑分与基线差距 > 0.5 的 case;若没有,从高分 case(top 5)中抽
- 抽检不通过:不开闸;改 judge 提示或回滚

## 3. judge 自身的稳定性:跑 3 次方差

- 每次发版,同样评估集用同样 prompt 跑 3 次(可调 `--repeat 3`,本文件"待补"列第 4 项)
- 同一 case 6 维分的标准差 > 0.3 → 标"judge 噪声大",分数不视为基线
- 噪声来源通常:1) case 期望字段缺;2) judge prompt 给了双解释空间;3) temperature 太高。优先 1+2

## 4. 引用层:不走 LLM-judge

引用层(铁律 3)有专门的程序化校验,见 `scripts/eval_citation.py`。
- 不依赖 LLM:不调 DeepSeek,纯本地读 SQLite
- 必查三项:
  1. `[n]` 角标 n 落在 citations 数组范围内(无越界)
  2. 每个被引用的 chunk_id 本会话的 retrieval 事件里出现(不是凭空引用)
  3. chunk 真实存在于 SQLite(未被篡改/未越权)
- `must_cite=true` 的 case 但回答无角标 → 必报

## 5. 待补(列在 docs/learning/ 后续期)

- [x] `scripts/eval_agent.py` 加 `--repeat N`:agent 只跑一次收集回答,judge 独立评 N 次,输出每 case 每维 mean/std,单维 std>0.3 标噪声(2026-09-07 落地,实测 5 条 kb 中 2 条出现噪声维度:kb_003 completeness std=0.47 / kb_005 accuracy+safety std=0.47,整体 dim_avg_std≤0.09)
- [x] **首次双评校准已执行(2026-09-07)**:12 条分层样本,quadratic weighted κ 六维平均 **0.79**(≥0.6 总体可信);faithfulness κ=0.53 低于阈值需修 judge prompt。boolean 标记不可靠:overclaim 1 处/hallucinate 3 处/refused 3 处误报(「中高端医疗」=产品官方名被误判夸大;正确拒答被反向标记)。结论详见 `docs/eval/history/calibration-2026-09-07.md`。
- [ ] `docs/eval/RUBRIC.md`:6 维 0-3 分的具体判分细则(目前只在 prompt 里口语描述)
- [ ] 人工抽检的 UI/脚本(目前是人工 vs LLM 文本并排,Markdown 表格)
- [ ] judge 模板(chat 模型 vs reasoner 模型)两套——避免"理由展开过度 → 长回答"被偏袒

## 6. 一句话原则

> LLM-judge 用来排序,程序化校验用来否决;两者结合才稳。
