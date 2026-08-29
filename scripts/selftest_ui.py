# -*- coding: utf-8 -*-
"""UI 自测脚本:build 前端 -> 起后端 -> 造会话+prompt -> 截图 -> 停后端。

用法(用 rag_env 解释器,含 fastapi/uvicorn/requests/bge):
    python scripts/selftest_ui.py [--question "责任免除包括哪些情形?"] [--out _tmp/ui_shot.png]
输出:会话 ID 与截图路径;再用视觉桥(或人工)判读截图是否符合预期(结构化/角标/复制/分侧)。

AGENTS.md 规则:改动前端/交互代码后必须运行本脚本并判读截图,确认符合预期再交付。
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
CHROME = os.environ.get("SELFTEST_CHROME",
    "C:/Users/mi/AppData/Local/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-win64/chrome-headless-shell.exe")
PORT = int(os.environ.get("SELFTEST_PORT", "8000"))
URL = "http://127.0.0.1:" + str(PORT)


def build() -> None:
    print("[1/5] build frontend ...")
    subprocess.run("npm run build", cwd=WEB, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("      ok")


def port_free(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def wait_api(timeout: int = 60) -> bool:
    for _ in range(timeout * 2):
        try:
            with urllib.request.urlopen(URL + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def post_json(path: str, payload: dict):
    req = urllib.request.Request(URL + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def prompt(sid: str, text: str) -> str:
    req = urllib.request.Request(URL + "/api/sessions/" + sid + "/prompt",
                                 data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read().decode()


def screenshot(out: str) -> None:
    subprocess.run([CHROME, "--no-sandbox", "--disable-gpu", "--disable-crash-reporter", "--disable-crashpad",
                    "--screenshot=" + out, "--window-size=1280,900", "--hide-scrollbars",
                    "--virtual-time-budget=9000", URL + "/"], check=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default="责任免除包括哪些情形?")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "dsh_selftest_ui.png"))
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)

    if not port_free(PORT):
        print("端口", PORT, "被占用;请先停掉旧的后端再跑"); sys.exit(1)

    build()
    print("[2/5] start backend ...")
    backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_api():
            print("后端未起来"); sys.exit(1)
        print("      backend up")
        print("[3/5] create session + prompt ...")
        sid = post_json("/api/sessions", {"user_id": "u1"})["id"]
        prompt(sid, a.question)  # 跑 agent(SSE,约 20-40s)
        time.sleep(2)
        print("      session:", sid)
        print("[4/5] screenshot ...")
        screenshot(a.out)
        print("[5/5] done")
        print("SESSION:", sid)
        print("SCREENSHOT:", a.out)
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except Exception:
            backend.kill()


if __name__ == "__main__":
    main()