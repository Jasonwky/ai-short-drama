# -*- coding: utf-8 -*-
"""溯源报告(设计 §6.9):整集的生成链,平台备案的「AI 工具溯源材料」。

从每段的视频出发,按「产物路径 → 产出它的 task → 它的参考素材」一路往回追:
视频 ← 首帧(合成的 / 从上一段视频抽的)← 角色四视图 / 场景空镜 / 道具图 ← …
每个节点记:哪个模型、什么参数、什么种子、什么提示词、何时在哪台机器生成,以及**文件的 sha256**。

**用数据审**:文件的 sha256 必须等于 task 表里记的产物哈希 —— 不等说明文件被替换过,
报告就不是真的,`problems()` 会列出来,导出据此拒绝。没有生成记录的素材(人工提供)照实标出。
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.drama import repo
from tools.drama.db import Store
from tools.serve.dispatch import sha256_file

LICENSE_NOTE = ("视频由 MiniMax H3 生成(Powered by MiniMax H3)。依 MiniMax H3 Community License,"
                "其输出不得在欧盟、英国、韩国、美国使用、分发或展示;使用限制须逐级传递给下游。")


def _task_for(store: Store, path: str):
    return store.conn.execute("SELECT * FROM task WHERE state = 'done' AND result_path = ? ORDER BY id DESC LIMIT 1",
                              (path,)).fetchone()


def _node(store: Store, root: Path, path: str) -> dict:
    f = root / path
    node = {"path": path, "sha256": sha256_file(f) if f.is_file() else None, "task": None, "inputs": []}
    t = _task_for(store, path)
    if t is None:
        return node
    if t["host_id"] is not None:
        host = repo.get(store, "host", t["host_id"])["label"]
    else:   # 本地确定性生成没有 external_id;有 external_id 却没有 host 的,是从别处导入的工程
        host = "本机" if t["external_id"] is None else "未登记的机器(导入的工程)"
    node["task"] = {"id": t["id"], "kind": t["kind"], "model": t["model"], "params": json.loads(t["params_json"]),
                    "seed": t["seed"], "prompt": t["prompt"], "host": host, "external_id": t["external_id"],
                    "created_at": t["created_at"], "finished_at": t["finished_at"],
                    "result_sha256": t["result_sha256"]}
    node["inputs"] = [_node(store, root, r) for r in json.loads(t["refs_json"])]   # 参考素材都早于产物,链无环
    return node


def build(store: Store, data_root: Path, episode_id: int, *, produce_id: str, producer: str,
          videos: list[dict]) -> dict:
    """videos:导出时快照的 [{segment_id, number, video_path, subtitle_path?}],按顺序。
    字幕的生成记录(对齐任务)以视频为输入,所以字幕这条链也一路追到素材。"""
    ep = repo.get(store, "episode", episode_id)
    drama = repo.get(store, "drama", ep["drama_id"])
    segs = []
    for v in videos:
        seg = repo.get(store, "segment", v["segment_id"])
        sub = v.get("subtitle_path")
        segs.append({"number": v["number"], "duration_sec": seg["duration_sec"], "description": seg["description"],
                     "video": _node(store, Path(data_root), v["video_path"]),
                     "subtitles": _node(store, Path(data_root), sub) if sub else None})
    return {"produce_id": produce_id, "content_producer": producer, "drama": drama["title"],
            "aspect_ratio": drama["aspect_ratio"], "episode": ep["number"], "license": LICENSE_NOTE,
            "models": sorted(_models([n for s in segs for n in (s["video"], s["subtitles"]) if n])), "segments": segs}


def _models(nodes: list[dict]) -> set[str]:
    out = set()
    for n in nodes:
        if n["task"]:
            out.add(n["task"]["model"])
        out |= _models(n["inputs"])
    return out


def _walk(node: dict):
    yield node
    for i in node["inputs"]:
        yield from _walk(i)


def problems(report: dict) -> list[str]:
    out = []
    for s in report["segments"]:
        roots = [s["video"]] + ([s["subtitles"]] if s.get("subtitles") else [])
        for n in (n for r in roots for n in _walk(r)):
            if n["sha256"] is None:
                out.append(f"段落 {s['number']}:文件不在本机 {n['path']}")
            elif n["task"] and n["task"]["result_sha256"] and n["sha256"] != n["task"]["result_sha256"]:
                out.append(f"段落 {s['number']}:{n['path']} 的内容与 task #{n['task']['id']} 记录的产物哈希不一致"
                           "(文件被替换过)—— 溯源不成立")
        if s["video"]["task"] is None:
            out.append(f"段落 {s['number']}:视频 {s['video']['path']} 在 task 表里没有生成记录")
        if s.get("subtitles") and s["subtitles"]["task"] is None:
            out.append(f"段落 {s['number']}:字幕 {s['subtitles']['path']} 在 task 表里没有生成记录")
    return list(dict.fromkeys(out))      # 字幕链里又走一遍视频,同一条问题只报一次


def to_markdown(report: dict) -> str:
    lines = [f"# 溯源报告 · {report['drama']} 第 {report['episode']} 集", "",
             f"- 内容制作编号(ProduceID):`{report['produce_id']}`",
             f"- 生成合成服务提供者编码:`{report['content_producer']}`",
             f"- 画幅:{report['aspect_ratio']}", f"- 用到的模型:{'、'.join(report['models'])}",
             f"- 许可证:{report['license']}", ""]

    def emit(node: dict, depth: int) -> None:
        pad = "  " * depth
        t = node["task"]
        if t is None:
            lines.append(f"{pad}- `{node['path']}` —— 无生成记录(人工提供)· sha256 `{(node['sha256'] or '缺失')[:16]}`")
        else:
            lines.append(f"{pad}- `{node['path']}` ← task #{t['id']} · {t['model']} · 种子 {t['seed']} · "
                         f"{t['host']} · {t['finished_at']} · sha256 `{(node['sha256'] or '缺失')[:16]}`")
            lines.append(f"{pad}  提示词:{t['prompt']}")
        for i in node["inputs"]:
            emit(i, depth + 1)

    for s in report["segments"]:
        lines += [f"## 段落 {s['number']}({s['duration_sec']} 秒)", "", s["description"], ""]
        emit(s["video"], 0)
        if s.get("subtitles"):
            lines.append("字幕:")
            emit(s["subtitles"], 0)
        lines.append("")
    return "\n".join(lines)
