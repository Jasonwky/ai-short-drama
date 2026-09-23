# -*- coding: utf-8 -*-
"""把「这个段落要出首帧 / 要出视频」变成一条不可变的生成任务。

**规划 ≠ 提交**:规划只看**内容**是否就绪(素材、提示词、引用、时长),在控制面上立刻完成;
云机通不通、模型在不在,是提交时(worker)的事。

任务一旦创建,提示词、参考素材、种子、参数都定死(P1 的数据库触发器保证),
worker 只负责把它原样交给云机 —— 这样「当时到底用了什么生成的」永远可查。

闸门 0 带来的两条规矩:
- 首帧是**合成**:必须用目标画幅的空 latent,尺寸来自剧的画幅,不来自参考图(0-3);
- 视频**不喂参考音频**:不喂也一致、喂了会复读(0-4)。音色靠角色的声音描述维持。
"""
from __future__ import annotations

from tools.drama import refs, repo, rules, tags
from tools.drama.db import Store
from tools.drama.readiness import segment_readiness
from tools.drama.tasks import create_task
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.gen.workflows import FRAME_SIZE, H3_MODEL, QWEN_MODEL, VIDEO_SIZE

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


def plan_first_frame(store: Store, segment_id: int, *, seed: int) -> int:
    seg = repo.get(store, "segment", segment_id)
    if seg["reference_mode"] != "first":
        raise ValueError(f"参考模式 {seg['reference_mode']} 的首帧规划尚未实现(当前只支持 first)")
    assets = repo.segment_assets(store, segment_id)
    chars, scene, props = assets["characters"], assets["scene"], assets["props"]

    problems = []
    if not seg["image_prompt"].strip():
        problems.append("缺少首帧图提示词")
    if scene is None:
        problems.append("未绑定场景")
    problems += [f"角色「{c['name']}」缺四视图" for c in chars if not c["turnaround_path"]]
    if scene is not None and not scene["image_path"]:
        problems.append(f"场景「{scene['name']}」缺空镜图")
    problems += [f"道具「{p['name']}」缺白底图" for p in props if not p["image_path"]]
    bound = [c["name"] for c in chars] + ([scene["name"]] if scene is not None else []) + [p["name"] for p in props]
    found, unknown = refs.find_mentions(seg["image_prompt"], bound)
    problems += [f"@{u} 不是本镜绑定的素材" for u in unknown]
    if len(bound) >= 2:
        problems += [f"绑定了「{n}」但首帧图提示词从未 @ 它" for n in bound if n not in found]
    if problems:
        raise ValueError(";".join(problems))

    kept, dropped = _slot_refs(seg, assets)
    prompt = tags.expand(seg["image_prompt"], kept, dropped, "qwen")
    params = {
        "workflow": "qwen_image", "preset": "fast", "size": list(FRAME_SIZE[_aspect(store, segment_id)]),
        "image_refs": [r.path for r in kept], "writes": "first_frame_path", "expect_ext": ".png",
        "dropped": [r.name for r in dropped],   # 出槽退化成文字的素材,溯源里要看得到
    }
    return create_task(store, kind="image", target_type="segment", target_id=segment_id, model=QWEN_MODEL,
                       params=params, seed=seed, prompt=prompt, refs=params["image_refs"])


def plan_video(store: Store, segment_id: int, *, seed: int) -> int:
    checks = segment_readiness(store, segment_id, video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511,
                               host_ok=True, host_msg="提交时检查", model_ok=True, model_msg="提交时检查")
    blocking = [c.msg for c in checks if not c.ok and c.key not in RUNTIME_KEYS]
    if blocking:
        raise ValueError(";".join(blocking))
    seg = repo.get(store, "segment", segment_id)
    if seg["reference_mode"] != "first":
        raise ValueError(f"参考模式 {seg['reference_mode']} 的视频生成尚未实现(ref2va 只接首帧参考)")

    assets = repo.segment_assets(store, segment_id)
    everyone = ([refs.Ref("character", c["name"], None) for c in assets["characters"]]
                + ([refs.Ref("scene", assets["scene"]["name"], None)] if assets["scene"] is not None else [])
                + [refs.Ref("prop", p["name"], None) for p in assets["props"]])
    body = tags.expand(seg["video_prompt"], [], everyone, "h3")   # 视频阶段只接首帧一张图,@名字都退化为文字
    # 台词逐字进提示词:模型不会从别处知道要说什么(P2 真链路漏了它,说出的是无意义音节)
    lines = "".join(f"{who}用普通话说:「{line}」" for who, line in rules.dialogue_lines(seg["description"]))
    voices = "".join(f"{c['name']}的声音:{c['voice_desc']}。" for c in assets["characters"] if c["voice_desc"])
    prompt = "<Picture 1> 是本段的首帧。" + body + "".join(f" {part}" for part in (lines, voices) if part)

    params = {
        "workflow": "minimax_h3", "frames": MINIMAX_H3.frames_for(seg["duration_sec"]),
        "size": list(VIDEO_SIZE[_aspect(store, segment_id)]),
        "image_refs": [seg["first_frame_path"]], "audio_ref": None,
        "writes": "video_path", "expect_ext": ".mp4",
    }
    return create_task(store, kind="video", target_type="segment", target_id=segment_id, model=H3_MODEL,
                       params=params, seed=seed, prompt=prompt, refs=params["image_refs"])
