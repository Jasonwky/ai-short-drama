# -*- coding: utf-8 -*-
"""启动控制台:网页 + 后台 worker(真实云机后端)。

    python -X utf8 -m tools.serve.console --db data/drama.db --data-root data [--port 8700]

- 只听 127.0.0.1 —— 纯本地,不对外;
- **没有假后端开关**:假后端只给测试与 Playwright 自测用,正式入口连不上云机就如实显示连不上。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from tools.drama.db import Store
from tools.refs.db import RefStore
from tools.serve.app import create_app
from tools.serve.runtime import Runtime, real_backend


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="AI 短剧控制台")
    ap.add_argument("--db", required=True, help="SQLite 库文件(不存在会新建)")
    ap.add_argument("--data-root", required=True, help="素材与产物根目录;库里的路径都相对它")
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--refs-db", help="参考库文件(spec 2026-11-07-参考库);不给则参考库面板显示「未配置参考库」")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    data_root = Path(args.data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    refs_root = Path(args.refs_db).parent if args.refs_db else None
    if refs_root:
        refs_root.mkdir(parents=True, exist_ok=True)
    refs = RefStore(args.refs_db) if args.refs_db else None
    runtime = Runtime(Store(args.db), data_root, real_backend(), refs=refs, refs_root=refs_root)
    runtime.start()
    print(f"控制台:http://127.0.0.1:{args.port}/  (Ctrl+C 退出;云机任务不受影响,下次启动按 external_id 接着收)",
          flush=True)
    try:
        uvicorn.run(create_app(runtime), host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        runtime.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
