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
# 中间关键帧:叠加在任何参考模式上(spike-results/2026-10-09-中间关键帧锚定实测.md)。离两头至少这么远
KEY_MARGIN_SEC = 0.5
# 一段多张(specs/2026-10-15-多关键帧与草稿档-design.md §2.2):张与张之间至少隔这么远(0.5 秒 = 12 帧,帧号不会相同);
# 最多 3 张(用户 2026-10-15 定),真机只验证到 2 张(spike-results/2026-10-14-开卡调研批次-运镜关键帧步数.md)
KEY_GAP_SEC = 0.5
KEY_MAX = 3
KEY_TESTED = 2
KIND_LABEL = {"character": "角色", "scene": "场景", "prop": "道具"}


@dataclass(frozen=True)
class Check:
    key: str
    ok: bool
    msg: str
    warn: bool = False


def is_ready(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)


def key_frame_index(time_sec: float, cap: Capability) -> int:
    """关键帧钉在第几帧:秒 × 模型帧率(AddGuide 的 frame_idx 是像素帧号,不按 latent 取整)。"""
    return round(time_sec * cap.fps)


def key_problems(seg, key, cap: Capability) -> list[str]:
    """一张关键帧还差什么。范围按**实际帧数**算(时长会吸附到模型的帧网格上)。每条都写明是第几秒的那张。"""
    t, dur = key["time_sec"], seg["duration_sec"]
    problems = []
    if not KEY_MARGIN_SEC <= t <= dur - KEY_MARGIN_SEC:
        problems.append(f"第 {t:g} 秒的关键帧不在 {KEY_MARGIN_SEC:g}–{dur - KEY_MARGIN_SEC:g} 秒之间")
    else:
        idx = key_frame_index(t, cap)
        if idx <= 0 or idx >= cap.frames_for(dur) - 1:
            problems.append(f"第 {t:g} 秒的关键帧落在第 {idx} 帧,与首帧 / 尾帧锚点重合")
    if not key["image_prompt"].strip():
        problems.append(f"第 {t:g} 秒的关键帧缺图提示词")
    if not key["frame_path"]:
        problems.append(f"第 {t:g} 秒的关键帧还没有图(在「参考帧」面板生成)")
    return problems


def keys_problems(seg, keys, cap: Capability) -> list[str]:
    """本段全部关键帧(按时刻先后)还差什么:张数、逐张、张与张之间的间距。"""
    problems = [f"最多 {KEY_MAX} 张关键帧,现在 {len(keys)} 张"] if len(keys) > KEY_MAX else []
    if seg["duration_sec"] is None:
        return problems + ["段落没有时长,算不出关键帧在第几帧"]
    for k in keys:
        problems += key_problems(seg, k, cap)
    for a, b in zip(keys, keys[1:]):
        if b["time_sec"] - a["time_sec"] < KEY_GAP_SEC:
            problems.append(f"第 {a['time_sec']:g} 秒与第 {b['time_sec']:g} 秒的关键帧相隔不到 {KEY_GAP_SEC:g} 秒")
    return problems


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
    if mode in ("first", "first_last") and not seg["image_prompt"].strip():
        missing.append("首帧图提示词")
    if mode in ("last", "first_last") and not seg["last_image_prompt"].strip():
        missing.append("尾帧图提示词")
    checks.append(Check("prompt_ready", not missing,
                        ("缺少" + "、".join(missing)) if missing else "提示词齐备"))

    if needs_images:
        lack = [] if scene is not None else ["未绑定场景"]
        lack += [f"角色「{c['name']}」缺正面定妆图" for c in chars if not c["turnaround_path"]]
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

    keys = repo.keyframes(store, segment_id)
    if keys:
        bad_key = keys_problems(seg, keys, video_cap)
        at = "、".join(f"第 {k['time_sec']:g} 秒 = 第 {key_frame_index(k['time_sec'], video_cap)} 帧" for k in keys)
        checks.append(Check("keyframe", not bad_key, ";".join(bad_key) or f"关键帧({at})已就绪"))
        if len(keys) > KEY_TESTED:
            checks.append(Check("keyframe_count", True,
                                f"{len(keys)} 张关键帧:此数量未经真机验证(实测到 {KEY_TESTED} 张)", warn=True))
        if mode == "first_last":
            checks.append(Check("keyframe_untested", True, "首尾帧 + 关键帧三个锚点同时用:此组合未经真机验证", warn=True))

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
        no_view = [c["name"] for c in chars if not c["fourview_path"]]
        if no_view:
            checks.append(Check("fourview", True, "".join(f"角色「{n}」" for n in no_view)
                                + "没有四视图,视频里只靠首帧与文字认人", warn=True))

    checks.append(Check("host_ready", host_ok, host_msg))
    checks.append(Check("video_model_ready", model_ok, model_msg))
    return checks
