# -*- coding: utf-8 -*-
"""MinerU 精准解析 API(v4)在线客户端:本地文件签名上传 → 轮询 → 解压 zip 取 full.md / *_content_list.json。

MINERU_API_KEY 为空 → 未启用(调用方应降级 MarkItDown/pdfplumber)。api_key 走 .env(用户自填)。
异步(上传→轮询);可选 OCR/表格/公式(默认开);模型支持 pipeline/vlm(推荐)/MinerU-HTML。
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile

import requests

BASE = "https://mineru.net"


class MinerUError(RuntimeError):
    pass


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _cfg():
    from app.config import load
    return load()


def parse_file(path: str) -> dict:
    """解析本地文件(≤200MB/≤200页),返回 {markdown, content_list}。未配置 key 抛 MinerUError。"""
    cfg = _cfg()
    token = getattr(cfg, "mineru_api_key", "") or ""
    if not token:
        raise MinerUError("MINERU_API_KEY 未配置(解析降级 MarkItDown/pdfplumber)")
    model_version = getattr(cfg, "mineru_model_version", "vlm") or "vlm"
    timeout = int(getattr(cfg, "mineru_timeout_seconds", 300) or 300)
    interval = int(getattr(cfg, "mineru_poll_interval", 3) or 3)
    fname = os.path.basename(path)

    # 1) 申请本地上传(签名)地址
    r = requests.post(f"{BASE}/api/v4/file-urls/batch", headers=_headers(token),
                      json={"files": [{"name": fname}], "model_version": model_version})
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 0:
        raise MinerUError(f"申请上传地址失败: {d.get('msg', d)}")
    batch_id = d["data"]["batch_id"]
    file_url = d["data"]["file_urls"][0]

    # 2) PUT 上传到签名地址(系统自动扫描提交)
    with open(path, "rb") as f:
        up = requests.put(file_url, data=f)
    up.raise_for_status()

    # 3) 轮询批量结果
    start = time.time()
    while time.time() - start < timeout:
        rs = requests.get(f"{BASE}/api/v4/extract-results/batch/{batch_id}", headers=_headers(token))
        rs.raise_for_status()
        rd = rs.json()
        if rd.get("code") != 0:
            raise MinerUError(f"查询结果失败: {rd.get('msg', rd)}")
        item = rd["data"]["extract_result"][0]
        state = item.get("state")
        if state == "done":
            return _download(item["full_zip_url"])
        if state == "failed":
            raise MinerUError(f"解析失败: {item.get('err_msg')}")
        time.sleep(interval)
    raise MinerUError(f"轮询超时({timeout}s): batch_id={batch_id}")


def _download(zip_url: str) -> dict:
    zr = requests.get(zip_url)
    zr.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(zr.content))
    names = zf.namelist()
    markdown = None
    content_list = None
    md = [n for n in names if n.endswith("full.md")]
    if md:
        markdown = zf.read(md[0]).decode("utf-8")
    cl = [n for n in names if n.endswith("_content_list.json")]
    if cl:
        content_list = json.loads(zf.read(cl[0]).decode("utf-8"))
    return {"markdown": markdown, "content_list": content_list}
