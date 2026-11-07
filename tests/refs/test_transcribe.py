# -*- coding: utf-8 -*-
"""参考库转写:云机脚本的纯函数 + 本机 run_one(假 ComfyUI client、假 ssh runner;音频转换换成假的,不要 ffmpeg)。"""
import ast
import hashlib
import json
from pathlib import Path

import pytest

from tools.gen.capability import WHISPER_ALIGN
from tools.refs import asr_remote, jobs, rjournal, settings, transcribe, works

TEXT = "你怎么还在这里我不是让你走了吗今天这件事没完我们走着瞧"   # 27 个汉字
SCRIPT_SRC = Path("tools/refs/asr_remote.py").read_bytes()


class Client:
    def __init__(self, running=(), pending=(), vram=20.0):
        self.q = (list(running), list(pending))
        self.vram = vram

    def queue_ids(self):
        return self.q

    def vram_free_gb(self):
        return self.vram

    def free_models(self):
        pass


def ok_runner(text=TEXT, seen=None):
    def run(args, script):
        assert args == [WHISPER_ALIGN.model_dir]
        assert script.startswith(SCRIPT_SRC) and script.rstrip().endswith(b"sys.exit(main(sys.argv[1], _AUDIO_B64))")
        compile(script, "asr_remote", "exec")
        if seen is not None:
            seen.append(script)
        chars = [[c, i * 0.2, i * 0.2 + 0.2] for i, c in enumerate(text)]
        return 0, "Loading...\n" + json.dumps({"text": text, "chars": chars, "chunks": 1, "asr_sec": 3.2}, ensure_ascii=False)
    return run


@pytest.fixture
def env(refs, tmp_path, monkeypatch):
    settings.ensure(refs)
    monkeypatch.setattr(transcribe, "pcm16k", lambda p: b"\x01\x00" * 1600)
    wid = works.upsert(refs, "hongguo", "77")
    rel = "content/hongguo/77/1.webm"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_bytes(b"webm")
    with refs.write() as c:
        rid = c.execute("INSERT INTO ref_content (work_id, kind, unit, title, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                        " VALUES (?, 'recording', 1, '第 1 集', ?, 'x', 'u', 'ok', '2026-11-07T10:00:00+08:00',"
                        " '2026-12-07T10:00:00+08:00', '2027-01-06T10:00:00+08:00')", (wid, rel)).lastrowid
    jid = jobs.enqueue(refs, "hongguo", "transcribe", day="2026-11-07", work_id=wid, params={"recording_id": rid})
    return refs, tmp_path, rid, jid


def transcripts(refs):
    return [dict(r) for r in refs.conn.execute("SELECT * FROM ref_content WHERE kind = 'transcript'")]


def test_chunks_cover_the_audio_in_25_second_blocks():
    spans = asr_remote.chunks(16000 * 61)
    assert len(spans) == 3 and spans[0] == (0, 16000 * 25) and spans[-1] == (16000 * 50, 16000 * 61)


VOCAB = {1: "这块".encode(), 2: "玉".encode()[:2], 3: "玉".encode()[2:], 4: " ".encode(), 5: "是".encode()}


def test_merge_tokens_joins_byte_tokens():
    decode = lambda ids: b"".join(VOCAB[i] for i in ids).decode("utf-8", "replace")
    assert asr_remote.merge_tokens([1, 2, 3, 4, 5], [0.5, 0.6, 0.7, 0.8, 0.9], decode) == [
        ("这", 0.0, 0.5), ("块", 0.0, 0.5), ("玉", 0.5, 0.7), ("是", 0.8, 0.9)]


def test_remote_script_top_level_imports_are_stdlib_only():
    tree = ast.parse(SCRIPT_SRC.decode("utf-8"))
    top = {a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names}
    top |= {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom)}
    assert top <= {"__future__", "base64", "json", "sys", "time", "pathlib"}


def test_no_job_returns_none(refs, tmp_path):
    settings.ensure(refs)
    assert transcribe.run_one(refs, tmp_path, Client(), ok_runner()) is None


def test_success_stores_transcript_inheriting_recording(env):
    refs, root, rid, jid = env
    seen = []
    line = transcribe.run_one(refs, root, Client(), ok_runner(seen=seen))
    assert line == f"参考库转写 {jid}:27 个汉字"
    assert jobs.get(refs, jid)["state"] == "done"
    rec = dict(refs.conn.execute("SELECT * FROM ref_content WHERE id = ?", (rid,)).fetchone())
    [t] = transcripts(refs)
    assert t["verdict"] == "ok" and t["reason"] == ""
    assert (t["work_id"], t["unit"], t["due_at"], t["hard_due_at"]) == (rec["work_id"], 1, rec["due_at"], rec["hard_due_at"])
    assert t["path"] == "content/hongguo/77/1.transcript.json"
    body = (root / t["path"]).read_bytes()
    assert t["sha256"] == hashlib.sha256(body).hexdigest() and json.loads(body)["text"] == TEXT
    assert json.loads(t["meta_json"]) == {"chunks": 1, "asr_sec": 3.2}
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_fingerprint WHERE content_id = ?", (t["id"],)).fetchone()[0] > 0
    [j] = rjournal.read(root, event="transcribe")
    assert j["cloud"] and j["job"] == jid and j["work_id"] == rec["work_id"]
    assert b'_AUDIO_B64 = "' + __import__("base64").b64encode(b"\x01\x00" * 1600) + b'"' in seen[0]


