# -*- coding: utf-8 -*-
"""资产参考图(角色正面 / 场景空镜 / 道具白底)的规划。

Qwen-Image-Edit 必须有输入图(路线图未决项 #5)。2026-09-23 实测:**纯灰底图 + 「整幅替换」提示词**
能出写实照片质感、无画风残留的素材(`spike-results/2026-09-23-写实真人风格实测.md`)。这里把它定成正式做法。

- 提示词 = 风格前缀 + 资产的 `final_prompt` + 该类资产的硬规范(设计 §6:三视图/无人空镜/白底单品)+ avoid;
- 写实风格下,角色提示词强制写明「虚构人物」—— 不生成任何真实存在的人;
- `final_prompt` 为空、剧没选风格,都直接报错,不用默认值填空。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

from tools.drama import repo
from tools.drama.db import Store
from tools.drama.styles import get_style
from tools.drama.tasks import create_task
from tools.gen.workflows import FRAME_SIZE, QWEN_MODEL

CANVAS = "assets/_canvas/gray_1024.png"

# 各类资产:写回哪个字段、出图尺寸、硬规范
SPEC = {
    "character": ("turnaround_path", lambda aspect: (928, 1664),
                  "Full body, standing straight, front view, arms relaxed, plain white studio background, soft even lighting."),
    "scene": ("image_path", lambda aspect: FRAME_SIZE[aspect],
              "Absolutely no people, no human figures, no silhouettes. Wide establishing shot, eye level, fixed camera."),
    "prop": ("image_path", lambda aspect: (1024, 1024),
             "Single isolated object centered on a pure white background, studio lighting, no people, no hands, no scenery."),
}
FICTIONAL = "A fictional person who does not resemble any real individual."


def _png(width: int, height: int, gray: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    raw = b"".join(b"\x00" + bytes([gray]) * width for _ in range(height))   # 每行:滤波字节 + 灰度像素
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def ensure_canvas(data_root: Path) -> str:
    """灰底输入图,不存在就生成。返回相对 data_root 的路径。"""
    path = Path(data_root) / CANVAS
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_png(1024, 1024, 128))
    return CANVAS


def asset_prompt(table: str, style_key: str, final_prompt: str) -> str:
    style = get_style(style_key)
    who = f" {FICTIONAL}" if table == "character" and style_key == "realistic" else ""
    return (f"Completely replace the entire image with a {style.prefix}.{who} {final_prompt.strip()} "
            f"{SPEC[table][2]} Avoid: {style.avoid}.")


def asset_problems(store: Store, table: str, row_id: int) -> list[str]:
    """出资产图还差什么(不建任务)。空 = 可以规划。"""
    if table not in SPEC:
        raise ValueError(f"不是资产表:{table}")
    row = repo.get(store, table, row_id)
    drama = repo.get(store, "drama", row["drama_id"])
    problems = []
    if not drama["style"]:
        problems.append("这部剧还没选风格预设(在「立项」里选)")
    if not row["final_prompt"].strip():
        problems.append(f"「{row['name']}」缺少生成描述(final_prompt)")
    return problems


def locked_seed(store: Store, table: str, row_id: int) -> int | None:
    """角色的锁定种子(设计 §七「种子锁定」):锁了之后重出参考图只用它 —— 改了描述再出,长相也尽量不跑。"""
    return repo.get(store, table, row_id)["seed"] if table == "character" else None


def lock_seed(store: Store, character_id: int, task_id: int) -> int:
    """把这个角色某一版参考图的种子锁定下来。只认这个角色自己的、已完成的生成记录。"""
    t = repo.get(store, "task", task_id)
    if (t["target_type"], t["target_id"]) != ("character", character_id) or t["state"] != "done":
        raise ValueError(f"task {task_id} 不是这个角色已完成的参考图,不能锁它的种子")
    repo.update(store, "character", character_id, seed=t["seed"])
    return t["seed"]


def unlock_seed(store: Store, character_id: int) -> None:
    repo.update(store, "character", character_id, seed=None)


def plan_asset_image(store: Store, table: str, row_id: int, *, seed: int) -> int:
    problems = asset_problems(store, table, row_id)
    locked = locked_seed(store, table, row_id)
    if locked is not None and seed != locked:
        problems.append(f"种子已锁定为 {locked} —— 要换种子先解锁")
    if problems:
        raise ValueError(";".join(problems))
    row = repo.get(store, table, row_id)
    drama = repo.get(store, "drama", row["drama_id"])
    writes, size_of, _ = SPEC[table]
    params = {
        "workflow": "qwen_image", "preset": "baseline", "size": list(size_of(drama["aspect_ratio"])),
        "image_refs": [CANVAS], "writes": writes, "expect_ext": ".png", "style": drama["style"],
    }
    prompt = asset_prompt(table, drama["style"], row["final_prompt"])
    return create_task(store, kind="image", target_type=table, target_id=row_id, model=QWEN_MODEL,
                       params=params, seed=seed, prompt=prompt, refs=params["image_refs"])
