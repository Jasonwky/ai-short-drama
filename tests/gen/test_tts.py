# -*- coding: utf-8 -*-
"""段落台词音频的规划:说话人要有定稿音色与念法模板;参数定死台词、音色、逐句模板与配方快照;任一变了算过期。
引擎:IndexTTS2 港片念法(spec 2026-10-31-IndexTTS2港片配音-design.md);旧引擎 qwen3_tts_clone 的音频一律过期。"""
import json

import pytest

from tools.drama import repo
from tools.drama.tasks import set_task_state
from tools.gen import tts

TWO = "【镜头1】林小雨说：「这块玉佩，是我母亲留给我的。」沈墨说：「我知道。」"


def _voice(store, cid, path, text="试听句"):
    repo.update(store, "character", cid, voice_ref_path=path, voice_ref_text=text)
    with store.write() as c:
        c.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                  " VALUES ('character', ?, 'voice', ?, ?, 't')", (cid, path, f"sha-{path}"))


def _template(store, drama, name="喝令"):
    return repo.add_voice_template(store, drama, name=name, path=f"voice_templates/{name}.wav", sha256=f"sha-{name}",
                                   source="synth:1", note="")


@pytest.fixture
def two(store, sample):
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"]])
    repo.update(store, "segment", sample["seg"], description=TWO)
    _voice(store, sample["lin"], "v/lin.wav", "林的试听句")
    _voice(store, sample["shen"], "v/shen.wav", "沈的试听句")
    t = _template(store, sample["drama"])
    for c in ("lin", "shen"):
        repo.update(store, "character", sample[c], voice_template_id=t)
    return {**sample, "tpl": t}


def test_plan_freezes_lines_and_approved_voices(store, two):
    tid = tts.plan_tts(store, two["seg"], seed=9)
    t = repo.get(store, "task", tid)
    p = json.loads(t["params_json"])
    assert (t["kind"], t["target_type"], t["seed"]) == ("tts", "segment", 9)
    assert p["lines"] == [["林小雨", "这块玉佩，是我母亲留给我的。"], ["沈墨", "我知道。"]]
    assert p["voices"]["沈墨"] == {"path": "v/shen.wav", "sha256": "sha-v/shen.wav"}          # IndexTTS2 不要参考文本(V3)
    assert p["writes"] == "line_audio_path" and p["expect_ext"] == ".wav"
    assert (p["workflow"], p["engine"], t["model"]) == ("indextts2_hk", "indextts2_hk", "IndexTTS-2")
    assert [x["name"] for x in p["line_templates"]] == ["喝令", "喝令"]
    assert p["recipe"] == {"emo_alpha": 1.0, "pitch_widen": 1.4, "squeeze_gap_sec": 0.06, "squeeze_db": -38.0}
    assert (p["gap_sec"], p["sr_out"]) == (0.15, 24000)
    assert json.loads(t["refs_json"]) == ["v/lin.wav", "v/shen.wav", "voice_templates/喝令.wav", "voice_templates/喝令.wav"]


def test_speaker_without_an_approved_voice_is_named(store, two):
    store.conn.execute("DELETE FROM asset_approval WHERE row_id = ? AND slot = 'voice'", (two["shen"],))
    assert any("沈墨" in p and "定稿" in p for p in tts.tts_problems(store, two["seg"]))
    with pytest.raises(ValueError, match="沈墨"):
        tts.plan_tts(store, two["seg"], seed=1)


def test_unknown_speaker_and_silent_segment_are_refused(store, two):
    repo.update(store, "segment", two["seg"], description="路人说：「让一让。」")
    assert any("路人" in p for p in tts.tts_problems(store, two["seg"]))
    repo.update(store, "segment", two["seg"], description="林小雨低头不语。")
    assert any("没有台词" in p for p in tts.tts_problems(store, two["seg"]))


def _done(store, tid, path):
    set_task_state(store, tid, "queued", external_id="x")
    set_task_state(store, tid, "done", result_path=path)
    repo.update(store, "segment", repo.get(store, "task", tid)["target_id"], line_audio_path=path)


