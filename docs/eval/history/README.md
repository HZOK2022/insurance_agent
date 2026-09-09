# 评测历史(history)

每次 `scripts/eval_agent.py` 跑完,产物(eval_report.json / cite_report.json)应保留一份到此目录:

- `history/eval-<date>.json`         # LLM-judge 6 维分
- `history/cite-<date>.json`         # 引用层程序化校验
- `history/calibration-<date>.md`    # LLM-judge vs 人工校准(见 CITATION_VALIDATION.md §1)

命名规则:`<artifact>-<YYYY-MM-DD>.json`

## 基线读取

```python
import json, glob
def latest(pattern):
    files = sorted(glob.glob(f"docs/eval/history/{pattern}"))
    return json.load(open(files[-1])) if files else None

eval_ = latest("eval-*.json")
cite_ = latest("cite-*.json")
```

每次报告(eval-*.json / cite-*.json)都带 `prompt_version` 字段
= `app.businesses.insurance.prompt_version()`(SYSTEM 规则 sha256 前 12 位)。
改 SYSTEM 规则 → 该值变化,使"某次评测对应哪版 prompt"可追溯。

## diff 基线

```bash
python scripts/eval_agent.py --out docs/eval/history/eval-$(date +%F).json
python scripts/eval_citation.py --out docs/eval/history/cite-$(date +%F).json
# 跑两次 diff:与上一个 eval_report 比,任何维度 mean 跌 > 0.5 → 必查 PR
```

比对前**先校验 prompt_version**:与历史基线同版本再比分数;
跨版本只比分数有误导(旧基线可能是不同 SYSTEM 规则下的结果)。
