"""PyCharm 启动入口:直接运行本文件即可起后端。

⚠ 必须用带依赖的解释器(本项目用你另一项目的 rag_env):
    D:\LLM\huai_test\agentic_rag_ins\rag_env\Scripts\python.exe
(基础 python 没有 fastapi/uvicorn/sentence_transformers)

用法:PyCharm 里把 run.py 设为运行目标 + 解释器指向上述 rag_env python;或命令行:
    <rag_env>\python.exe run.py                    # 默认 8181
    <rag_env>\python.exe run.py --port 8199        # 换端口(agent/测试用,避开开发者的 8181)
    <rag_env>\python.exe run.py --no-reload        # 单进程,便于彻底停止

端口占用检测:被占用时明确报错退出,避免"新进程悄悄绑不上、你还在看旧实例"
(uvicorn reload 的 master/worker 结构下,worker 继承监听 socket 尤其容易踩)。
"""
import argparse
import socket
import sys

import uvicorn


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8181, help="监听端口(默认 8181)")
    ap.add_argument("--no-reload", action="store_true", help="关闭自动重载(单进程)")
    a = ap.parse_args()
    if _port_in_use("127.0.0.1", a.port):
        print("[run] 端口 %d 已被占用:可能已有后端在跑,或旧 worker 进程残留。" % a.port)
        print("[run] 排查: netstat -ano | findstr :%d" % a.port)
        print("[run] 结束占用: taskkill /F /PID <上面列出的 PID>(必要时用管理员 PowerShell)")
        print("[run] 或换端口: python run.py --port 8199")
        sys.exit(1)
    uvicorn.run("app.main:app", host="127.0.0.1", port=a.port,
                reload=not a.no_reload, reload_dirs=["app"])
