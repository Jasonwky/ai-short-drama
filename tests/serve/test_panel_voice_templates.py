# -*- coding: utf-8 -*-
"""资产图审 · 念法模板(spec 2026-10-31-IndexTTS2港片配音-design.md §3、§6、§9 V2/V9)。"""
import io
import json
import wave

from tools.cut import prosody
from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state


def _wav(seconds=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * seconds))
    return buf.getvalue()


def _approved_voice(store, cid):
    repo.update(store, "character", cid, voice_ref_path="v/lin.wav", voice_ref_text="试听句")
    store.conn.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                       " VALUES ('character', ?, 'voice', 'v/lin.wav', 'x', 't')", (cid,))


def test_import_needs_a_source_and_passes_the_prosody_bar(client, store, sample, runtime, monkeypatch):
    monkeypatch.setattr(prosody, "template_problems", lambda x, sr: [])
    url = f"/drama/{sample['drama']}/voice-template/import"
    r = client.post(url, data={"name": "喝令", "source": " "}, files={"file": ("a.wav", _wav(), "audio/wav")})
    assert r.status_code == 400 and repo.voice_templates(store, sample["drama"]) == []
    r = client.post(url, data={"name": "喝令", "source": "我们自己合成"}, files={"file": ("a.wav", _wav(), "audio/wav")},
                    follow_redirects=False)
    assert r.status_code == 303
    [t] = repo.voice_templates(store, sample["drama"])
    assert t["name"] == "喝令" and t["source"] == "upload:我们自己合成" and (runtime.data_root / t["path"]).is_file()


def test_import_below_the_prosody_bar_is_refused(client, store, sample, monkeypatch):
    monkeypatch.setattr(prosody, "template_problems", lambda x, sr: ["音高起伏 8.0 半音 < 15(抑扬顿挫不够)"])
    r = client.post(f"/drama/{sample['drama']}/voice-template/import", data={"name": "平", "source": "自己录"},
                    files={"file": ("a.wav", _wav(), "audio/wav")})
    assert r.status_code == 400 and "半音" in r.text and repo.voice_templates(store, sample["drama"]) == []


def test_pick_a_character_default_template(client, store, sample):
    t = repo.add_voice_template(store, sample["drama"], name="喝令", path="p.wav", sha256="s", source="synth:1", note="")
    r = client.post(f"/character/{sample['lin']}/voice-template", data={"template_id": str(t)}, follow_redirects=False)
    assert r.status_code == 303 and repo.get(store, "character", sample["lin"])["voice_template_id"] == t
    client.post(f"/character/{sample['lin']}/voice-template", data={"template_id": ""})
    assert repo.get(store, "character", sample["lin"])["voice_template_id"] is None


def test_generate_candidates_and_save_one_that_passes(client, store, sample, runtime):
    _approved_voice(store, sample["lin"])
    r = client.post(f"/character/{sample['lin']}/voice-template/generate", data={"preset": "惊疑"}, follow_redirects=False)
    assert r.status_code == 303
    tasks = store.conn.execute("SELECT * FROM task WHERE kind = 'voice' AND json_extract(params_json, '$.workflow')"
                               " = 'indextts2_template'").fetchall()
    assert len(tasks) == 3
    rel = f"outputs/tasks/{tasks[0]['id']}/template.wav"
    (runtime.data_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (runtime.data_root / rel).write_bytes(_wav())
    set_task_state(store, tasks[0]["id"], "queued", external_id="v")
    set_task_state(store, tasks[0]["id"], "done", result_path=rel, result_sha256="sha-c")
    (runtime.data_root / rel).with_name("prosody.json").write_text(
        json.dumps({"occupancy": 0.6, "range_st": 9.0, "problems": ["音高起伏 9.0 半音 < 15"]}), encoding="utf-8")
    html = client.get(f"/drama/{sample['drama']}/asset-review").text
    assert "data-template-candidate" in html and "9.0" in html
    r = client.post(f"/task/{tasks[0]['id']}/save-template", data={"name": "惊疑"})
    assert r.status_code == 400                                  # 没过门槛不让存
    (runtime.data_root / rel).with_name("prosody.json").write_text(
        json.dumps({"occupancy": 0.9, "range_st": 16.0, "problems": []}), encoding="utf-8")
    r = client.post(f"/task/{tasks[0]['id']}/save-template", data={"name": "惊疑"}, follow_redirects=False)
    assert r.status_code == 303
    [t] = repo.voice_templates(store, sample["drama"])
    assert (t["name"], t["path"], t["sha256"], t["source"]) == ("惊疑", rel, "sha-c", f"synth:{tasks[0]['id']}")


def test_template_candidates_are_not_offered_as_voice_takes(client, store, sample):
    _approved_voice(store, sample["lin"])
    tid = create_task(store, kind="voice", target_type="character", target_id=sample["lin"], model="m",
                      params={"workflow": "indextts2_template", "writes": "voice_template_candidate"}, seed=1, prompt="p", refs=[])
    set_task_state(store, tid, "queued", external_id="v")
    set_task_state(store, tid, "done", result_path="x/template.wav", result_sha256="s")
    assert f'data-pick-voice="{tid}"' not in client.get(f"/drama/{sample['drama']}/asset-review").text
