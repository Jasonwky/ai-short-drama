# -*- coding: utf-8 -*-
"""段落 readiness:一串带人话的检查项,不是一个布尔值(设计 §6.4)。

界面上直接显示「还差什么」。包含外部依赖(云机通不通、模型配没配)—— 那往往是最常见的没准备好。
readiness 是**可计算的**,不是人手工点的;它和任务状态(task.state)是两个独立维度。

`warn=True` 的项不阻塞生成,但必须让人看见(例如素材因槽位不足退化成文字描述)。
"""
from __future__ import annotations

from dataclasses import dataclass

from tools.drama import refs, repo, rules
from tools.drama.db import Store
from tools.gen.capability import Capability

# 各参考模式需要哪些参考帧。key / first_last_key / anchors 尚未实现 —— 报出来,不假装支持。
FRAMES_NEEDED: dict[str, tuple[str, ...]] = {
    "text_only": (),
    "first": ("first_frame_path",),
    "last": ("last_frame_path",),
    "first_last": ("first_frame_path", "last_frame_path"),
}
FRAME_LABEL = {"first_frame_path": "首帧", "last_frame_path": "尾帧"}
KIND_LABEL = {"character": "角色", "scene": "场景", "prop": "道具"}


@dataclass(frozen=True)
class Check:
    key: str
    ok: bool
    msg: str
    warn: bool = False


def is_ready(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)


def segment_readiness(store: Store, segment_id: int, *, video_cap: Capability, image_cap: Capability,
                      host_ok: bool, host_msg: str, model_ok: bool, model_msg: str) -> list[Check]:
    seg = repo.get(store, "segment", segment_id)
    assets = repo.segment_assets(store, segment_id)
    chars, scene, props = assets["characters"], assets["scene"], assets["props"]
    mode = seg["reference_mode"]
    needs_images = mode != "text_only"
    checks: list[Check] = []

    problems = rules.check_segment(seg["description"], seg["duration_sec"], video_cap)
    checks.append(Check("rules", not problems, ";".join(problems) or "时长与子镜头结构合规"))

    missing = []
    if not seg["video_prompt"].strip():
        missing.append("视频提示词")
    if needs_images and not seg["image_prompt"].strip():
        missing.append("首帧图提示词")
    checks.append(Check("prompt_ready", not missing,
                        ("缺少" + "、".join(missing)) if missing else "提示词齐备"))

    if needs_images:
        lack = [] if scene is not None else ["未绑定场景"]
        lack += [f"角色「{c['name']}」缺四视图" for c in chars if not c["turnaround_path"]]
        if scene is not None and not scene["image_path"]:
            lack.append(f"场景「{scene['name']}」缺空镜图")
        lack += [f"道具「{p['name']}」缺白底图" for p in props if not p["image_path"]]
        checks.append(Check("assets_bound", not lack, ";".join(lack) or "参考素材齐备"))

    bound = ([c["name"] for c in chars] + ([scene["name"]] if scene is not None else [])
             + [p["name"] for p in props])
    found, unknown = refs.find_mentions(seg["video_prompt"], bound)
    bad = [f"@{u} 不是本镜绑定的素材" for u in unknown]
    if len(bound) >= 2:
        bad += [f"绑定了「{n}」但视频提示词从未 @ 它" for n in bound if n not in found]
    checks.append(Check("mentions", not bad, ";".join(bad) or "提示词引用与绑定一致"))

    need = FRAMES_NEEDED.get(mode)
    if need is None:
        checks.append(Check("refs_ready", False, f"参考模式 {mode} 尚未实现"))
    else:
        lack_frames = [FRAME_LABEL[f] for f in need if not seg[f]]
        if lack_frames:
            checks.append(Check("refs_ready", False, f"参考模式 {mode},缺少" + "、".join(lack_frames)))
        else:
            checks.append(Check("refs_ready", True, f"参考模式 {mode},参考帧齐备"))

    if needs_images:
        spoken = rules.dialogue_by_speaker(seg["description"])
        weighted = [(refs.Ref("character", c["name"], c["turnaround_path"]),
                     sum(n for who, n in spoken.items() if who.startswith(c["name"]))) for c in chars]
        scene_ref = refs.Ref("scene", scene["name"], scene["image_path"]) if scene is not None else None
        prop_refs = [refs.Ref("prop", p["name"], p["image_path"]) for p in props]
        _, dropped = refs.allocate_slots(weighted, scene_ref, prop_refs, image_cap.max_ref_images)
        if dropped:
            msg = ";".join(f"{KIND_LABEL[r.kind]}「{r.name}」因槽位不足未作为参考图,仅以文字描述参与"
                           for r in dropped)
            checks.append(Check("slots", True, msg, warn=True))

    checks.append(Check("host_ready", host_ok, host_msg))
    checks.append(Check("video_model_ready", model_ok, model_msg))
    return checks
