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

from tools.drama import blocking, framing, refs, repo, rules, tags
from tools.drama.db import Store
from tools.drama.readiness import key_frame_index, segment_readiness
from tools.drama.targets import KEY_WRITES
from tools.drama.tasks import create_task
from tools.gen import compose, presets
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.gen.workflows import DRAFT_STEPS, FRAME_SIZE, H3_IMAGE_SLOTS, H3_MODEL, QWEN_MODEL, VIDEO_SIZE, build_h3

# readiness 里属于「提交时」的检查项;规划阶段不看
RUNTIME_KEYS = {"host_ready", "video_model_ready"}


def _aspect(store: Store, segment_id: int) -> str:
    row = store.conn.execute("SELECT d.aspect_ratio FROM segment s JOIN episode e ON e.id = s.episode_id "
                             "JOIN drama d ON d.id = e.drama_id WHERE s.id = ?", (segment_id,)).fetchone()
    return row[0]


def _slot_refs(seg, assets) -> tuple[list[refs.Ref], list[refs.Ref], list[refs.Ref]]:
    """参考帧的分槽:(第一遍, 可进第二遍的, 仍放不下的)。超过 3 张才有第二遍(两遍合成)。"""
    chars, scene, props = assets["characters"], assets["scene"], assets["props"]
    spoken = rules.dialogue_by_speaker(seg["description"], [c["name"] for c in chars])
    weighted = [(refs.Ref("character", c["name"], c["turnaround_path"]),
                 sum(n for who, n in spoken.items() if who.startswith(c["name"]))) for c in chars]
    scene_ref = refs.Ref("scene", scene["name"], scene["image_path"]) if scene is not None else None
    prop_refs = [refs.Ref("prop", p["name"], p["image_path"]) for p in props]
    return refs.allocate_passes(weighted, scene_ref, prop_refs, QWEN_IMAGE_EDIT_2511.max_ref_images)


def _bound_names(assets) -> list[str]:
    return ([c["name"] for c in assets["characters"]] + ([assets["scene"]["name"]] if assets["scene"] is not None else [])
            + [p["name"] for p in assets["props"]])


def _passes(seg, assets, text: str) -> tuple[list[refs.Ref], str, list[str], list[refs.Ref], list[refs.Ref]]:
    """按图提示词 text 定两遍:(第一遍参考图, 第一遍提示词, 拿出来的分句, 第二遍加入的, 只能写成名字的)。
    第二遍「留位加人」只收提示词里能整句拿出来的素材(`refs.split_for_add`),其余照旧退化成名字。"""
    kept, later, dropped = _slot_refs(seg, assets)
    first_text, moved, added = refs.split_for_add(text, later, _bound_names(assets))
    order = refs.find_mentions(first_text, _bound_names(assets))[0]
    kept = sorted(kept, key=lambda r: order.index(r.name) if r.name in order else len(order))   # 先写的人当 Picture 1
    return kept, first_text, moved, added, [r for r in later if r not in added] + dropped


def frame_passes(store: Store, segment_id: int) -> int:
    """这段的参考帧要合成几遍(1 或 2)—— 估时用,按首帧图提示词判(尾帧 / 关键帧多半同一批人)。"""
    seg = repo.get(store, "segment", segment_id)
    return 2 if _passes(seg, repo.segment_assets(store, segment_id), seg["image_prompt"])[3] else 1


# 参考帧字段 → 用它的参考模式、它的提示词字段、界面上的叫法。
# 中间关键帧不在这里:它叠加在任何参考模式上,一段可以有几张,存在 segment_keyframe 表里(见 key_frame_problems)
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


# 纯文字(只给四视图、不给首帧):不做(用户 2026-10-15 定),原因写给人看
TEXT_ONLY_REFUSED = ("纯文字模式不支持出视频:实测场景和道具守不住(黄昏院子 → 白天砖墙,大块雕花绿玉 → 小块浅色坠子;"
                     "spike-results/2026-10-14-开卡调研批次-运镜关键帧步数.md)")


def frame_text(store: Store, segment_id: int, field: str) -> str:
    """参考帧实际用的图提示词。首帧在人 / LLM 写的那句后面拼上场记表(姿态、朝向、站位、手持,由代码写 ——
    spec 2026-10-22-连续性保障-design.md 第 4 项),竖屏躺姿近景再拼一句景别(spec 2026-10-23-不穿帮验收-design.md 第 2 节);
    场记表只描述段落开头,尾帧不拼。"""
    seg = repo.get(store, "segment", segment_id)
    text = seg[FRAMES[field][1]]
    if field != "first_frame_path" or not text.strip():
        return text
    entries = blocking.parse(seg["blocking_json"])
    close = framing.phrase(_aspect(store, segment_id), entries, seg["shot_type"]) if entries else ""
    # 景别 + 机位也由代码拼进首帧(分镜方案挑的镜头要真到画面里);躺姿那句已经说了景别(近景 / 特写),就只补机位
    shot = framing.shot_phrase("" if close else seg["shot_type"], seg["angle"])
    tail = "。".join(x for x in (blocking.phrase(entries) if entries else "", close, shot) if x)
    return f"{text.rstrip('。;; ')}。{tail}" if tail else text


