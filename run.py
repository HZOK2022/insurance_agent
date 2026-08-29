"""PyCharm 启动入口:直接运行本文件即可起后端。

⚠ 必须用带依赖的解释器(本项目用你另一项目的 rag_env):
    D:\LLM\huai_test\agentic_rag_ins\rag_env\Scripts\python.exe
(基础 python 没有 fastapi/uvicorn/sentence_transformers)

用法:PyCharm 里把 run.py 设为运行目标 + 解释器指向上述 rag_env python;或命令行:
    <rag_env>\python.exe run.py
"""
import uvicorn

if __name__ == "__main__":
    # 从项目根运行;reload=True 代码改动自动重启
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True, reload_dirs=["app"])