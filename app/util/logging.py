# -*- coding: utf-8 -*-
"""集中日志(可读控制台 + 结构化 JSON 文件 + trace_id + 分级 + 滚动),供排查/监控。

双格式:
- ConsoleFormatter:控制台用**人读文本**(时间 级别 [logger] msg + 上下文字段),开发时一眼可读。
- JsonFormatter:文件用**一条日志一个 JSON 对象**(ts/level/logger/msg + 可选 trace_id/session_id/turn/step/tool/model/latency_ms/error 等),机器可解析。
- setup_logging():配置根 logger 的 level + 控制台(可读)/文件(JSON,按天滚动)。重复调用只生效一次。
- 用法:logging.getLogger(__name__).info("...", extra={"session_id":..., "trace_id":...})
"""
from __future__ import annotations
import datetime
import json
import logging
import os
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

# 会作为"字段"打进日志的 extra 键(控制台拼在行尾,文件进 JSON 对象)
_EXTRA_KEYS = ("trace_id", "session_id", "turn", "step", "event_type", "tool", "model",
               "latency_ms", "prompt_tokens", "completion_tokens", "error")


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class ConsoleFormatter(logging.Formatter):
    """人类可读的控制台格式:2026-09-06T10:29:08 INFO [app.agent_loop] turn end sid=xxx  … trace_id=xxx"""

    def format(self, record: logging.LogRecord) -> str:
        ts = _utcnow().isoformat(timespec="milliseconds")
        # where(源码位置):filename:lineno · funcName —— 排查时一眼知道这行出自哪
        line = f"{ts} {record.levelname:<7} [{record.name}] {record.filename}:{record.lineno} {record.funcName} - {record.getMessage()}"
        extra = [f"{k}={getattr(record, k)}" for k in _EXTRA_KEYS if getattr(record, k, None) is not None]
        if extra:
            line += "  " + " ".join(extra)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": _utcnow().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            # where(源码位置)作为结构化字段,便于机器按文件/行/函数过滤
            "source": f"{record.filename}:{record.lineno}",
            "func": record.funcName,
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


def _quiet_third_party() -> None:
    """降噪第三方库(网络/HTTP 框架)的 INFO 刷屏,保留业务日志可读。"""
    for name in ("httpx", "httpcore", "urllib3", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)


def setup_logging(level: str = "INFO", log_dir: str = "data/logs",
                  backup_count: int = 30, console_format: str = "text",
                  file_format: str = "text") -> None:
    """配置根 logger。默认控制台 + 文件都用**人读文本**(tail .log 即可排查);
    传 console_format/file_format="json" 可切回 JSON(供日志采集器/机器解析)。

    按天滚动:每天生成一个新文件,保留 backup_count 天。重复调用只生效一次。
    """
    lg = logging.getLogger()
    if getattr(lg, "_dsh_setup", False):
        return
    lg.setLevel(getattr(logging, level.upper(), logging.INFO))
    _quiet_third_party()
    console_fmt = ConsoleFormatter() if console_format == "text" else JsonFormatter()
    file_fmt = ConsoleFormatter() if file_format == "text" else JsonFormatter()
    ch = logging.StreamHandler()
    ch.setFormatter(console_fmt)
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
        fh.setFormatter(file_fmt)
        lg.addHandler(fh)
    lg._dsh_setup = True  # type: ignore[attr-defined]
