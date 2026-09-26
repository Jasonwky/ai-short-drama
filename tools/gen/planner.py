# -*- coding: utf-8 -*-
"""把「这个段落要出首帧 / 尾帧 / 视频」变成一条不可变的生成任务。

**规划 ≠ 提交**:规划只看**内容**是否就绪(素材、提示词、引用、时长),在控制面上立刻完成;
云机通不通、模型在不在,是提交时(worker)的事。

任务一旦创建,提示词、参考素材、种子、参数都定死(P1 的数据库触发器保证),
worker 只负责把它原样交给云机 —— 这样「当时到底用了什么生成的」永远可查。

闸门 0 带来的两条规矩:
- 首帧是**合成**:必须用目标画幅的空 latent,尺寸来自剧的画幅,不来自参考图(0-3);
- 视频**不喂参考音频**:不喂也一致、喂了会复读(0-4)。音色靠角色的声音描述维持。

参考模式(2026-09-26 云机实测,`spike-results/2026-09-26-参考锚定与四视图云机实测.md`):参考帧除了当 <Picture 1>,
还要**钉住**(首帧钉第 0 帧、尾帧钉最后一帧)—— 只当参考图喂,参考图一多首帧就守不住(第一帧差 74.2)。
有四视图的角色把四视图也当参考图。
"""
from __future__ import annotations

from tools.drama import refs, repo, rules, tags
from tools.drama.db import Store
from tools.drama.readiness import segment_readiness
from tools.drama.tasks import create_task
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.gen.workflows import FRAME_SIZE, H3_IMAGE_SLOTS, H3_MODEL, QWEN_MODEL, VIDEO_SIZE

# readiness 里属于「提交时」的检查项;规划阶段不看
RUNTIME_KEYS = {"host_ready", "video_model_ready"}


def _aspect(store: Store, segment_id: int) -> str:
    row = store.conn.execute("SELECT d.aspect_ratio FROM segment s JOIN episode e ON e.id = s.episode_id "
                             "JOIN drama d ON d.id = e.drama_id WHERE s.id = ?", (segment_id,)).fetchone()
    return row[0]


def _slot_refs(seg, assets) -> tuple[list[refs.Ref], list[refs.Ref]]:
    chars, scene, props = assets["characters"], assets["scene"], assets["props"]
    spoken = rules.dialogue_by_speaker(seg["description"])
    weighted = [(refs.Ref("character", c["name"], c["turnaround_path"]),
                 sum(n for who, n in spoken.items() if who.startswith(c["name"]))) for c in chars]
    scene_ref = refs.Ref("scene", scene["name"], scene["image_path"]) if scene is not None else None
    prop_refs = [refs.Ref("prop", p["name"], p["image_path"]) for p in props]
    return refs.allocate_slots(weighted, scene_ref, prop_refs, QWEN_IMAGE_EDIT_2511.max_ref_images)


# 参考帧字段 → 用它的参考模式、它的提示词字段、界面上的叫法
FRAMES = {
    "first_frame_path": (("first", "first_last"), "image_prompt", "首帧"),
    "last_frame_path": (("last", "first_last"), "last_image_prompt", "尾帧"),
}
# 视频:各参考模式喂哪张帧当 <Picture 1>、钉哪几帧(0 = 第一帧,-1 = 最后一帧)
VIDEO_MODES = {
    "first": ("first_frame_path", "是本段的首帧。", (("first_frame_path", 0),)),
    "last": ("last_frame_path", "是本段的尾帧(最后一个画面)。", (("last_frame_path", -1),)),
    "first_last": ("first_frame_path", "是本段的首帧。", (("first_frame_path", 0), ("last_frame_path", -1))),
}


def _frame_problems(store: Store, segment_id: int, field: str) -> list[str]:
    modes, prompt_field, label = FRAMES[field]
    seg = repo.get(store, "segment", segment_id)
    if seg["reference_mode"] not in modes:
        return [f"参考模式 {seg['reference_mode']} 不用{label}"]
    assets = repo.segment_assets(store, segment_id)
    chars, scene, props = assets["characters"], assets["scene"], assets["props"]

    problems = []
    if not seg[prompt_field].strip():
        problems.append(f"缺少{label}图提示词")
    if scene is None:
        problems.append("未绑定场景")
    problems += [f"角色「{c['name']}」缺正面定妆图" for c in chars if not c["turnaround_path"]]
    if scene is not None and not scene["image_path"]:
        problems.append(f"场景「{scene['name']}」缺空镜图")
    problems += [f"道具「{p['name']}」缺白底图" for p in props if not p["image_path"]]
    bound = [c["name"] for c in chars] + ([scene["name"]] if scene is not None else []) + [p["name"] for p in props]
    found, unknown = refs.find_mentions(seg[prompt_field], bound)
    problems += [f"@{u} 不是本镜绑定的素材" for u in unknown]
    if len(bound) >= 2:
        problems += [f"绑定了「{n}」但{label}图提示词从未 @ 它" for n in bound if n not in found]
    return problems


