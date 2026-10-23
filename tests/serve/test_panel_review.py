# -*- coding: utf-8 -*-
"""视频面板的看片区:切镜计数、接缝两帧、看片确认(spec 2026-10-22-连续性保障-design.md 第 1、2 项)。"""
from tests.cut.conftest import done_task
from tools.cut import checklist
from tools.drama import repo
from tools.serve.fakes import sample_mp4


def _two(store, sample, root):
    seg2 = repo.add_segment(store, sample["episode"], 2, scene_id=sample["yard"], duration_sec=8.0,
                            description="【镜头1】林小雨把玉佩收进怀里。")
    for n, seg in ((1, sample["seg"]), (2, seg2)):
        done_task(store, root, seg, "video", f"outputs/tasks/v{n}/clip.mp4", sample_mp4())
    return seg2


def test_review_section_shows_cuts_and_the_seam(client, runtime, store, sample):
    seg2 = _two(store, sample, runtime.data_root)
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert page.count("data-review-section") == 2 and page.count("data-cut-count") == 2
    assert page.count("data-seam>") == 1                     # 只有第 2 段有「上一段末帧」
    assert f'id="seg-{seg2}"' in page and "data-review-confirm" in page


def test_card_warns_when_the_lines_changed_after_the_video(client, runtime, store, sample):
    seg2 = _two(store, sample, runtime.data_root)
    repo.update(store, "segment", seg2, description="【镜头1】林小雨说：「谁敢碰我的玉佩？」")
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "data-stale-lines" in page and "谁敢碰我的玉佩？" in page


def test_card_warns_when_a_line_was_deleted_and_stays_quiet_when_unchanged(client, runtime, store, sample):
    from tools.drama import rules
    seg2 = _two(store, sample, runtime.data_root)
    row = repo.get(store, "segment", seg2)
    # 当前视频带台本快照,当时说了「旧台词一句。」
    from tools.drama.tasks import create_task, set_task_state
    from tools.serve.dispatch import sha256_file
    rel, root = "outputs/tasks/v9/clip.mp4", runtime.data_root
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(sample_mp4())
    t = create_task(store, kind="video", target_type="segment", target_id=seg2, model="m-video", seed=7, prompt="p",
                    params={"writes": "video_path", "script": {"lines": [["林小雨", "旧台词一句。"]]}}, refs=[])
    set_task_state(store, t, "queued", external_id=f"p{t}")
    set_task_state(store, t, "done", result_path=rel, result_sha256=sha256_file(root / rel))
    repo.update(store, "segment", seg2, video_path=rel)
    repo.update(store, "segment", sample["seg"], description="【镜头1】林小雨低头。")    # 第 1 段不带台词,免得它也报
    repo.update(store, "segment", seg2, description="【镜头1】林小雨低头。")
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "data-stale-lines" in page and "已删的台词" in page and "旧台词一句。" in page
    repo.update(store, "segment", seg2, description="【镜头1】林小雨说：「旧台词一句。」")
    assert "data-stale-lines" not in client.get(f"/episode/{sample['episode']}/videos").text


def test_confirm_review_is_tied_to_the_current_video(client, runtime, store, sample):
    seg2 = _two(store, sample, runtime.data_root)
    r = client.post(f"/segment/{seg2}/review", data={"note": "接缝看过", "check": list(checklist.MANUAL)},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert page.count("data-reviewed") == 1 and "接缝看过" in page and f"勾过 {len(checklist.MANUAL)} 项" in page
    done_task(store, runtime.data_root, seg2, "video", "outputs/tasks/v3/clip.mp4", sample_mp4() + b"\0")
    assert "data-reviewed" not in client.get(f"/episode/{sample['episode']}/videos").text    # 换了视频要重看


def test_card_lists_every_checklist_item_and_refuses_partial_ticks(client, runtime, store, sample):
    """验收清单(spec 2026-10-23-不穿帮验收-design.md 第 1 节):整张清单都列出来,少勾一项不能确认。"""
    seg2 = _two(store, sample, runtime.data_root)
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert page.count("data-check-item=") == 2 * len(checklist.ITEMS)        # 两段,每段整张清单
    assert page.count("data-check-box") >= 2 * len(checklist.MANUAL)
    r = client.post(f"/segment/{seg2}/review", data={"check": ["identity"]}, follow_redirects=False)
    assert r.status_code == 400 and "人在家具里" in r.text
    assert "data-reviewed" not in client.get(f"/episode/{sample['episode']}/videos").text


def test_vision_check_button_and_result_on_the_card(client, runtime, store, sample):
    """视觉抽检(第 5 项):有场记表才出按钮;结果只认这条视频;对不上的列成提示。"""
    import json

    from tools.llm import runs
    from tools.serve.dispatch import sha256_file
    _two(store, sample, runtime.data_root)
    assert "data-vision-start" not in client.get(f"/episode/{sample['episode']}/videos").text
    entry = {"character": "林小雨", "pose": "跪", "facing": "镜头", "side": "中", "holding": "无"}
    repo.update(store, "segment", sample["seg"], blocking_json=json.dumps([entry], ensure_ascii=False))
    assert "data-vision-start" in client.get(f"/episode/{sample['episode']}/videos").text
    sha = sha256_file(runtime.data_root / repo.get(store, "segment", sample["seg"])["video_path"])
    rid = runs.start(store, task="vision_check", target_type="segment", target_id=sample["seg"], model="m",
                     input_sha="x", llm_mode="real", params={"video_sha256": sha})
    runs.propose(store, rid, {"check_number": "1", "frames": [{"index": 1, "people": [
        {"character": "林小雨", "visible": "是", "pose": "站", "facing": "镜头"}]}]}, "j")
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "data-vision-mismatch" in page and "应当跪,模型看到的是站" in page
    assert "应当跪,模型看到的是站" in client.get(f"/llm/run/{rid}").text
