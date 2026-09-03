# -*- coding: utf-8 -*-
"""集中日志(结构化 JSON + trace_id + 分级 + 滚动文件),供排查/监控。

- JsonFormatter:一条日志一个 JSON 对象,含 ts/level/logger/msg + 可选的 trace_id/session_id/turn/step/event_type/tool/model/latency_ms/error 等字段。
- setup_logging():配置根 logger 的 level + 控制台/文件 handler(文件滚动,路径默认可配)。
- 用法:logging.getLogger(__name__).info("...", extra={"session_id":..., "trace_id":...})
"""
from __future__ import annotations
import datetime
import json
import logging
import os
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

# 会作为"字段"打进 JSON 的 extra 键
_EXTRA_KEYS = ("trace_id", "session_id", "turn", "step", "event_type", "tool", "model",
               "latency_ms", "prompt_tokens", "completion_tokens", "error")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in _EXTRA_KEYS:
            v = getattr(record, k, None)
            if v is not None:
                d[k] = v
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        try:
            return json.dumps(d, ensure_ascii=False)
        except TypeError:  # 某字段不可序列化 → 保守用 repr
            d = {k: (str(v) if v is not None else v) for k, v in d.items()}
            return json.dumps(d, ensure_ascii=False)


def setup_logging(level: str = "INFO", log_dir: str = "data/logs",
                  backup_count: int = 30) -> None:
    """配置根 logger:控制台(可读)+ 文件(JSON,滚动)。重复调用只生效一次。

    按天滚动:每天生成一个新文件,保留 backup_count 天。
    """
    lg = logging.getLogger()
    if getattr(lg, "_dsh_setup", False):
        return
    lg.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = JsonFormatter()
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    lg.addHandler(ch)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        # 每天生成一个新日志文件,后缀为 .YYYY-MM-DD
        fh = TimedRotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            when="midnight",  # 午夜切换
            interval=1,      # 每 1 天一个文件
            backupCount=backup_count,
            encoding="utf-8",
            utc=True         # 用 UTC 时间切分,避免时区问题
        )
        fh.setFormatter(fmt)
        lg.addHandler(fh)
    lg._dsh_setup = True  # type: ignore[attr-defined]