def _frame_problems(store: Store, segment_id: int, field: str) -> list[str]:
    modes, prompt_field, label = FRAMES[field]
    seg = repo.get(store, "segment", segment_id)
    if seg["reference_mode"] not in modes:
        return [f"参考模式 {seg['reference_mode']} 不用{label}"]
    problems = []
    if field == "first_frame_path" and seg["blocking_json"].strip():
        a = repo.segment_assets(store, segment_id)
        problems += [f"场记表与本段绑定对不上:{p}(到段落编辑页改场记表)" for p in blocking.problems(
            blocking.parse(seg["blocking_json"]), [c["name"] for c in a["characters"]], [p["name"] for p in a["props"]])]
        problems += framing.problems(_aspect(store, segment_id), blocking.parse(seg["blocking_json"]), seg["shot_type"])
    return problems or _image_problems(store, segment_id, frame_text(store, segment_id, field), label)


def _image_problems(store: Store, segment_id: int, text: str, label: str) -> list[str]:
    """按 text 这条图提示词合成一张参考帧还差什么(素材、@引用)。首帧 / 尾帧 / 关键帧共用。"""
    assets = repo.segment_assets(store, segment_id)
    chars, scene, props = assets["characters"], assets["scene"], assets["props"]

    problems = []
    if not text.strip():
        problems.append(f"缺少{label}图提示词")
    if scene is None:
        problems.append("未绑定场景")
    problems += [f"角色「{c['name']}」缺正面定妆图" for c in chars if not c["turnaround_path"]]
    if scene is not None and not scene["image_path"]:
        problems.append(f"场景「{scene['name']}」缺空镜图")
    problems += [f"道具「{p['name']}」缺白底图" for p in props if not p["image_path"]]
    bound = [c["name"] for c in chars] + ([scene["name"]] if scene is not None else []) + [p["name"] for p in props]
    found, unknown = refs.find_mentions(text, bound)
    problems += [f"@{u} 不是本镜绑定的素材" for u in unknown]
    if len(bound) >= 2:
        problems += [f"绑定了「{n}」但{label}图提示词从未 @ 它" for n in bound if n not in found]
    return problems


def _plan_frame(store: Store, segment_id: int, field: str, seed: int) -> int:
    problems = _frame_problems(store, segment_id, field)
    if problems:
        raise ValueError(";".join(problems))
    return _plan_image(store, segment_id, frame_text(store, segment_id, field), seed, {"writes": field})


def _plan_image(store: Store, segment_id: int, text: str, seed: int, writes: dict) -> int:
    """按图提示词 text 合成一张参考帧(内容已核过)。writes:写回到哪(writes,关键帧另带 keyframe_id)。"""
    seg = repo.get(store, "segment", segment_id)
    assets = repo.segment_assets(store, segment_id)
    kept, first_text, moved, added, dropped = _passes(seg, assets, text)
    prompt = tags.expand(first_text, kept, added + dropped, "qwen")
    params = {
        "workflow": "qwen_image", "preset": "fast", "size": list(FRAME_SIZE[_aspect(store, segment_id)]),
        "image_refs": [r.path for r in kept], **writes, "expect_ext": ".png",
        "dropped": [r.name for r in dropped],   # 退化成文字的素材,溯源里要看得到(就绪清单提示用局部替换补)
        "frame": {"text": text},                # 生成时的图提示词拼句:镜头改过时比对用;改参重跑沿用(serve/rerun.py)
    }
    if added:                                   # 第一遍留位、第二遍加入(spike-results/2026-10-20-多人同框留位加人实测.md)
        params["second_pass"] = {"image_refs": [r.path for r in added], "names": [r.name for r in added],
                                 "prompt": compose.second_pass_prompt(moved, added)}
    params = presets.stamp(store, "frame", params)      # 绑定当前「首帧 / 尾帧」模板预设(没有 = 内置)
    return create_task(store, kind="image", target_type="segment", target_id=segment_id, model=QWEN_MODEL,
                       params=params, seed=seed, prompt=prompt, refs=params["image_refs"] + [r.path for r in added])


def first_frame_problems(store: Store, segment_id: int) -> list[str]:
    """出首帧还差什么(只看内容,不建任务)。空 = 可以规划。增量补全靠它判「不能做」。"""
    return _frame_problems(store, segment_id, "first_frame_path")


def last_frame_problems(store: Store, segment_id: int) -> list[str]:
    return _frame_problems(store, segment_id, "last_frame_path")


