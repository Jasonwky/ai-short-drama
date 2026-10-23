# -*- coding: utf-8 -*-
from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.serve.hosts import add_host


def _ready_for_frames(store, sample, root):
    for rel in ("assets/lin.png", "assets/yard.png", "assets/jade.png"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"png")
    repo.update(store, "segment", sample["seg"], first_frame_path=None, video_path=None)
    add_host(store, label="fake", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k", local_port=8188,
             vram_gb=24.0, models_dir="/m")


def test_frame_then_video_through_the_fake_gpu(client, runtime, store, sample):
    _ready_for_frames(store, sample, runtime.data_root)
    client.post(f"/segment/{sample['seg']}/frame", data={"seed": "42"})
    runtime.tick_all()        # 派发
    runtime.tick_all()        # 假 GPU 跑完 → 收取 → 写回
    seg = repo.get(store, "segment", sample["seg"])
    assert (runtime.data_root / seg["first_frame_path"]).read_bytes()[:4] == b"\x89PNG"
    assert f'src="/media/{seg["first_frame_path"]}"' in client.get(f"/episode/{sample['episode']}/frames").text

    client.post(f"/segment/{sample['seg']}/video", data={"seed": "43"})
    runtime.tick_all()
    runtime.tick_all()
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["video_path"].endswith(".mp4")
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert f'<video src="/media/{seg["video_path"]}"' in page and "当前版" in page


def test_video_refused_without_first_frame_shows_reason(client, store, sample):
    repo.update(store, "segment", sample["seg"], first_frame_path=None)
    r = client.post(f"/segment/{sample['seg']}/video", data={"seed": "1"})
    assert r.status_code == 400 and "首帧" in r.text
    assert tasks_for(store, "segment", sample["seg"]) == []


def test_videos_panel_shows_estimate_and_host_state(client, store, sample):
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "无法估时:还没登记云机" in page and "还没有登记云机" in page
    add_host(store, label="a", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k", local_port=8188,
             vram_gb=24.0, models_dir="/m", gpu="RTX 4090")
    assert "预计生成约 15 分钟(按 RTX 4090 估算)" in client.get(f"/episode/{sample['episode']}/videos").text


def test_frame_drawn_from_an_older_shot_design_is_flagged(client, store, sample, runtime):
    """首帧任务记了当时的拼句;段落的图提示词改过 → 提示;改回去 → 消失;没有快照的旧首帧不提示。"""
    from tools.drama.tasks import create_task, set_task_state
    from tools.gen import planner
    seg = sample["seg"]
    rel = "frames/old.png"
    (runtime.data_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (runtime.data_root / rel).write_bytes(b"png")
    repo.update(store, "segment", seg, first_frame_path=rel)
    url = f"/episode/{sample['episode']}/frames"

    def done(params):
        t = create_task(store, kind="image", target_type="segment", target_id=seg, model="m", params=params,
                        seed=1, prompt="p", refs=[])
        set_task_state(store, t, "queued", external_id=f"p{t}")
        set_task_state(store, t, "done", result_path=rel, result_sha256="x")

    done({"writes": "first_frame_path"})                          # 没快照
    repo.update(store, "segment", seg, image_prompt="改过的首帧句")
    assert "data-frame-stale" not in client.get(url).text
    repo.update(store, "segment", seg, image_prompt="旧方案的句子")
    done({"writes": "first_frame_path", "frame": {"text": planner.frame_text(store, seg, "first_frame_path")}})
    assert "data-frame-stale" not in client.get(url).text
    repo.update(store, "segment", seg, image_prompt="镜头改了")
    assert "data-frame-stale" in client.get(url).text
    repo.update(store, "segment", seg, image_prompt="旧方案的句子")
    assert "data-frame-stale" not in client.get(url).text
