# 03 阶段2:配置集中化(app/config/config.py)

> 本阶段把「所有可调参数」收敛到一处,消灭散落硬编码——改阈值不用翻代码。

## 这一阶段解决什么问题
硬编码上限(步数/token/超时)散落在各文件,改一处漏三处;误配置(如 0 步上限)静默死循环。集中 + 启动校验 = 一处改、一处查、越界大声失败。

## 对应 dsh 源码
- `packages/boot/app-boot`(配置装载)+ 各包 Config 校验;dsh 用 zod/cordis 校验,我们用 dataclass + 启动校验

## 设计要点
1. **一个 frozen dataclass** `Config`:LLM/嵌入/上限/审批/存储全部字段,带默认值
2. 从环境变量(.env)读取并**类型强制**(int/tuple/str),缺失用默认
3. **启动校验**:上限类字段必须 > 0,否则 ValueError 大声失败
4. **集中纪律**:测试扫描 app/ 除 config.py 外不得出现这些上限字面量(禁止散落硬编码)

## Python 实现
```python
# app/config/config.py —— @dataclass(frozen=True) Config + _ENV 映射 + load()/_validate()
# app/config/__init__.py —— from .config import Config, load
```

## 验收测试(tests/test_config.py,6 项全绿)
- 默认值存在且为正;load() 读 env 并强制 int(deepseek_model / max_steps=42)
- 非整数(abc)→ ValueError;负数阈值(-5)→ ValueError
- approval_exempt_tools 逗号解析成 tuple
- **无散落硬编码**:扫描 app/ 非 config 文件,不得出现 20/16000/30/8000/200000 这些字面量

## 手动测试
- 设置页(阶段2 前端,待本机构建):改阈值 → 生效,并即时校验

## 你学到了什么
- **集中配置 + 启动校验**:可调参数一处收口,越界 fail-loud
- **类型强制 + 语义校验**:环境变量是字符串,进 Config 前转成正确类型并校验
- 用测试守卫“不散落硬编码”(扫描具体字面量)

## 踩坑记录
- `app/config/__init__.py` 没导出 → ImportError;包入口要 re-export
- 测试扫描里反斜杠转义踩坑 → 改用 `os.path.basename`
