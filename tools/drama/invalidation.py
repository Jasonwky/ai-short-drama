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
}
_VIDEO_ONLY = {"voice", "fourview"}
_FRAME_FIELDS = ("first_frame_path", "last_frame_path")


@dataclass(frozen=True)
class Impact:
    segment_ids: tuple[int, ...]
    frames: int          # 要重出的参考帧张数(只算已经出过的)
    videos: int          # 要重跑的视频条数(只算已经出过的)
    est_seconds: float
    videos_only: bool = False    # True:只作废视频,参考帧保留

    def summary(self) -> str:
        if self.videos == 0 and self.frames == 0:
            return "不影响任何已生成的产物"
        return (f"这会重跑 {self.videos} 条视频、{self.frames} 张参考帧,"
                f"约 {self.est_seconds / 3600:.1f} 小时(估算)")


def impact(store: Store, event: str, target_id: int, *, video_cap: Capability,
           image_cap: Capability) -> Impact:
    if event not in _AFFECTED:
        raise ValueError(f"未知的变更事件 {event},可选:{sorted(_AFFECTED)}")
    ids = sorted({r[0] for r in store.conn.execute(_AFFECTED[event], (target_id,))})
    frames = videos = 0
    est = 0.0
    from tools.gen.presets import video_steps    # 延迟导入:当前视频预设可能改了步数,重跑代价按它算
    steps = video_steps(store)
    for sid in ids:
        seg = repo.get(store, "segment", sid)
        if seg["video_path"]:
            if seg["duration_sec"] is None:
                raise ValueError(f"段落 {sid} 有视频却没有时长 —— 数据不一致,不能估算")
            videos += 1
            est += video_cap.est_seconds(video_cap.frames_for(seg["duration_sec"]), steps)
        if event in _VIDEO_ONLY:
            continue
        n = sum(1 for f in _FRAME_FIELDS if seg[f])
        frames += n
        est += n * image_cap.sec_per_item
    return Impact(tuple(ids), frames, videos, est, event in _VIDEO_ONLY)


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
