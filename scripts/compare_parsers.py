# -*- coding: utf-8 -*-
"""三路解析对比工具:同一份 pdf/docx/xlsx 分别用 mineru / markitdown / (pdfplumber|native) 解析,
统计结构线索与"脱# + 编号 regex 切块"后的层级可用性,并把每路原文落盘便于人工比对。

用法:
  python scripts/compare_parsers.py --path <文件|目录> [--out DIR] [--limit N]
                                    [--only mineru|markitdown|pdfplumber|native] [--no-mineru]

说明:
- MinerU 走后端 API(消耗每日配额,MINERU_API_KEY 留空自动跳过并标原因)。
- 输出目录默认 ./compare_out/<文件名>/<后端>.md;同时打印对比表并写 summary.tsv。
- 切块口径统一为"脱 md # 前缀 + 编号 regex(policy)":MinerU 输出带 ## 平铺标题,
  脱#后才能被 第X部分/第X条/(一)/N. 识别 —— 这正是"层级靠编号 regex 重建"的验证。
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.retrieval.ingest.reader import try_backend, is_supported, backend_supported_for, _SUPPORTED_EXTS  # noqa: E402
from app.retrieval.ingest.probe import probe_text, chunk_probe, strip_md_prefix  # noqa: E402

_PDF_BACKENDS = ("mineru", "markitdown", "pdfplumber")
_OFFICE_BACKENDS = ("mineru", "markitdown", "native")


def _backends_for(path: str, only: str | None, no_mineru: bool) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        return []
    base = _PDF_BACKENDS if ext == ".pdf" else _OFFICE_BACKENDS
    out = [b for b in base if not (no_mineru and b == "mineru") and (not only or b == only)]
    return out


def _fmt_row(cols: list) -> str:
    return "  ".join(str(c).ljust(w) for c, w in zip(cols, (10, 5, 8, 7, 7, 6, 7, 7, 6, 6, 8, 6, 7, 6, 26)))


def compare_file(path: str, out_dir: str, chunk_size: int, only: str | None, no_mineru: bool,
                 max_tokens: int | None = None) -> list[dict]:
    stem = os.path.splitext(os.path.basename(path))[0]
    d = os.path.join(out_dir, stem)
    os.makedirs(d, exist_ok=True)
    backends = _backends_for(path, only, no_mineru)
    rows = []
    print(f"\n===== {os.path.basename(path)} =====")
    if not backends:
        # 纯文本:无需对比,直接落盘
        with open(path, encoding="utf-8") as f:
            t = f.read()
        with open(os.path.join(d, "raw.txt"), "w", encoding="utf-8") as f:
            f.write(t)
        print("  (txt/md 原样保留,无解析对比)")
        return []
    import time
    for b in backends:
        print(f"  [{b}] 解析中 ...", flush=True)
        t0 = time.perf_counter()
        ok, text, err = try_backend(path, b)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not ok:
            rows.append({"backend": b, "ok": False, "err": err, "elapsed_ms": elapsed_ms})
            print(_fmt_row([b, "ERR", f"{elapsed_ms}ms", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", (err or "")[:24]]))
            continue
        ext = os.path.splitext(path)[1].lower()
        doc_type = "markdown" if ext == ".md" else ("policy_pdf" if ext == ".pdf" else "policy_docx")
        p = probe_text(text)
        cp = chunk_probe(text, doc_type=doc_type, chunk_size=chunk_size, strip_md=True, max_tokens=max_tokens)
        # mineru 额外看未脱#的切块损失
        loss = ""
        if b == "mineru":
            raw_cp = chunk_probe(text, doc_type=doc_type, chunk_size=chunk_size, strip_md=False,
                                 max_tokens=max_tokens)
            loss = f"raw#{raw_cp['with_section']}/{raw_cp['chunks']}"
        with open(os.path.join(d, f"{b}.md"), "w", encoding="utf-8") as f:
            f.write(text)
        mdh = sum(p["md_heads"].values())
        row = {
            "backend": b, "ok": True, "chars": p["chars"], "lines": p["lines"],
            "md#": mdh, "part": p["part"], "art": p["article"], "sub": p["subitem"],
            "num": p["numbered"], "noise": p["page_noise"],
            "chunks": cp["chunks"], "sec%": cp["section_fill_pct"], "depth": cp["avg_path_len"],
            "overlong": cp["overlong"], "err": loss or "", "elapsed_ms": elapsed_ms,
        }
        rows.append(row)
        print(_fmt_row([row["backend"], "OK", f"{elapsed_ms}ms", row["chars"], row["lines"], row["md#"],
                        row["part"], row["art"], row["sub"], row["num"], row["noise"],
                        row["chunks"], f"{row['sec%']}%", row["depth"], (row["err"] or "")[:24]]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True, help="文件或目录")
    ap.add_argument("--out", default="compare_out", help="输出目录(默认 ./compare_out)")
    ap.add_argument("--limit", type=int, default=0, help="目录模式仅前 N 个文件")
    ap.add_argument("--only", default="", choices=("", "mineru", "markitdown", "pdfplumber", "native"),
                    help="只跑指定后端")
    ap.add_argument("--no-mineru", action="store_true", help="跳过 MinerU(不动 API/配额)")
    ap.add_argument("--chunk-size", type=int, default=512, help="结构切块 chunk_size(与摄取口径一致)")
    ap.add_argument("--max-tokens", type=int, default=-1,
                    help="embedding token 预算(默认取 config CHUNK_MAX_TOKENS;--max-tokens 0=关闭改字符上限)")
    a = ap.parse_args()

    # 默认与摄取同口径:config 的 token 预算
    from app.config import load as _load
    _cfg = _load()
    max_tokens = a.max_tokens
    if max_tokens == -1:
        max_tokens = int(getattr(_cfg, "chunk_max_tokens", 0) or 0) or None
    elif max_tokens <= 0:
        max_tokens = None

    if os.path.isfile(a.path):
        files = [a.path]
    else:
        files = []
        for root, _, fs in os.walk(a.path):
            for fn in sorted(fs):
                if fn.lower().endswith(_SUPPORTED_EXTS):
                    files.append(os.path.join(root, fn))
        if a.limit:
            files = files[:a.limit]

    os.makedirs(a.out, exist_ok=True)
    header = _fmt_row(["backend", "ok", "chars", "lines", "md#", "part", "art", "sub", "num", "noise", "chunks", "sec%", "depth", "note/err"])
    print("\n" + header)
    print("-" * len(header))
    all_rows = []
    for f in files:
        rows = compare_file(f, a.out, a.chunk_size, a.only or None, a.no_mineru, max_tokens)
        for r in rows:
            all_rows.append({"file": os.path.basename(f), **r})
    # summary.tsv
    tsv = os.path.join(a.out, "summary.tsv")
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("file\tbackend\tok\tchars\tlines\tmd#\tpart\tart\tsub\tnum\tnoise\tchunks\tsec%\tdepth\terr\n")
        for r in all_rows:
            f.write("\t".join(str(r.get(k, "")) for k in
                    ("file", "backend", "ok", "chars", "lines", "md#", "part", "art", "sub", "num", "noise", "chunks", "sec%", "depth", "err")) + "\n")
    print(f"\n结果落盘: {os.path.abspath(a.out)}  (summary.tsv + <文件名>/<后端>.md)")


if __name__ == "__main__":
    main()
