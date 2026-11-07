# -*- coding: utf-8 -*-
"""验收的来源冒烟(spec 4.3):每个来源真联网拉一个榜单,过解析校验。不写参考库。

- 网站返回了页面但解析失败、或榜单地址返回 404 / 500 等 → FAIL(适配器坏了);连不上 / 风控 / 验证码 / robots → UNAVAILABLE(标红,不算失败);
- 都 UNAVAILABLE → 整步 SKIP;有 FAIL → 整步 FAIL;否则 PASS。journal 写到 --out 目录。

    python -X utf8 -m tools.refs.smoke --out <目录>     # 打印并写 <目录>/smoke.json;退出码 0 = PASS,1 = FAIL,2 = SKIP
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.refs.net import FetchError, Fetcher
from tools.refs.sources import ADAPTERS
from tools.refs.sources.base import AdapterError

BOARD = {"hongguo": "hot-real-drama"}


def check_one(list_ranks, board: str, fetcher) -> dict:
    try:
        items = list_ranks(fetcher, board)
        return {"state": "PASS", "items": len(items)}
    except AdapterError as e:
        return {"state": "FAIL", "reason": f"网站返回了页面,但解析失败(适配器坏了):{e}"}
    except FetchError as e:
        if e.kind == "http":         # 404 / 500 等:网站在,但榜单地址不对了 —— 适配器要改
            return {"state": "FAIL", "reason": f"榜单地址返回 HTTP {e.status}(适配器坏了):{e}"}
        return {"state": "UNAVAILABLE", "reason": f"外部不可用({e.kind}):{e}"}


def overall(states: dict[str, str]) -> str:
    v = set(states.values())
    if "FAIL" in v:
        return "FAIL"
    if v <= {"UNAVAILABLE"}:
        return "SKIP"
    return "PASS"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="参考库来源冒烟")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    for key, ad in ADAPTERS.items():
        board = BOARD.get(key, next(iter(ad.boards)))
        res[key] = {"board": board, **check_one(ad.list_ranks, board, Fetcher(out, key))}
        print(f"  [{res[key]['state']}] {key} {board} {res[key].get('reason', res[key].get('items', ''))}", flush=True)
    verdict = overall({k: v["state"] for k, v in res.items()})
    (out / "smoke.json").write_text(json.dumps({"verdict": verdict, "sources": res}, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(f"来源冒烟:{verdict}", flush=True)
    return {"PASS": 0, "FAIL": 1, "SKIP": 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
