# -*- coding: utf-8 -*-
"""一键门禁(CI gate):后端 unittest + 前端 tsc + 前端 build,聚合结果、失败即非零退出。

用法(在项目根):
    python scripts/ci.py                 # 全量:后端测试 + 前端类型检查 + 前端构建
    python scripts/ci.py --only backend  # 只跑后端 unittest
    python scripts/ci.py --only frontend # 只跑前端 tsc + vite build
    python scripts/ci.py --python D:/path/to/python.exe   # 指定后端解释器(默认=运行本脚本的解释器)

退出码:0=全部通过;1=任一环节失败(门禁未过)。

要点:
- 后端解释器默认取 sys.executable(即"谁跑 ci.py 就用谁测"),README 约定在 rag_env 下执行:
      rag_env\python.exe scripts/ci.py
- 若后端失败且错误里出现 starlette TestClient / httpx 版本类错误,会给出环境诊断提示
  (代码失败 vs 环境不匹配要分开看,别把环境问题当回归)。
- 前端需要 node 与 web/node_modules(vite/tsc 直接经 node 调用,避免 Windows npm.cmd 差异)。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
NODE_MODULES = WEB / "node_modules"
TSC_BIN = NODE_MODULES / "typescript" / "bin" / "tsc"
VITE_BIN = NODE_MODULES / "vite" / "bin" / "vite.js"

# starlette TestClient(旧版,app= 构造)与 httpx>=0.28 不兼容的关键报错特征
_ENV_FAIL_MARKERS = (
    "unexpected keyword argument 'app'",
    "TestClient",
)


def _print(text: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text, flush=True)


def _run(cmd, cwd, timeout=1800):
    """执行命令,返回 (exit_code, output_tail, output_full)。"""
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out[-4000:], out
    except FileNotFoundError as e:
        return 127, f"command not found: {e}", ""
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s", ""


def step_backend(python_exe: str) -> tuple[bool, str]:
    """python -m unittest discover -s tests(项目根执行)。"""
    _print("== [1/3] backend: unittest discover -s tests ==")
    code, tail, full = _run([python_exe, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT)
    ok = code == 0
    print(tail if tail else f"(no output, exit={code})")
    if not ok and any(m in full for m in _ENV_FAIL_MARKERS):
        print("[env-hint] 输出含 TestClient 兼容错误特征:优先怀疑解释器依赖版本不匹配")
        print("[env-hint] (旧 starlette 的 TestClient 与 httpx>=0.28 不兼容),而不是代码回归。")
        print("[env-hint] 请用项目声明环境跑门禁:  rag_env\\python.exe scripts/ci.py")
        print("[env-hint] 或按 requirements.txt 重建 venv(python -m venv .venv 且 pip install -r requirements.txt)。")
    return ok, "backend unittest"


def _node_ok() -> tuple[bool, str]:
    if shutil.which("node") is None:
        return False, "node not found in PATH"
    if not NODE_MODULES.exists():
        return False, f"{NODE_MODULES} missing -> run: cd web && npm install"
    return True, ""


def step_tsc() -> tuple[bool, str]:
    _print("== [2/3] frontend: tsc --noEmit ==")
    ok_node, why = _node_ok()
    if not ok_node:
        print(f"[skip] {why}")
        return False, why
    code, tail, _ = _run(["node", str(TSC_BIN), "--noEmit"], cwd=WEB, timeout=600)
    ok = code == 0
    print(tail if tail else f"(tsc exit={code})")
    return ok, "frontend tsc --noEmit"


def step_build() -> tuple[bool, str]:
    _print("== [3/3] frontend: vite build ==")
    ok_node, why = _node_ok()
    if not ok_node:
        print(f"[skip] {why}")
        return False, why
    code, tail, _ = _run(["node", str(VITE_BIN), "build"], cwd=WEB, timeout=600)
    ok = code == 0
    print(tail if tail else f"(vite exit={code})")
    return ok, "frontend vite build"


def main() -> int:
    ap = argparse.ArgumentParser(description="One-shot CI gate for insurance-agent")
    ap.add_argument("--only", choices=["backend", "frontend"], default=None,
                    help="run only one side (default: all)")
    ap.add_argument("--python", default=sys.executable, help="backend python interpreter")
    args = ap.parse_args()

    t0 = time.time()
    steps: list[tuple[bool, str]] = []

    if args.only in (None, "backend"):
        steps.append(step_backend(args.python))
    if args.only in (None, "frontend"):
        steps.append(step_tsc())
        steps.append(step_build())

    _print()
    _print("== summary ==")
    all_ok = True
    for ok, name in steps:
        all_ok = all_ok and ok
        _print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    _print(f"  total {time.time() - t0:.1f}s")
    _print("CI GATE " + ("PASSED" if all_ok else "FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
