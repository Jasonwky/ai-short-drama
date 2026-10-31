# -*- coding: utf-8 -*-
import pytest

from tools.drama import repo
from tools.drama.db import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def approve_all(store):
    """样例里已有的资产图都算定稿过(spec 2026-10-28 第 6.1 节;按路径认,sha 记个占位)。测试换了图要重新定稿。"""
    for table, slot, col in (("character", "image", "turnaround_path"), ("character", "fourview", "fourview_path"),
                             ("scene", "image", "image_path"), ("prop", "image", "image_path")):
        for r in store.conn.execute(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL").fetchall():
            if not store.conn.execute("SELECT 1 FROM asset_approval WHERE table_name = ? AND row_id = ? AND slot = ?"
                                      " AND path = ?", (table, r[0], slot, r[1])).fetchone():
                store.conn.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                                   " VALUES (?, ?, ?, ?, 'test', 't')", (table, r[0], slot, r[1]))


def give_voice_template(store, character_id, root=None, name="喝令"):
    """给角色一个默认念法模板(IndexTTS2 港片念法,spec 2026-10-31);给 root 就把模板文件也放好(要真跑 ttser 的测试)。"""
    path = f"v/tpl_{character_id}.wav"
    if root is not None:
        (root / "v").mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"RIFF")
    drama = repo.get(store, "character", character_id)["drama_id"]
    t = repo.add_voice_template(store, drama, name=name, path=path, sha256="test", source="synth:0", note="")
    repo.update(store, "character", character_id, voice_template_id=t)
    return t


def give_line_audio(store, seg, sec=8.0):
    """段落里说话的角色都有定稿音色,段落有一条已合成、记了时长的台词音频(spec 2026-10-30 第 4 节;视频规划要它)。
    时长默认等于样例段落的 8 秒,原有的帧数断言不变。测试改了台词要重新给。"""
    from tools.drama.tasks import set_task_state
    from tools.gen import tts
    for c in repo.segment_assets(store, seg)["characters"]:
        path = f"audio/voice_{c['id']}.wav"
        if c["voice_ref_path"] != path:
            repo.update(store, "character", c["id"], voice_ref_path=path, voice_ref_text="试听句")
            store.conn.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                               " VALUES ('character', ?, 'voice', ?, 'test', 't')", (c["id"], path))
        if c["voice_template_id"] is None:      # 念法模板(IndexTTS2 港片配音,spec 2026-10-31):每个说话人一个默认
            t = repo.add_voice_template(store, c["drama_id"], name=f"模板{c['id']}", path=f"audio/tpl_{c['id']}.wav",
                                        sha256="test", source="synth:0", note="")
            repo.update(store, "character", c["id"], voice_template_id=t)
    if not tts._lines(store, seg):
        return
    tid = tts.plan_tts(store, seg, seed=1)
    set_task_state(store, tid, "queued", external_id="x")
    set_task_state(store, tid, "done", result_path=f"audio/lines_{seg}.wav", result_sha256=f"sha-lines-{tid}", result_sec=sec)
    repo.update(store, "segment", seg, line_audio_path=f"audio/lines_{seg}.wav")


@pytest.fixture
def sample(store):
    """一部剧、一集、两个角色、一个场景、一个道具、一个已出过片的段落。

    段落只绑了林小雨和玉佩;沈墨存在但没绑 —— 用来测「只影响绑定了的段落」。
    """
    d = repo.create_drama(store, "玉佩", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "script", script="字" * 1000)
    lin = repo.add_character(store, d, "林小雨", turnaround_path="assets/lin.png", fourview_path="assets/lin_4v.png",
                             voice_desc="清亮女声")
    shen = repo.add_character(store, d, "沈墨", turnaround_path="assets/shen.png")
    yard = repo.add_scene(store, d, "老宅庭院", image_path="assets/yard.png")
    jade = repo.add_prop(store, d, "玉佩", image_path="assets/jade.png")
    seg = repo.add_segment(
        store, ep, 1, scene_id=yard, duration_sec=8.0, reference_mode="first",
        first_frame_path="frames/s1.png", video_path="videos/s1.mp4",
        description="【镜头1】林小雨低头看着玉佩。【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」",
        image_prompt="@林小雨 站在 @老宅庭院 里,手里拿着 @玉佩",
        video_prompt="@林小雨 在 @老宅庭院 低头看 @玉佩,然后抬眼说话")
    repo.bind_characters(store, seg, [lin])
    repo.bind_props(store, seg, [jade])
    approve_all(store)
    return {"drama": d, "episode": ep, "lin": lin, "shen": shen, "yard": yard, "jade": jade, "seg": seg}
