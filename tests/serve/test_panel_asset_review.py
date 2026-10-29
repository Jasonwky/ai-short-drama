# -*- coding: utf-8 -*-
"""资产图审页(spec 2026-10-28-成片质量闸门-第一期-design.md 第 6.4 节):代码检查 + AI 结论 + 人定稿 / 重出 / 改描述。"""
import json
import subprocess

from tools.drama import approval, repo
from tools.llm import runs, service


def _png(path, bg="0xF4F4F4", size=(464, 832)):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={bg}:s={size[0]}x{size[1]}",
                    "-vf", "drawbox=x=150:y=100:w=160:h=600:color=0x333333:t=fill", "-frames:v", "1", str(path)], check=True)


def _world(runtime, store, sample, lin_bg="0xF4F4F4"):
    root = runtime.data_root
    _png(root / "assets/lin.png", lin_bg)
    _png(root / "assets/lin_4v.png", size=(1664, 928))
    _png(root / "assets/yard.png", "0x557755")
    _png(root / "assets/jade.png")
    store.conn.execute("DELETE FROM asset_approval")
    store.conn.execute("UPDATE character SET final_prompt = 'A young woman.' WHERE drama_id = ?", (sample["drama"],))
    return root


def _ai(store, sample, slot="image", verdict="通过", path="assets/lin.png"):
    """一次 AI 检查的产物(真 LLM 的契约在 test_contract_asset_check.py)。"""
    from tools.llm import asset_check
    dims = asset_check.dims("character", slot)
    items = [{"dim": d, "verdict": "通过", "evidence": "", "fix": "无", "suggestion": ""} for d in dims]
    if verdict == "不通过":
        items[0] = {"dim": "时代与服饰", "verdict": "不通过", "evidence": "胸前两个贴袋", "fix": "改描述",
                    "suggestion": "A young woman in a cross-collared robe."}
    params = {"slot": slot, "path": path}
    rid = runs.start(store, task="asset_check_character", target_type="character", target_id=sample["lin"], model="m",
                     input_sha=f"sha-{slot}-{verdict}", llm_mode="real", params=params)
    runs.propose(store, rid, {"check_number": "123", "items": items}, "j")
    return rid


def test_page_shows_code_and_ai_results(client, runtime, store, sample):
    _world(runtime, store, sample)
    _ai(store, sample, verdict="不通过")
    page = client.get(f"/drama/{sample['drama']}/asset-review").text
    assert 'data-review-card="character-' in page and "代码 · 浅色底、主体没顶到边" in page
    assert 'data-ai-dim="时代与服饰" data-verdict="不通过"' in page and "胸前两个贴袋" in page
    assert "A young woman in a cross-collared robe." in page                       # 改描述预填 AI 建议
    assert 'data-ai-state="none"' in page                                          # 场景还没 AI 检查


def test_failed_items_need_a_reason_to_approve(client, runtime, store, sample):
    _world(runtime, store, sample)
    _ai(store, sample, verdict="不通过")
    url = f"/asset/character/{sample['lin']}/image/approve"
    r = client.post(url, data={})
    assert r.status_code == 400 and "要写理由" in r.text
    assert client.post(url, data={"reason": "首帧会重画衣服"}, follow_redirects=False).status_code == 303
    a = approval.current(store, "character", sample["lin"], "image")
    assert a["reason"] == "首帧会重画衣服" and json.loads(a["checks_json"])["failed"] == ["时代与服饰"]


def test_approve_clean_only_takes_images_that_passed_everything(client, runtime, store, sample):
    _world(runtime, store, sample)
    _ai(store, sample)                                                    # 正面图:代码 + AI 都过
    _ai(store, sample, slot="fourview", verdict="不通过", path="assets/lin_4v.png")
    client.post(f"/drama/{sample['drama']}/asset-review/approve-clean")
    assert approval.current(store, "character", sample["lin"], "image") is not None
    assert approval.current(store, "character", sample["lin"], "fourview") is None


def test_code_check_failure_blocks_the_one_click(client, runtime, store, sample):
    _world(runtime, store, sample, lin_bg="0xAA2222")                    # 红底:代码检查不过
    _ai(store, sample)
    client.post(f"/drama/{sample['drama']}/asset-review/approve-clean")
    assert approval.current(store, "character", sample["lin"], "image") is None