def test_stale_when_lines_or_voices_change(store, two):
    _done(store, tts.plan_tts(store, two["seg"], seed=1), "outputs/tasks/50/lines.wav")
    seg = lambda: repo.get(store, "segment", two["seg"])   # noqa: E731
    assert tts.stale(store, seg()) == []
    repo.update(store, "segment", two["seg"], description=TWO.replace("我知道。", "我早就知道。"))
    assert any("台词" in r for r in tts.stale(store, seg()))
    repo.update(store, "segment", two["seg"], description=TWO)
    _voice(store, two["shen"], "v/shen2.wav")
    assert any("沈墨" in r and "音色" in r for r in tts.stale(store, seg()))


def test_audio_not_made_by_a_tts_task_is_stale(store, two):
    repo.update(store, "segment", two["seg"], line_audio_path="up/hand.wav")
    assert tts.stale(store, repo.get(store, "segment", two["seg"])) == ["当前台词音频不是由台词音频任务生成的,重新合成"]
    repo.update(store, "segment", two["seg"], line_audio_path=None)
    assert tts.stale(store, repo.get(store, "segment", two["seg"])) == []


def _done_sec(store, tid, path, sec):
    set_task_state(store, tid, "queued", external_id="x")
    set_task_state(store, tid, "done", result_path=path, result_sha256="sha-lines", result_sec=sec)
    repo.update(store, "segment", repo.get(store, "task", tid)["target_id"], line_audio_path=path)


def test_current_audio_and_its_problems(store, two):
    seg = lambda: repo.get(store, "segment", two["seg"])   # noqa: E731
    assert "还没有台词音频" in tts.audio_problems(store, seg())[0]
    _done_sec(store, tts.plan_tts(store, two["seg"], seed=1), "outputs/tasks/60/lines.wav", 6.2)
    assert tts.current_audio(store, seg()) == {"path": "outputs/tasks/60/lines.wav", "sha256": "sha-lines", "sec": 6.2}
    assert tts.audio_problems(store, seg()) == []
    repo.update(store, "segment", two["seg"], description=TWO.replace("我知道。", "我早就知道。"))
    assert tts.current_audio(store, seg()) is None and "过期" in tts.audio_problems(store, seg())[0]
    repo.update(store, "segment", two["seg"], description="林小雨低头不语。")
    assert "没有台词" in tts.audio_problems(store, seg())[0]


def test_audio_longer_than_the_video_limit_is_refused(store, two):
    _done_sec(store, tts.plan_tts(store, two["seg"], seed=1), "outputs/tasks/61/lines.wav", 16.0)
    assert "拆段或删句" in tts.audio_problems(store, repo.get(store, "segment", two["seg"]))[0]


def test_audio_without_a_recorded_length_must_be_redone(store, two):
    _done(store, tts.plan_tts(store, two["seg"], seed=1), "outputs/tasks/62/lines.wav")
    assert "没记时长" in tts.audio_problems(store, repo.get(store, "segment", two["seg"]))[0]


def test_speaker_without_a_template_is_named(store, two):
    repo.update(store, "character", two["shen"], voice_template_id=None)
    assert any("沈墨" in p and "念法模板" in p for p in tts.tts_problems(store, two["seg"]))
    with pytest.raises(ValueError, match="念法模板"):
        tts.plan_tts(store, two["seg"], seed=1)


def test_per_line_template_overrides_the_speaker_default(store, two):
    b = _template(store, two["drama"], "惊疑")
    repo.update(store, "segment", two["seg"], line_templates_json=json.dumps({"2": b}))
    p = json.loads(repo.get(store, "task", tts.plan_tts(store, two["seg"], seed=3))["params_json"])
    assert [x["id"] for x in p["line_templates"]] == [two["tpl"], b]


