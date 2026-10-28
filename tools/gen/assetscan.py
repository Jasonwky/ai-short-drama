# -*- coding: utf-8 -*-
"""资产图的代码检查(spec 2026-10-28-成片质量闸门-第一期-design.md 第 6.2 节):能确定性测的不交给 LLM。

阈值在题甲 68 张真资产图上标定,低于阈值的每张都人眼看过(spike-results/2026-10-28-资产图白底检查标定.md):
浅色底比例同时抓住「人物被裁切」—— 半身像的肩膀、腿顶到边,边带上的浅色像素就少了。
本机 ffmpeg 解像素(不加图像库依赖);结果只给人看,拦不拦由人定稿决定。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

WIDTH = 256           # 缩到这个宽度再量(够判边带与分格,快)
BAND = 4              # 边带宽(缩放后的像素)
LIGHT_MIN = 200       # 「浅色无彩」:三通道最小值 ≥ 200 …
GRAY_SPREAD = 20      # … 且最大最小差 ≤ 20(影棚浅灰渐变底也算)
BORDER_MIN = {"image": 0.85, "fourview": 0.70}
QUARTER_MIN = 0.5     # 四视图每一格至少有一列 ≥ 这个比例是非浅色(格里有人)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _rgb(path: Path) -> tuple[bytes, int]:
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={WIDTH}:-2",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return out, len(out) // (WIDTH * 3)


def _light(px: bytes) -> bool:
    return min(px) >= LIGHT_MIN and max(px) - min(px) <= GRAY_SPREAD


def border_ratio(data: bytes, height: int) -> float:
    n = light = 0
    for y in range(height):
        xs = range(WIDTH) if y < BAND or y >= height - BAND else (*range(BAND), *range(WIDTH - BAND, WIDTH))
        for x in xs:
            i = (y * WIDTH + x) * 3
            n += 1
            light += _light(data[i:i + 3])
    return light / n


def quarter_fill(data: bytes, height: int) -> list[float]:
    cols = [sum(not _light(data[(y * WIDTH + x) * 3:(y * WIDTH + x) * 3 + 3]) for y in range(height)) / height
            for x in range(WIDTH)]
    q = WIDTH // 4
    return [max(cols[k * q:(k + 1) * q]) for k in range(4)]


def check(path: Path, table: str, slot: str) -> list[Check]:
    """场景空镜不查(本来就不是白底)。"""
    if table == "scene":
        return []
    data, h = _rgb(path)
    ratio = border_ratio(data, h)
    need = BORDER_MIN[slot]
    out = [Check("浅色底、主体没顶到边", ratio >= need,
                 f"四边浅色像素占 {ratio:.0%}(要 ≥ {need:.0%};低了多半是背景不是白底,或人物 / 物件被裁切)")]
    if slot == "fourview":
        fill = quarter_fill(data, h)
        out.append(Check("四格都有人", min(fill) >= QUARTER_MIN,
                         "四格主体高度占比 " + " / ".join(f"{x:.0%}" for x in fill) + f"(每格要 ≥ {QUARTER_MIN:.0%})"))
    return out


CACHE_DIR = "outputs/asset_check"


def cached(data_root: Path, rel: str, table: str, slot: str) -> list[Check]:
    """按图的 sha256 缓存(面板每次打开不重算 57 张);图换了 sha 就变,自动重算。"""
    from tools.serve.dispatch import sha256_file     # serve 依赖 gen,用到时再取
    root = Path(data_root)
    sha = sha256_file(root / rel)
    f = root / CACHE_DIR / f"{sha[:16]}_{table}_{slot}.scan.json"
    if f.is_file():
        return [Check(**c) for c in json.loads(f.read_text(encoding="utf-8"))]
    out = check(root / rel, table, slot)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([c.__dict__ for c in out], ensure_ascii=False), encoding="utf-8")
    return out
