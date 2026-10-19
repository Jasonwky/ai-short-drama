# -*- coding: utf-8 -*-
"""失效传播:改了什么 → 哪些已生成的产物作废 → 重跑要多久(设计 §6.5)。

两态(CLAUDE.md「plan/confirm」):`impact()` 只算不改,给人看「这会重跑 9 条视频,约 2.2 小时」;
人确认后才 `apply()`。**不许默默全量重跑** —— 单条 8 秒片约 15 分钟,代价不允许。

`apply()` 只清掉段落上的产物指针,不删文件:每次生成都在 task 表里有不可变记录,旧版本可以挑回来。
"""
from __future__ import annotations

from dataclasses import dataclass

from tools.drama import repo
from tools.drama.db import Store
from tools.gen.capability import Capability

# 事件 → 受影响段落的查询。参数是事件目标的 id。
_AFFECTED = {
    "script": "SELECT id FROM segment WHERE episode_id = ?",
    "style": "SELECT s.id FROM segment s JOIN episode e ON e.id = s.episode_id WHERE e.drama_id = ?",
    "character": "SELECT segment_id FROM segment_character WHERE character_id = ?",
    "scene": "SELECT id FROM segment WHERE scene_id = ?",
    "prop": "SELECT segment_id FROM segment_prop WHERE prop_id = ?",
    "layout": "SELECT id FROM segment WHERE id = ?",
    "segment_prompt": "SELECT id FROM segment WHERE id = ?",
    # 声音描述逐字进视频提示词(闸门 0-4:音色靠文字描述维持),改了只影响视频,参考帧不受影响
    "voice": "SELECT segment_id FROM segment_character WHERE character_id = ?",
    # 四视图只进视频的参考图(首帧合成用正面图),换它只影响视频
    "fourview": "SELECT segment_id FROM segment_character WHERE character_id = ?",
    # 只改了视频提示词 / 时长:参考帧是按图提示词和绑定出的,不受影响
    "segment_video": "SELECT id FROM segment WHERE id = ?",
}
_VIDEO_ONLY = {"voice", "fourview", "segment_video"}
_FRAME_FIELDS = ("first_frame_path", "last_frame_path")          # 关键帧图在 segment_keyframe 表里,另算


@dataclass(frozen=True)
class Impact:
    segment_ids: tuple[int, ...]
    frames: int          # 要重出的参考帧张数(只算已经出过的)
    videos: int          # 要重跑的视频条数(只算已经出过的)
    est_seconds: float | None    # None = 无法估时(原因在 est_note)
    videos_only: bool = False    # True:只作废视频,参考帧保留
    est_note: str = ""           # 「按 X 估算」或无法估时的原因(gen/estimate.py)

    def summary(self) -> str:
        if self.videos == 0 and self.frames == 0:
            return "不影响任何已生成的产物"
        est = self.est_note if self.est_seconds is None else f"约 {self.est_seconds / 3600:.1f} 小时({self.est_note})"
        return f"这会重跑 {self.videos} 条视频、{self.frames} 张参考帧,{est}"


def impact(store: Store, event: str, target_id: int, *, video_cap: Capability,
           image_cap: Capability) -> Impact:
    if event not in _AFFECTED:
        raise ValueError(f"未知的变更事件 {event},可选:{sorted(_AFFECTED)}")
    ids = sorted({r[0] for r in store.conn.execute(_AFFECTED[event], (target_id,))})
    frames = videos = 0
    est = 0.0
    from tools.gen import estimate               # 延迟导入(gen 依赖 drama)
    from tools.gen.presets import video_steps    # 当前视频预设可能改了步数,重跑代价按它算
    steps, b = video_steps(store), estimate.basis(store)
    for sid in ids:
        seg = repo.get(store, "segment", sid)
        if seg["video_path"]:
            if seg["duration_sec"] is None:
                raise ValueError(f"段落 {sid} 有视频却没有时长 —— 数据不一致,不能估算")
            videos += 1
            est += b.video(video_cap, video_cap.frames_for(seg["duration_sec"]), steps) or 0.0
        if event in _VIDEO_ONLY:
            continue
        n = sum(1 for f in _FRAME_FIELDS if seg[f]) + sum(1 for k in repo.keyframes(store, sid) if k["frame_path"])
        frames += n
        from tools.gen.planner import frame_passes     # 延迟导入(gen 依赖 drama);两遍合成按两张图算
        est += n * (b.item(image_cap) or 0.0) * frame_passes(store, sid)
    return Impact(tuple(ids), frames, videos, None if b.timing is None else est, event in _VIDEO_ONLY, b.note)


def one_video(store: Store, seg, video_cap: Capability) -> Impact:
    """只作废这一段的视频(换参考帧 / 抽尾帧接下一段)。"""
    from tools.gen import estimate
    from tools.gen.presets import video_steps
    b = estimate.basis(store)
    return Impact((seg["id"],), 0, 1, b.video(video_cap, video_cap.frames_for(seg["duration_sec"]), video_steps(store)),
                  est_note=b.note)


def keyframe_edit(store: Store, seg, *, video_cap: Capability, image_cap: Capability, images: int) -> Impact:
    """只改了关键帧(加 / 删 / 改时刻 / 改提示词):作废本段视频;images = 改了提示词、已经有图的那几张(图也作废,调用方清)。
    首帧 / 尾帧不动 —— 否则第一次给已有首帧的段落加关键帧,首帧会被冲掉。"""
    from tools.gen import estimate
    from tools.gen.presets import video_steps
    b = estimate.basis(store)
    frames = images
    videos = 1 if seg["video_path"] else 0
    est = 0.0
    if videos:
        est += b.video(video_cap, video_cap.frames_for(seg["duration_sec"]), video_steps(store)) or 0.0
    est += frames * (b.item(image_cap) or 0.0)
    ids = (seg["id"],) if frames or videos else ()
    return Impact(ids, frames, videos, None if b.timing is None else est, True, b.note)


def apply(store: Store, imp: Impact) -> None:
    """人确认后才调用:清掉受影响段落的产物指针。"""
    with store.write() as c:
        if imp.videos_only:
            c.executemany("UPDATE segment SET video_path = NULL, subtitle_path = NULL WHERE id = ?",
                          [(i,) for i in imp.segment_ids])
            return
        c.executemany("UPDATE segment SET first_frame_path = NULL, last_frame_path = NULL,"
                      " video_path = NULL, subtitle_path = NULL WHERE id = ?",
                      [(i,) for i in imp.segment_ids])
        c.executemany("UPDATE segment_keyframe SET frame_path = NULL WHERE segment_id = ?", [(i,) for i in imp.segment_ids])