def key_frame_problems(store: Store, keyframe_id: int) -> list[str]:
    """出这张关键帧的图还差什么。关键帧不看参考模式(叠加在任何模式上)。"""
    k = repo.get(store, "segment_keyframe", keyframe_id)
    return _image_problems(store, k["segment_id"], k["image_prompt"], "关键帧")


def plan_first_frame(store: Store, segment_id: int, *, seed: int) -> int:
    return _plan_frame(store, segment_id, "first_frame_path", seed)


def plan_last_frame(store: Store, segment_id: int, *, seed: int) -> int:
    """尾帧与首帧同一套合成(同样的素材槽位、同样的快档),只是用尾帧图提示词、写回尾帧。"""
    return _plan_frame(store, segment_id, "last_frame_path", seed)


def plan_key_frame(store: Store, keyframe_id: int, *, seed: int) -> int:
    """中间关键帧图:与首帧同一套合成,用这张关键帧的图提示词。任务挂在段落上,参数里记写回哪一张(tools/drama/targets.py)。"""
    problems = key_frame_problems(store, keyframe_id)
    if problems:
        raise ValueError(";".join(problems))
    k = repo.get(store, "segment_keyframe", keyframe_id)
    return _plan_image(store, k["segment_id"], k["image_prompt"], seed,
                       {"writes": KEY_WRITES, "keyframe_id": keyframe_id})


REGION_KINDS = {"character": ("character", "turnaround_path"), "scene": ("scene", "image_path"),
                "prop": ("prop", "image_path")}


def _frame_slot(store: Store, segment_id: int, slot: str) -> tuple[str | None, dict]:
    """局部替换的写回位置:slot = first_frame_path / last_frame_path / key:<关键帧号> → (当前图, writes 参数)。"""
    if slot in FRAMES:
        return repo.get(store, "segment", segment_id)[slot], {"writes": slot}
    kid = int(slot.removeprefix("key:"))
    k = repo.get(store, "segment_keyframe", kid)
    if k["segment_id"] != segment_id:
        raise ValueError(f"关键帧 {kid} 不属于段落 {segment_id}")
    return k["frame_path"], {"writes": KEY_WRITES, "keyframe_id": kid}


def plan_region(store: Store, segment_id: int, slot: str, *, source: str, size: tuple[int, int], box: list[int],
                asset: tuple[str, int], note: str, seed: int) -> int:
    """框选局部替换:当前帧 source 里框住的一块,按 asset 的参考图改,贴回原图。新版本进历史,人挑了才替换当前帧。
    size 是 source 的真实像素(面板读的);框是在旧图上画的(当前帧已换)就拒绝。"""
    current, writes = _frame_slot(store, segment_id, slot)
    if not current:
        raise ValueError("这一格还没有图,先出图再做局部替换")
    if source != current:
        raise ValueError("框是在旧图上画的:这一格的图已经换了,刷新后重画")
    kind, row_id = asset
    table, field = REGION_KINDS[kind]
    assets = repo.segment_assets(store, segment_id)
    bound = {"character": [c["id"] for c in assets["characters"]], "prop": [p["id"] for p in assets["props"]],
             "scene": [assets["scene"]["id"]] if assets["scene"] is not None else []}[kind]
    row = repo.get(store, table, row_id)
    if row_id not in bound:
        raise ValueError(f"「{row['name']}」不是本段绑定的素材")
    if not row[field]:
        raise ValueError(f"「{row['name']}」还没有参考图")
    ref = refs.Ref(kind, row["name"], row[field])
    prompt = compose.region_prompt(ref, note, row["styling"] if kind == "character" else "")
    problems = tags.check_tags(prompt, 2, dialect="qwen")
    if problems:                                # 补充说明里写了 Picture N 之类,规划时就拒(不等到云机)
        raise ValueError(";".join(problems))
    params = {"workflow": "qwen_region", "preset": "fast", "source": source, "ref": ref.path, "ref_name": ref.name,
              "box": list(compose.region_box(box, size)), "size": list(size), "note": note, **writes,
              "image_refs": [], "expect_ext": ".png"}
    return create_task(store, kind="image", target_type="segment", target_id=segment_id, model=QWEN_MODEL,
                       params=params, seed=seed, prompt=prompt, refs=[source, ref.path])


def fourview_refs(store: Store, segment_id: int) -> tuple[list[refs.Ref], list[refs.Ref]]:
    """进 H3 参考图的角色四视图(台词多的优先),与因槽位不够没进去的。
    工作流有 3 个图槽,<Picture 1> 是参考帧,四视图最多 2 张。"""
    seg = repo.get(store, "segment", segment_id)
    chars = repo.segment_assets(store, segment_id)["characters"]
    spoken = rules.dialogue_by_speaker(seg["description"], [c["name"] for c in chars])
    weighted = [(refs.Ref("character", c["name"], c["fourview_path"]),
                 sum(n for who, n in spoken.items() if who.startswith(c["name"])))
                for c in chars if c["fourview_path"]]
    return refs.allocate_slots(weighted, None, [], H3_IMAGE_SLOTS - 1)


