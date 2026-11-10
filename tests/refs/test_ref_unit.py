# -*- coding: utf-8 -*-
"""拆解卡第一层 ref_unit(spec 2026-11-07-参考库-design.md 2.1):上下文、schema、判卷四类打回、apply 写行。
FakeLLM 只当确定性替身走 begin / finish / approve,不声称覆盖 LLM 契约(那是真 LLM 契约测试的事)。"""
import json

import pytest

from tools.llm import ref_unit as ru
from tools.llm import service
from tools.llm.fake import FakeLLM
from tools.llm.gate import strict_schema_problems
from tools.llm.service import LLMBackend

TRANSCRIPT = "把你打开好了我冲着杏州的赶紧过来办事幸好我一直撞下是是谁把本王的球换成了陆西酒小姐出闯户跑啊好大的胆子谁派你来的"
CHAPTER = "第一章 雪夜\n\n沈清辞推开门,风雪灌进来。\n“你终于来了，”顾长风说，“我等了三年。”\n她没有回答。"
JPG = b"\xff\xd8\xff\xe0fake-jpeg"
HEAD = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
BODY = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]


def _row(c, kind, rel, unit=1, *, verdict="ok", purged="", meta=None):
    return c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at,"
                     " hard_due_at, purged_at, meta_json) VALUES (1, ?, ?, ?, 's', 'o', ?, 't', 't', 't', ?, ?)",
                     (kind, unit, rel, verdict, purged, json.dumps(meta or {}))).lastrowid


def _write(root, rel, data):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _seed(refs, root):
    """一部红果剧:第 1 集转写 + 本集录屏的关键帧(7 张开头 + 9 张之后)+ 上一版录屏的关键帧(不该被带上);外加一章小说。"""
    chars = [[ch, round(10.0 + i * 0.3, 2), round(10.3 + i * 0.3, 2)] for i, ch in enumerate(TRANSCRIPT)]
    _write(root, "content/hongguo/7/ep1-abc.transcript.json", json.dumps({"text": TRANSCRIPT, "chars": chars}, ensure_ascii=False))
    _write(root, "c/ch1.txt", CHAPTER)
    with refs.write() as c:
        c.execute("INSERT INTO ref_source (key) VALUES ('hongguo')")
        c.execute("INSERT INTO ref_work (id, source, external_id, title, first_seen) VALUES (1, 'hongguo', '7', '掌上珠', 't')")
        tid = _row(c, "transcript", "content/hongguo/7/ep1-abc.transcript.json")
        for t in [*BODY, *HEAD]:                     # 乱序写入:context 要按秒排
            rel = f"content/hongguo/7/ep1-abc-kf/{t:g}.jpg"
            _write(root, rel, JPG + str(t).encode())
            _row(c, "keyframe", rel, meta={"sec": t})
        _write(root, "content/hongguo/7/ep1-old-kf/5.jpg", JPG)
        _row(c, "keyframe", "content/hongguo/7/ep1-old-kf/5.jpg", meta={"sec": 5.0})
        purged = _row(c, "keyframe", "content/hongguo/7/ep1-abc-kf/95.jpg", purged="2026-11-07", meta={"sec": 95.0})
        cid = _row(c, "chapter", "c/ch1.txt")
    return tid, cid, purged


def _video_args(**kw):
    return {"hook": "有", "reversal": "大", "emotion": "放", "paywall": "是",
            "summary": "女主被人当成刺客抓住,王爷逼问来历。", "quote": "好大的胆子,谁派你来的",
            "names": ["陆惜久"], "visual_hook": "有", "visual_sec": 1.5, **kw}


def _chapter_args(**kw):
    return {"hook": "有", "reversal": "无", "emotion": "压", "paywall": "是",
            "summary": "雪夜里女主来赴一个等了她多年的约。", "quote": "“你终于来了,”顾长风说",
            "names": ["沈清辞", "顾长风"], **kw}


def test_video_context_takes_head_frames_and_at_most_six_later_ones(refs, tmp_path):
    tid, _, _ = _seed(refs, tmp_path)
    ctx = ru.context(refs, tid, tmp_path)
    secs = [f["sec"] for f in ctx["frames"]]
    assert secs[:7] == HEAD and len(secs) == 13
    assert secs[7:] == [10.0, 30.0, 40.0, 60.0, 70.0, 90.0]           # 9 张里均匀取 6 张,首尾都在
    assert 5.0 not in secs and 95.0 not in secs                          # 上一版录屏的、已清理的不带
    assert ru.images(ctx)[0] == JPG + b"0.0" and len(ru.images(ctx)) == 13
    sch = ru.schema(ctx)
    assert strict_schema_problems(sch) == []
    assert sch["properties"]["visual_sec"]["enum"] == secs
    assert sch["properties"]["paywall"]["enum"] == ["是", "否"]
    msg = ru.user_message(ctx)
    assert "掌上珠" in msg and "第 1 集" in msg and TRANSCRIPT in msg and "依次是第 0、0.5、1" in msg


def test_chapter_context_has_no_frames_or_visual_fields(refs, tmp_path):
    _, cid, _ = _seed(refs, tmp_path)
    ctx = ru.context(refs, cid, tmp_path)
    assert ctx["frames"] == [] and ru.images(ctx) == []
    sch = ru.schema(ctx)
    assert strict_schema_problems(sch) == [] and "visual_sec" not in sch["properties"]
    assert "第 1 章" in ru.user_message(ctx) and CHAPTER in ru.user_message(ctx)


