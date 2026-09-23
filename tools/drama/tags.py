# -*- coding: utf-8 -*-
"""把 `@名字` 展开成模型认的编号标签,并做标签双向校验。

两种方言:
- `qwen`:Qwen-Image-Edit 的节点自动在提示词前拼「Picture 1: <vision…>」,正文写 `Picture N`
- `h3`:MiniMax H3 写 `<Picture i>` / `<Audio j>`,每类各自从 1 编号

🔴 ComfyUI 节点对标签**零校验**,写错编号静默错位。云机上一个已出片的工作流,
提示词写着「…and <Audio 1> exactly as it is」,而工作流里根本没接音频。

校验规则(闸门 0 实测后定):
1. 越界引用恒报;
2. 「每张接入的图都要被引用」只在 ≥2 张图时成立 —— 单图没有歧义;
3. 接了参考音频就必须引用 `<Audio N>`,否则模型不知道这段音频做什么用。
"""
from __future__ import annotations

import re

from tools.drama.refs import Ref

_FORMAT = {"qwen": "Picture {}", "h3": "<Picture {}>"}
_IMAGE_TAG = {"qwen": re.compile(r"(?<!<)\bPicture\s*(\d+)"), "h3": re.compile(r"<Picture\s+(\d+)>")}
_AUDIO_TAG = re.compile(r"<Audio\s+(\d+)>")


def expand(prompt: str, kept: list[Ref], dropped: list[Ref], dialect: str) -> str:
    """`@名字` → 编号标签(后面保留名字,人还读得懂);出槽的素材退化成纯名字。

    提示词里出现未绑定的 `@名字` 直接抛错 —— 不许静默跳过。
    """
    fmt = _FORMAT[dialect]
    index = {r.name: i + 1 for i, r in enumerate(kept)}
    by_len = sorted([r.name for r in kept] + [r.name for r in dropped], key=len, reverse=True)
    out: list[str] = []
    i = 0
    while (j := prompt.find("@", i)) >= 0:
        out.append(prompt[i:j])
        rest = prompt[j + 1:]
        hit = next((n for n in by_len if rest.startswith(n)), None)
        if hit is None:
            raise ValueError(f"提示词里的 @{rest[:12]} 不是本镜绑定的素材 —— 不许静默跳过")
        out.append(f"{fmt.format(index[hit])}({hit})" if hit in index else hit)
        i = j + 1 + len(hit)
    out.append(prompt[i:])
    return "".join(out)


def check_tags(prompt: str, n_images: int, n_audios: int = 0, dialect: str = "h3") -> list[str]:
    problems: list[str] = []
    images = {int(n) for n in _IMAGE_TAG[dialect].findall(prompt)}
    audios = {int(n) for n in _AUDIO_TAG.findall(prompt)} if dialect == "h3" else set()
    for n in sorted(images):
        if not 1 <= n <= n_images:
            problems.append(f"提示词引用了第 {n} 张参考图,但只接入了 {n_images} 张")
    for n in sorted(audios):
        if not 1 <= n <= n_audios:
            problems.append(f"提示词引用了 <Audio {n}>,但只接入了 {n_audios} 段参考音频")
    if n_images >= 2:
        for n in range(1, n_images + 1):
            if n not in images:
                problems.append(f"接了 {n_images} 张参考图,但提示词从未引用第 {n} 张 —— 多图必须逐一指代")
    for n in range(1, n_audios + 1):
        if n not in audios:
            problems.append(f"接了参考音频,但提示词从未引用 <Audio {n}> —— 模型不知道这段音频做什么用")
    return problems
