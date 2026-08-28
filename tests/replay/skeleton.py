"""回放测试骨架(阶段1占位)。参照 dsh test-support/llm-replay。

目标:录制一次真实问答(带检索/工具/引用),回放时不依赖真实 LLM API,
让改动 prompt/条款/工具 schema 也能无 key 回归。阶段4 接入真实 loop 后填充录制器。
当前仅验证:能加载一段录制(JSONL),每条含 type + payload。
"""
from __future__ import annotations

import json
import os

RECORD_DIR = os.path.dirname(__file__)


def load_record(name: str) -> list[dict]:
    with open(os.path.join(RECORD_DIR, name), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def record_path(name: str) -> str:
    return os.path.join(RECORD_DIR, name)