def test_context_refuses_missing_bad_and_frameless_units(refs, tmp_path):
    tid, _, _ = _seed(refs, tmp_path)
    with pytest.raises(ValueError, match="没有内容"):
        ru.context(refs, 999, tmp_path)
    with refs.write() as c:
        rej = _row(c, "chapter", "c/ch1.txt", verdict="rejected")
        _write(tmp_path, "content/hongguo/7/ep2-def.transcript.json", json.dumps({"text": "谁派你来的", "chars": []}))
        bare = _row(c, "transcript", "content/hongguo/7/ep2-def.transcript.json", unit=2)
    with pytest.raises(ValueError, match="不收"):
        ru.context(refs, rej, tmp_path)
    with pytest.raises(ValueError, match="关键帧"):
        ru.context(refs, bare, tmp_path)
    (tmp_path / "content/hongguo/7/ep1-abc-kf/3.jpg").unlink()
    with pytest.raises(ValueError, match="关键帧文件不见了"):
        ru.context(refs, tid, tmp_path)


def test_judge_passes_good_annotations(refs, tmp_path):
    tid, cid, _ = _seed(refs, tmp_path)
    assert ru.judge_factory(ru.context(refs, tid, tmp_path))(_video_args()) == []
    assert ru.judge_factory(ru.context(refs, cid, tmp_path))(_chapter_args()) == []


def test_judge_sends_back_bad_quote_names_frame_and_copied_summary(refs, tmp_path):
    tid, cid, _ = _seed(refs, tmp_path)
    video = ru.judge_factory(ru.context(refs, tid, tmp_path))
    chapter = ru.judge_factory(ru.context(refs, cid, tmp_path))
    assert any("找不到" in p for p in video(_video_args(quote="我要杀了你们全家老小")))
    assert any("找不到" in p for p in chapter(_chapter_args(quote="顾长风笑着说")))
    assert any("太短" in p for p in chapter(_chapter_args(quote="你终于来了")))          # 审查 M2:至少 6 个字
    assert any("太短" in p for p in video(_video_args(quote="你终于来了")))            # 转写按音节数
    assert any("林婉" in p for p in chapter(_chapter_args(names=["沈清辞", "林婉"])))
    assert any("名字" in p for p in video(_video_args(names=["沈清辞"])))
    assert any("visual_sec" in p for p in video(_video_args(visual_sec=5.0)))
    # 用自己的话:与本单元原文连续 12 字相同就打回(标点空白不算);11 字不算
    assert any("照抄" in p for p in chapter(_chapter_args(summary="开头说“你终于来了,”顾长风说,“我等了三年。”")))
    assert any("照抄" in p for p in video(_video_args(summary="王爷喝问:是谁把本王的球换成了陆西酒小姐")))
    assert not any("照抄" in p for p in chapter(_chapter_args(summary="你终于来了顾长风说我等")))
    assert any("summary" in p for p in chapter(_chapter_args(summary="  ")))


def test_registered_as_vision_task_on_the_reference_library():
    sp = service.TASKS["ref_unit"]
    assert sp.module is ru and sp.target == "ref_content" and sp.needs_root and sp.model == service.VISION_MODEL
    assert sp.max_tokens == 3000


def _run(refs, root, target, args):
    llm = LLMBackend(FakeLLM({ru.TOOL: args}), "m", "real")      # 确定性替身,不声称覆盖 LLM 契约
    rid, reused = service.begin(refs, "ref_unit", target, {}, llm, data_root=root)
    assert not reused
    assert service.finish(refs, rid, llm, root / "journal", data_root=root) is None
    return rid


def test_approve_writes_a_unit_row_with_its_run_id(refs, tmp_path):
    tid, cid, _ = _seed(refs, tmp_path)
    rid = _run(refs, tmp_path, tid, _video_args())
    service.approve(refs, rid, data_root=tmp_path)
    row = refs.conn.execute("SELECT * FROM ref_unit WHERE content_id = ?", (tid,)).fetchone()
    assert (row["run_id"], row["work_id"], row["unit"]) == (rid, 1, 1)
    assert json.loads(row["fields_json"]) == {"hook": "有", "reversal": "大", "emotion": "放", "paywall": "是"}
    assert row["quote"] == "好大的胆子谁派你来的"                                      # 存转写原文写法
    assert float(row["quote_at"]) == round(10.0 + TRANSCRIPT.index("好大的胆子") * 0.3, 2)
    assert json.loads(row["names_json"]) == ["陆惜久"]
    assert json.loads(row["visual_json"]) == {"hook": "有", "sec": 1.5}
    assert refs.conn.execute("SELECT state FROM llm_run WHERE id = ?", (rid,)).fetchone()[0] == "approved"

    rid2 = _run(refs, tmp_path, cid, _chapter_args())
    service.approve(refs, rid2, data_root=tmp_path)
    row = refs.conn.execute("SELECT * FROM ref_unit WHERE content_id = ?", (cid,)).fetchone()
    assert row["run_id"] == rid2 and row["quote"] == "“你终于来了，”顾长风说" and row["quote_at"] == ""
    assert json.loads(row["visual_json"]) == {}


def test_fake_mode_runs_still_cannot_be_approved(refs, tmp_path):
    tid, _, _ = _seed(refs, tmp_path)
    llm = LLMBackend(FakeLLM({ru.TOOL: _video_args()}), "m", "fake")
    rid, _ = service.begin(refs, "ref_unit", tid, {}, llm, data_root=tmp_path)
    service.finish(refs, rid, llm, tmp_path / "journal", data_root=tmp_path)
    with pytest.raises(ValueError, match="假 LLM"):
        service.approve(refs, rid, data_root=tmp_path)
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_unit").fetchone()[0] == 0
