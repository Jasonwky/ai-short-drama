# -*- coding: utf-8 -*-
import pytest

from tests.serve.fakes import FakeComfy
from tools.drama import repo
from tools.drama.tasks import create_task
from tools.gen.planner import plan_first_frame, plan_video
from tools.serve.dispatch import dispatch, pick_next
from tools.serve.hosts import get_host


def _no_ssh(args, stdin):
    raise AssertionError("这些工作流不该触发远端预检")


@pytest.fixture
def data_root(tmp_path, sample):
    for rel in ("assets/lin.png", "assets/shen.png", "assets/yard.png", "assets/jade.png", "frames/s1.png"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"png")
    return tmp_path


def _task(store, task_id):
    return repo.get(store, "task", task_id)


def test_first_frame_is_uploaded_built_and_queued(store, sample, host_id, data_root):
    tid = plan_first_frame(store, sample["seg"], seed=7)
    comfy = FakeComfy()
    out = dispatch(store, comfy, get_host(store, host_id), _task(store, tid), data_root=data_root, run=_no_ssh)
    t = _task(store, tid)
    assert out.state == "queued" and t["state"] == "queued"
    assert t["external_id"] == out.message and t["host_id"] == host_id
    assert sorted(comfy.uploaded) == sorted((data_root / p).as_posix() for p in
                                            ("assets/lin.png", "assets/yard.png", "assets/jade.png"))
    wf = comfy.submitted[t["external_id"]]
    assert wf["27"]["inputs"]["seed"] == 7 and wf["30"]["inputs"]["width"] == 928
    assert len(out.warnings) == 2          # GGUF 上的 LoRA 无法核验 —— 如实带出来,不吞


def test_video_task_submits_h3_without_audio(store, sample, host_id, data_root):
    tid = plan_video(store, sample["seg"], seed=3)
    comfy = FakeComfy()
    out = dispatch(store, comfy, get_host(store, host_id), _task(store, tid), data_root=data_root, run=_no_ssh)
    wf = comfy.submitted[out.message]
    assert "11" not in wf and wf["19"]["inputs"]["noise_seed"] == 3
    assert comfy.uploaded == [(data_root / "frames/s1.png").as_posix()]


def test_missing_reference_fails_loudly(store, sample, host_id, data_root):
    (data_root / "assets/jade.png").unlink()
    tid = plan_first_frame(store, sample["seg"], seed=1)
    out = dispatch(store, FakeComfy(), get_host(store, host_id), _task(store, tid), data_root=data_root, run=_no_ssh)
    assert out.state == "failed" and "jade.png" in _task(store, tid)["error"]


def test_unreachable_host_leaves_task_pending(store, sample, host_id, data_root):
    tid = plan_first_frame(store, sample["seg"], seed=1)
    comfy = FakeComfy()
    comfy.reachable = False
    out = dispatch(store, comfy, get_host(store, host_id), _task(store, tid), data_root=data_root, run=_no_ssh)
    assert out.state == "pending" and _task(store, tid)["state"] == "pending"


def test_comfy_rejection_fails_with_its_message(store, sample, host_id, data_root):
    tid = plan_first_frame(store, sample["seg"], seed=1)
    comfy = FakeComfy()
    comfy.reject_next_submit = "unet_name not in list"
    dispatch(store, comfy, get_host(store, host_id), _task(store, tid), data_root=data_root, run=_no_ssh)
    t = _task(store, tid)
    assert t["state"] == "failed" and "unet_name not in list" in t["error"]


def test_incompatible_lora_is_never_submitted(store, sample, host_id, data_root, monkeypatch):
    # 把视频任务的工作流换成带 safetensors LoRA 的,模拟闸门 0-6 的那种组合
    import tools.serve.dispatch as d
    monkeypatch.setattr(d, "build_workflow", lambda task, names: {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "h3.safetensors"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "pdd.safetensors", "model": ["1", 0]}}})
    tid = plan_video(store, sample["seg"], seed=1)
    comfy = FakeComfy()
    out = dispatch(store, comfy, get_host(store, host_id), _task(store, tid), data_root=data_root,
                   run=lambda a, s: (3, "adaln_proj: lora 需要 [96768, 2688],底模是 [96768, 8]"))
    assert out.state == "failed" and "形状不兼容" in _task(store, tid)["error"]
    assert comfy.submitted == {}


def test_pick_next_keeps_the_loaded_model(store, sample, host_id, data_root):
    comfy = FakeComfy()
    host = get_host(store, host_id)
    first = plan_first_frame(store, sample["seg"], seed=1)
    video = plan_video(store, sample["seg"], seed=1)
    second = plan_first_frame(store, sample["seg"], seed=2)
    assert pick_next(store, host_id)["id"] == first           # 什么都没派过:按先后
    dispatch(store, comfy, host, _task(store, first), data_root=data_root, run=_no_ssh)
    assert pick_next(store, host_id)["id"] == second          # Qwen 已在显存:插队同模型的
    dispatch(store, comfy, host, _task(store, second), data_root=data_root, run=_no_ssh)
    assert pick_next(store, host_id)["id"] == video