def test_stale_when_a_line_template_changes(store, two):
    _done(store, tts.plan_tts(store, two["seg"], seed=1), "outputs/tasks/70/lines.wav")
    seg = lambda: repo.get(store, "segment", two["seg"])   # noqa: E731
    assert tts.stale(store, seg()) == []
    b = _template(store, two["drama"], "惊疑")
    repo.update(store, "segment", two["seg"], line_templates_json=json.dumps({"1": b}))
    assert any("第 1 句" in r and "念法模板" in r for r in tts.stale(store, seg()))
    repo.update(store, "segment", two["seg"], line_templates_json="{}")
    assert tts.stale(store, seg()) == []


def test_old_engine_line_audio_is_stale(store, two):
    """旧引擎(Qwen3 克隆)出的台词音频:引擎换了 → 过期(spec §5、V6)。"""
    from tools.drama.tasks import create_task
    tid = create_task(store, kind="tts", target_type="segment", target_id=two["seg"], model="Qwen3-TTS-12Hz-1.7B",
                      params={"workflow": "qwen3_tts_clone", "lines": tts._lines(store, two["seg"]), "voices": {},
                              "writes": "line_audio_path", "expect_ext": ".wav"}, seed=1, prompt="p", refs=[])
    _done(store, tid, "outputs/tasks/71/lines.wav")
    assert any("引擎" in r for r in tts.stale(store, repo.get(store, "segment", two["seg"])))


# ── 台词按镜头对时(spec 2026-10-31-IndexTTS2港片配音-design.md §10)────────────

FOUR = ("【镜头1】石子飞来。孙老栓说：「小子,你这手……」【镜头2】燕无咎回头。燕无咎说：「老栓叔,我就是碰巧。」"
        "【镜头3】孙老栓摆手。孙老栓说：「放心,老头子嘴严。」【镜头4】燕无咎望向锅碗,闭口不语。")


def test_line_starts_follow_the_subshot_each_line_sits_in():
    assert tts.line_starts(FOUR, ["孙老栓", "燕无咎"], 12.0) == [0.0, 3.0, 6.0]
    two_in_one = "【镜头1】甲说：「一」乙说：「二」【镜头2】甲说：「三」"
    assert tts.line_starts(two_in_one, ["甲", "乙"], 8.0) == [0.0, 0.0, 4.0]   # 同一镜头里的句子接着说(拼的时候顺延)
    assert tts.line_starts("没有镜头号。甲说：「一」", ["甲"], 8.0) == [0.0]


def test_plan_snapshots_the_line_starts_and_goes_stale_when_shots_move(store, two):
    repo.update(store, "segment", two["seg"], description="【镜头1】林小雨低头。【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」沈墨说：「我知道。」",
                duration_sec=8.0)
    tid = tts.plan_tts(store, two["seg"], seed=1)
    assert json.loads(repo.get(store, "task", tid)["params_json"])["starts"] == [4.0, 4.0]
    _done(store, tid, "outputs/tasks/80/lines.wav")
    seg = lambda: repo.get(store, "segment", two["seg"])   # noqa: E731
    assert tts.stale(store, seg()) == []
    repo.update(store, "segment", two["seg"], duration_sec=10.0)
    assert any("镜头" in r for r in tts.stale(store, seg()))


def test_line_audio_made_before_shot_timing_is_not_stale_for_it(store, two):
    """方案 B 上线前合成的(参数里没有 starts):不因「镜头节奏」判过期 —— 已经过了口型的视频不被连带作废。"""
    from tools.drama.tasks import create_task
    p = json.loads(repo.get(store, "task", tts.plan_tts(store, two["seg"], seed=1))["params_json"])
    p.pop("starts")
    tid = create_task(store, kind="tts", target_type="segment", target_id=two["seg"], model="IndexTTS-2", params=p,
                      seed=2, prompt="p", refs=[])
    _done(store, tid, "outputs/tasks/90/lines.wav")
    repo.update(store, "segment", two["seg"], duration_sec=13.0)
    assert tts.stale(store, repo.get(store, "segment", two["seg"])) == []
