# -*- coding: utf-8 -*-
"""视频面板的台词音频(spec 2026-10-30 第 4 节):没定稿音色时按钮置灰并说明;有定稿时排一条 tts;有音频时逐句命中与过期提示。"""
import json
import re

from tools.drama import repo
from tools.drama.tasks import set_task_state
from tools.gen.tts import plan_tts


def _approved_voice(store, root, cid):
    (root / "v").mkdir(exist_ok=True)
    (root / "v/lin.wav").write_bytes(b"RIFF")
    repo.update(store, "character", cid, voice_ref_path="v/lin.wav", voice_ref_text="试听句")
    store.conn.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                       " VALUES ('character', ?, 'voice', 'v/lin.wav', 'x', 't')", (cid,))


def _card(page, seg_id):
    return page.split(f'id="seg-{seg_id}"')[1].split("</section>")[0]


def test_without_an_approved_voice_the_button_is_disabled_and_says_why(client, store, sample):
    card = _card(client.get(f"/episode/{sample['episode']}/videos").text, sample["seg"])
    assert "林小雨" in card and "还没有定稿的音色" in card
    assert re.search(r"data-synthesize\s+disabled", card)


def test_synthesize_plans_one_tts_task(client, runtime, store, sample):
    _approved_voice(store, runtime.data_root, sample["lin"])
    r = client.post(f"/segment/{sample['seg']}/tts", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"#seg-{sample['seg']}")
    assert store.conn.execute("SELECT COUNT(*) FROM task WHERE kind = 'tts'").fetchone()[0] == 1
    assert "data-line-flying" in _card(client.get(f"/episode/{sample['episode']}/videos").text, sample["seg"])


def test_current_audio_shows_per_line_hits_and_goes_stale_when_lines_change(client, runtime, store, sample):
    _approved_voice(store, runtime.data_root, sample["lin"])
    tid = plan_tts(store, sample["seg"], seed=1)
    rel = f"outputs/tasks/{tid}/lines.wav"
    f = runtime.data_root / rel
    f.parent.mkdir(parents=True)
    f.write_bytes(b"RIFF")
    f.with_name("tts.json").write_text(json.dumps({
        "lines": [["林小雨", "这块玉佩，是我母亲留给我的。"]], "hits": [1.0], "ok": True, "attempt": 1, "sec": 3.2,
        "spans": [[0.3, 2.7]], "heard": "这块玉佩是我母亲留给我的", "attempts": []}, ensure_ascii=False), encoding="utf-8")
    set_task_state(store, tid, "queued", external_id="x")
    set_task_state(store, tid, "done", result_path=rel)
    repo.update(store, "segment", sample["seg"], line_audio_path=rel)
    card = _card(client.get(f"/episode/{sample['episode']}/videos").text, sample["seg"])
    assert f"/media/{rel}" in card and "命中 1.00" in card and "data-line-stale" not in card
    repo.update(store, "segment", sample["seg"], description="林小雨说：「我不要它了。」")
    card = _card(client.get(f"/episode/{sample['episode']}/videos").text, sample["seg"])
    assert "data-line-stale" in card and "台词改过了" in card


def test_video_card_says_when_the_video_was_pinned_to_another_line_audio(client, store, sample):
    from tests.drama.conftest import give_line_audio
    from tools.gen.planner import plan_video
    give_line_audio(store, sample["seg"])
    vid = plan_video(store, sample["seg"], seed=1)
    set_task_state(store, vid, "queued", external_id="x")
    set_task_state(store, vid, "done", result_path="outputs/tasks/v/h3.mp4")
    repo.update(store, "segment", sample["seg"], video_path="outputs/tasks/v/h3.mp4")
    assert "data-audio-mismatch" not in _card(client.get(f"/episode/{sample['episode']}/videos").text, sample["seg"])
    give_line_audio(store, sample["seg"], sec=6.0)          # 重合成了一条台词音频
    card = _card(client.get(f"/episode/{sample['episode']}/videos").text, sample["seg"])
    assert "data-audio-mismatch" in card and "不是段落现在的台词音频" in card