def video_problems(store: Store, segment_id: int) -> list[str]:
    """出视频还差什么(只看内容;云机与模型是提交时的事)。空 = 可以规划。"""
    checks = segment_readiness(store, segment_id, video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511,
                               host_ok=True, host_msg="提交时检查", model_ok=True, model_msg="提交时检查")
    blocking = [c.msg for c in checks if not c.ok and c.key not in RUNTIME_KEYS]
    if blocking:
        return blocking
    seg = repo.get(store, "segment", segment_id)
    if seg["reference_mode"] == "text_only":
        return [TEXT_ONLY_REFUSED]
    if seg["reference_mode"] not in VIDEO_MODES:
        return [f"参考模式 {seg['reference_mode']} 的视频生成尚未实现(已实现:{'、'.join(VIDEO_MODES)})"]
    return []


def plan_video(store: Store, segment_id: int, *, seed: int, steps: int | None = None) -> int:
    """steps:None = 定稿(内置 20 步,或当前视频预设改过的步数;参数里不写这个键,与以前的任务一致);DRAFT_STEPS = 草稿档。"""
    if steps not in (None, DRAFT_STEPS):
        raise ValueError(f"步数档只有定稿和草稿({DRAFT_STEPS} 步),给的是 {steps}")
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
    # 说话人按本段角色名认:「说」前常连着动作描写,原样写进来会被 H3 烧成画面字幕(实战《替嫁冲喜》第 6 段)
    names = [c["name"] for c in assets["characters"]]
    lines = "".join(f"{who}用普通话说:「{line}」" for who, line in rules.dialogue_lines(seg["description"], names))
    voices = "".join(f"{c['name']}的声音:{c['voice_desc']}。" for c in assets["characters"] if c["voice_desc"])
    who = "".join(f"<Picture {k}> 是{r.name}的四视图(正面、左侧、背面、右侧)。" for k, r in enumerate(views, 2))
    move = framing.move_phrase(seg["movement"])      # 运镜由代码写,没填不拼(老段落提示词不变)
    prompt = f"<Picture 1> {intro}" + who + body + "".join(f" {part}" for part in (lines, voices, move) if part)

    image_refs = [seg[ref_field]] + [r.path for r in views]
    anchors = [[seg[f], idx] for f, idx in pins]
    for k in repo.keyframes(store, segment_id):   # 关键帧:模式的锚点之后按时刻先后逐张钉(readiness 已核过范围、间距与图)
        anchors.append([k["frame_path"], key_frame_index(k["time_sec"], MINIMAX_H3)])
    params = {
        "workflow": "minimax_h3", "frames": MINIMAX_H3.frames_for(seg["duration_sec"]),
        "size": list(VIDEO_SIZE[_aspect(store, segment_id)]),
        "image_refs": image_refs, "anchors": anchors, "audio_ref": None,
        "writes": "video_path", "expect_ext": ".mp4", **({"steps": steps} if steps else {}),
        # 台本快照(用户 2026-10-23):这条视频生成时的描述、逐句台词、声音、提示词、时长 —— 任务参数不可改,就是记录;
        # 挑历史版本时看得到每一版说的是什么,台词改过也比得出来(rules.stale_lines)
        "script": {"description": seg["description"],
                   "lines": [[w, line] for w, line in rules.dialogue_lines(seg["description"], names)],
                   "voices": {c["name"]: c["voice_desc"] for c in assets["characters"] if c["voice_desc"]},
                   "video_prompt": seg["video_prompt"], "duration_sec": seg["duration_sec"]},
    }
    params = presets.stamp(store, "video", params)
    try:            # 规划时就试拼一次(内置拼法叠上当前预设):拼不出就拒绝规划,不等到交给云机时才失败
        presets.apply_params(build_h3(prompt, image_refs, None, params["frames"], seed, size=tuple(params["size"]),
                                      anchors=[tuple(a) for a in anchors]), params)
    except ValueError as e:
        which = f"当前视频模板预设 v{params['preset_version']} " if "preset_diff" in params else "内置视频模板"
        raise ValueError(f"{which}拼不出这一段的工作流({len(anchors)} 个锚点):{e}"
                         + (" —— 到「工作流」面板换一版或回到内置" if "preset_diff" in params else "")) from e
    return create_task(store, kind="video", target_type="segment", target_id=segment_id, model=H3_MODEL,
                       params=params, seed=seed, prompt=prompt,
                       refs=list(dict.fromkeys([*image_refs, *(p for p, _ in anchors)])))