def test_regenerate_and_rewrite_plan_new_images(client, runtime, store, sample):
    _world(runtime, store, sample)
    store.conn.execute("UPDATE drama SET style = 'realistic' WHERE id = ?", (sample["drama"],))
    assert client.post(f"/asset/character/{sample['lin']}/image/regenerate", follow_redirects=False).status_code == 303
    client.post(f"/asset/character/{sample['lin']}/image/rewrite", data={"final_prompt": "A woman in a hemp robe."})
    assert repo.get(store, "character", sample["lin"])["final_prompt"] == "A woman in a hemp robe."
    rows = store.conn.execute("SELECT prompt FROM task WHERE target_type = 'character' ORDER BY id").fetchall()
    assert len(rows) == 2 and "hemp robe" in rows[1][0]
    repo.update(store, "character", sample["lin"], seed=77)               # 锁了种子:换种子重出没意义
    r = client.post(f"/asset/character/{sample['lin']}/image/regenerate")
    assert r.status_code == 400 and "种子锁定" in r.text


def test_check_button_starts_the_vision_task(client, runtime, store, sample):
    _world(runtime, store, sample)
    client.post(f"/asset/character/{sample['lin']}/image/check")
    runtime.wait_llm()
    row = runs.history(store, "character", sample["lin"], task="asset_check_character")[-1]
    assert runs.params(row) == {"slot": "image", "path": "assets/lin.png"} and row["llm_mode"] == "fake"
    assert row["state"] == "failed"                                     # 假 LLM 看不见图:校验数字必错,被判卷拦下
    assert service.spec("asset_check_character").model == service.VISION_MODEL


def _voice(root, rel, ok=True, heard="这块玉佩是我母亲留给我的"):
    """一条试听音 + 回听结果(与 voicer 落盘的格式一致)。"""
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"RIFF")
    f.with_name("voice.json").write_text(json.dumps({"text": "这块玉佩，是我母亲留给我的。", "heard": heard, "ok": ok,
                                                     "hit": 1.0 if ok else 0.2, "sec": 2.0, "gen_sec": 4.0},
                                                    ensure_ascii=False), encoding="utf-8")


def test_voice_design_plans_three_takes(client, runtime, store, sample):
    r = client.post(f"/character/{sample['lin']}/voice/design", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"#character-{sample['lin']}-voice")
    assert store.conn.execute("SELECT COUNT(*) FROM task WHERE kind = 'voice' AND target_id = ?",
                              (sample["lin"],)).fetchone()[0] == 3
    page = client.get(f"/drama/{sample['drama']}/asset-review").text
    assert f'data-voice-card="{sample["lin"]}"' in page and "正在生成 3 版" in page
    # 沈墨没有声音描述:没有生成按钮
    shen = page.split(f'data-voice-card="{sample["shen"]}"')[1]
    assert "data-voice-design" not in shen.split("</section>")[0]


def test_voice_that_was_heard_right_approves_without_a_reason(client, runtime, store, sample):
    _voice(runtime.data_root, "outputs/tasks/7/voice.wav")
    repo.update(store, "character", sample["lin"], voice_ref_path="outputs/tasks/7/voice.wav",
                voice_ref_text="这块玉佩，是我母亲留给我的。")
    page = client.get(f"/drama/{sample['drama']}/asset-review").text
    assert 'data-voice-check="1"' in page and "/media/outputs/tasks/7/voice.wav" in page
    url = f"/asset/character/{sample['lin']}/voice/approve"
    assert client.post(url, data={}, follow_redirects=False).status_code == 303
    a = approval.current(store, "character", sample["lin"], "voice")
    assert a["path"] == "outputs/tasks/7/voice.wav" and json.loads(a["checks_json"])["listen"]["ok"] is True
    assert "data-voice-approved" in client.get(f"/drama/{sample['drama']}/asset-review").text


def test_misheard_or_unchecked_voice_needs_a_reason(client, runtime, store, sample):
    _voice(runtime.data_root, "outputs/tasks/8/voice.wav", ok=False, heard="完全不对")
    repo.update(store, "character", sample["lin"], voice_ref_path="outputs/tasks/8/voice.wav")
    url = f"/asset/character/{sample['lin']}/voice/approve"
    r = client.post(url, data={})
    assert r.status_code == 400 and "要写理由" in r.text and "回听没念对" in r.text
    assert client.post(url, data={"reason": "人耳听过,是 Whisper 听错"}, follow_redirects=False).status_code == 303
    (runtime.data_root / "up").mkdir()
    (runtime.data_root / "up/shen.wav").write_bytes(b"RIFF")
    repo.update(store, "character", sample["shen"], voice_ref_path="up/shen.wav")     # 手工放的:没有回听结果
    r = client.post(f"/asset/character/{sample['shen']}/voice/approve", data={})
    assert r.status_code == 400 and "回听没有结果" in r.text