def test_same_named_references_do_not_collide(store, sample, host_id, data_root):
    # 两张参考图同名(不同任务的产物常见)但内容不同 —— 在云机上必须是两个文件
    (data_root / "a").mkdir()
    (data_root / "b").mkdir()
    (data_root / "a/ComfyUI_00001_.png").write_bytes(b"lin")
    (data_root / "b/ComfyUI_00001_.png").write_bytes(b"yard")
    repo.update(store, "character", sample["lin"], turnaround_path="a/ComfyUI_00001_.png")
    repo.update(store, "scene", sample["yard"], image_path="b/ComfyUI_00001_.png")
    tid = plan_first_frame(store, sample["seg"], seed=1)
    comfy = FakeComfy()
    out = dispatch(store, comfy, get_host(store, host_id), _task(store, tid), data_root=data_root, run=_no_ssh)
    wf = comfy.submitted[out.message]
    names = [wf[n]["inputs"]["image"] for n in ("18", "19", "20")]
    assert len(set(names)) == 3 and all(n.startswith("ai-short-drama/") for n in names)


def test_anchor_images_are_uploaded_once_and_wired(store, sample, host_id, data_root):
    """锚点图与参考图是同一张(首帧)时只传一次;只作锚点的图(尾帧)也要传上去。"""
    (data_root / "frames/last.png").write_bytes(b"last")
    tid = create_task(store, kind="video", target_type="segment", target_id=sample["seg"], model="m", seed=1,
                      prompt="<Picture 1> 是首帧", refs=["frames/s1.png", "frames/last.png"],
                      params={"workflow": "minimax_h3", "frames": 192, "size": [768, 1344],
                              "image_refs": ["frames/s1.png"], "anchors": [["frames/s1.png", 0], ["frames/last.png", -1]],
                              "audio_ref": None, "writes": "video_path", "expect_ext": ".mp4"})
    comfy = FakeComfy()
    out = dispatch(store, comfy, get_host(store, host_id), _task(store, tid), data_root=data_root, run=_no_ssh)
    assert out.state == "queued"
    assert sorted(comfy.uploaded) == sorted((data_root / p).as_posix() for p in ("frames/s1.png", "frames/last.png"))
    wf = comfy.submitted[out.message]
    guides = [n for n in wf.values() if n["class_type"] == "MiniMaxH3AddGuide"]
    assert [wf[g["inputs"]["image"][0]]["inputs"]["image"].endswith(name) for g, name in
            zip(guides, ("s1.png", "last.png"))] == [True, True]


def test_missing_anchor_image_fails_loudly(store, sample, host_id, data_root):
    tid = create_task(store, kind="video", target_type="segment", target_id=sample["seg"], model="m", seed=1,
                      prompt="<Picture 1> x", refs=["frames/s1.png"],
                      params={"workflow": "minimax_h3", "frames": 192, "size": [768, 1344],
                              "image_refs": ["frames/s1.png"], "anchors": [["frames/gone.png", -1]],
                              "audio_ref": None, "writes": "video_path", "expect_ext": ".mp4"})
    out = dispatch(store, FakeComfy(), get_host(store, host_id), _task(store, tid), data_root=data_root, run=_no_ssh)
    assert out.state == "failed" and "gone.png" in out.message


def test_region_task_uploads_source_and_ref_and_builds_the_paste_back(store, sample, host_id, data_root):
    from tools.gen.planner import plan_region
    tid = plan_region(store, sample["seg"], "first_frame_path", source="frames/s1.png", size=(928, 1664),
                      box=[100, 200, 200, 300], asset=("character", sample["lin"]), note="", seed=3)
    comfy = FakeComfy()
    out = dispatch(store, comfy, get_host(store, host_id), _task(store, tid), data_root=data_root, run=_no_ssh)
    assert out.state == "queued"
    assert sorted(comfy.uploaded) == sorted((data_root / p).as_posix() for p in ("frames/s1.png", "assets/lin.png"))
    wf = comfy.submitted[out.message]
    assert wf["40"]["inputs"]["image"].endswith("s1.png") and wf["18"]["inputs"]["image"].endswith("lin.png")
    assert (wf["41"]["inputs"]["x"], wf["41"]["inputs"]["y"]) == (64, 160) and wf["29"]["inputs"]["images"] == ["46", 0]


def test_region_task_with_a_missing_source_fails_loudly(store, sample, host_id, data_root):
    from tools.gen.planner import plan_region
    tid = plan_region(store, sample["seg"], "first_frame_path", source="frames/s1.png", size=(928, 1664),
                      box=[0, 0, 400, 400], asset=("character", sample["lin"]), note="", seed=1)
    (data_root / "frames/s1.png").unlink()
    out = dispatch(store, FakeComfy(), get_host(store, host_id), _task(store, tid), data_root=data_root, run=_no_ssh)
    assert out.state == "failed" and "s1.png" in out.message