def test_few_hanzi_is_rejected_without_fingerprint(env):
    refs, root, _, jid = env
    transcribe.run_one(refs, root, Client(), ok_runner(text="啊哈哈"))
    [t] = transcripts(refs)
    assert t["verdict"] == "rejected" and t["reason"] == "几乎没有台词(3 个汉字)"
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_fingerprint WHERE content_id = ?", (t["id"],)).fetchone()[0] == 0
    assert jobs.get(refs, jid)["state"] == "done"


def test_busy_comfy_queue_leaves_job_waiting(env):
    refs, root, _, jid = env
    line = transcribe.run_one(refs, root, Client(running=["x"]), lambda a, s: pytest.fail("不该跑"))
    j = jobs.get(refs, jid)
    assert j["state"] == "waiting" and j["started_at"] == "" and "队列" in line
    assert transcripts(refs) == []


def test_ssh_unreachable_goes_back_to_waiting(env):
    refs, root, _, jid = env
    transcribe.run_one(refs, root, Client(), lambda a, s: (255, "Connection refused"))
    j = jobs.get(refs, jid)
    assert j["state"] == "waiting" and j["attempts"] == 1


def test_oom_goes_back_to_waiting(env):
    refs, root, _, jid = env
    transcribe.run_one(refs, root, Client(), lambda a, s: (3, "显存不够:CUDA out of memory. Tried"))
    j = jobs.get(refs, jid)
    assert j["state"] == "waiting" and j["attempts"] == 1


def test_missing_recording_fails(env):
    refs, root, _, jid = env
    (root / "content/hongguo/77/1.webm").unlink()
    transcribe.run_one(refs, root, Client(), lambda a, s: pytest.fail("不该跑"))
    j = jobs.get(refs, jid)
    assert j["state"] == "failed" and j["error"].startswith("录屏文件不在了")


def test_script_error_fails_with_output_tail(env):
    refs, root, _, jid = env
    transcribe.run_one(refs, root, Client(), lambda a, s: (1, "Traceback ...\nKeyError: 'token_timestamps'"))
    j = jobs.get(refs, jid)
    assert j["state"] == "failed" and "退出码 1" in j["error"] and "KeyError: 'token_timestamps'" in j["error"]
    assert transcripts(refs) == []


def test_audio_conversion_failure_fails_job_not_left_running(env, monkeypatch):
    refs, root, _, jid = env
    def bad(p):
        raise RuntimeError("转音频失败:moov atom not found")
    monkeypatch.setattr(transcribe, "pcm16k", bad)
    transcribe.run_one(refs, root, Client(), lambda a, s: pytest.fail("不该跑"))
    j = jobs.get(refs, jid)
    assert j["state"] == "failed" and j["error"].startswith("转音频失败")


def test_ssh_unreachable_three_times_in_a_row_fails_with_hint(env):
    from tools.refs import config
    refs, root, _, jid = env
    assert config.TRANSCRIBE_RETRIES == 3
    for _ in range(2):
        transcribe.run_one(refs, root, Client(), lambda a, s: (255, "Connection refused"))
        assert jobs.get(refs, jid)["state"] == "waiting"
    line = transcribe.run_one(refs, root, Client(), lambda a, s: (3, "CUDA out of memory. Tried"))
    j = jobs.get(refs, jid)
    assert j["state"] == "failed" and j["attempts"] == 2
    assert j["error"] == "连续 3 次 ssh 连不上 / 显存被抢,先停下 —— 云机空出来后点「重新转写」"
    assert "失败" in line


@pytest.mark.parametrize("runner, kind", [
    (lambda a, s: (_ for _ in ()).throw(FileNotFoundError("ssh")), "FileNotFoundError"),
    (lambda a, s: (0, json.dumps({"text": TEXT, "chunks": 1, "asr_sec": 1.0})), "KeyError"),   # 输出缺 chars
])
def test_unexpected_error_fails_the_job_journals_and_raises(env, runner, kind):
    refs, root, _, jid = env
    with pytest.raises(Exception) as ei:
        transcribe.run_one(refs, root, Client(), runner)
    assert type(ei.value).__name__ == kind
    j = jobs.get(refs, jid)
    assert j["state"] == "failed" and j["error"].startswith(f"意外异常:{kind}: ")
    [e] = rjournal.read(root, event="job")
    assert e["job"] == jid and e["error"] == j["error"]