def _plan_frame(store: Store, segment_id: int, field: str, seed: int) -> int:
    problems = _frame_problems(store, segment_id, field)
    if problems:
        raise ValueError(";".join(problems))
    seg = repo.get(store, "segment", segment_id)
    assets = repo.segment_assets(store, segment_id)
    kept, dropped = _slot_refs(seg, assets)
    prompt = tags.expand(seg[FRAMES[field][1]], kept, dropped, "qwen")
    params = {
        "workflow": "qwen_image", "preset": "fast", "size": list(FRAME_SIZE[_aspect(store, segment_id)]),
        "image_refs": [r.path for r in kept], "writes": field, "expect_ext": ".png",
        "dropped": [r.name for r in dropped],   # 出槽退化成文字的素材,溯源里要看得到
    }
    return create_task(store, kind="image", target_type="segment", target_id=segment_id, model=QWEN_MODEL,
                       params=params, seed=seed, prompt=prompt, refs=params["image_refs"])


def first_frame_problems(store: Store, segment_id: int) -> list[str]:
    """出首帧还差什么(只看内容,不建任务)。空 = 可以规划。增量补全靠它判「不能做」。"""
    return _frame_problems(store, segment_id, "first_frame_path")


def last_frame_problems(store: Store, segment_id: int) -> list[str]:
    return _frame_problems(store, segment_id, "last_frame_path")


def plan_first_frame(store: Store, segment_id: int, *, seed: int) -> int:
    return _plan_frame(store, segment_id, "first_frame_path", seed)


def plan_last_frame(store: Store, segment_id: int, *, seed: int) -> int:
    """尾帧与首帧同一套合成(同样的素材槽位、同样的快档),只是用尾帧图提示词、写回尾帧。"""
    return _plan_frame(store, segment_id, "last_frame_path", seed)


def fourview_refs(store: Store, segment_id: int) -> tuple[list[refs.Ref], list[refs.Ref]]:
    """进 H3 参考图的角色四视图(台词多的优先),与因槽位不够没进去的。
    工作流有 3 个图槽,<Picture 1> 是参考帧,四视图最多 2 张。"""
    seg = repo.get(store, "segment", segment_id)
    spoken = rules.dialogue_by_speaker(seg["description"])
    weighted = [(refs.Ref("character", c["name"], c["fourview_path"]),
                 sum(n for who, n in spoken.items() if who.startswith(c["name"])))
                for c in repo.segment_assets(store, segment_id)["characters"] if c["fourview_path"]]
    return refs.allocate_slots(weighted, None, [], H3_IMAGE_SLOTS - 1)


def video_problems(store: Store, segment_id: int) -> list[str]:
    """出视频还差什么(只看内容;云机与模型是提交时的事)。空 = 可以规划。"""
    checks = segment_readiness(store, segment_id, video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511,
                               host_ok=True, host_msg="提交时检查", model_ok=True, model_msg="提交时检查")
    blocking = [c.msg for c in checks if not c.ok and c.key not in RUNTIME_KEYS]
    if blocking:
        return blocking
    seg = repo.get(store, "segment", segment_id)
    if seg["reference_mode"] not in VIDEO_MODES:
        return [f"参考模式 {seg['reference_mode']} 的视频生成尚未实现(已实现:{'、'.join(VIDEO_MODES)})"]
    return []


def plan_video(store: Store, segment_id: int, *, seed: int) -> int:
    problems = video_problems(store, segment_id)
    if problems:
        raise ValueError(";".join(problems))
    seg = repo.get(store, "segment", segment_id)
    ref_field, intro, pins = VIDEO_MODES[seg["reference_mode"]]
    views, _ = fourview_refs(store, segment_id)

    assets = repo.segment_assets(store, segment_id)
    everyone = ([refs.Ref("character", c["name"], None) for c in assets["characters"]]
                + ([refs.Ref("scene", assets["scene"]["name"], None)] if assets["scene"] is not None else [])
                + [refs.Ref("prop", p["name"], None) for p in assets["props"]])
    # @名字都退化为文字:四视图只在开头逐张说明是谁(与云机实测的写法一致),正文不写 <Picture N>
    body = tags.expand(seg["video_prompt"], [], everyone, "h3")
    # 台词逐字进提示词:模型不会从别处知道要说什么(P2 真链路漏了它,说出的是无意义音节)
    lines = "".join(f"{who}用普通话说:「{line}」" for who, line in rules.dialogue_lines(seg["description"]))
    voices = "".join(f"{c['name']}的声音:{c['voice_desc']}。" for c in assets["characters"] if c["voice_desc"])
    who = "".join(f"<Picture {k}> 是{r.name}的四视图(正面、左侧、背面、右侧)。" for k, r in enumerate(views, 2))
    prompt = f"<Picture 1> {intro}" + who + body + "".join(f" {part}" for part in (lines, voices) if part)

    image_refs = [seg[ref_field]] + [r.path for r in views]
    anchors = [[seg[f], idx] for f, idx in pins]
    params = {
        "workflow": "minimax_h3", "frames": MINIMAX_H3.frames_for(seg["duration_sec"]),
        "size": list(VIDEO_SIZE[_aspect(store, segment_id)]),
        "image_refs": image_refs, "anchors": anchors, "audio_ref": None,
        "writes": "video_path", "expect_ext": ".mp4",
    }
    return create_task(store, kind="video", target_type="segment", target_id=segment_id, model=H3_MODEL,
                       params=params, seed=seed, prompt=prompt,
                       refs=list(dict.fromkeys([*image_refs, *(p for p, _ in anchors)])))
