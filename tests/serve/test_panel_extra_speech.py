# -*- coding: utf-8 -*-
"""视频面板的「台词外多说」提醒:只认当前视频的对齐(设计 specs/2026-10-10-台词外多说提示-design.md §二、§三)。"""
import json
from pathlib import Path

from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state
from tools.serve import aligner

REAL = json.loads((Path(__file__).parents[1] / "cut/fixtures/align_real.json").read_text(encoding="utf-8"))["runs"]


def _aligned(store, root, seg, video, run, state="done"):
    t = create_task(store, kind="align", target_type="segment", target_id=seg, model="whisper", seed=0, prompt="p",
                    params={"workflow": "whisper_align", "video": video, "lines": run["lines"],
                            "writes": "subtitle_path"}, refs=[video])
    out = Path(root) / "outputs/tasks" / str(t)
    out.mkdir(parents=True, exist_ok=True)
    (out / "align.json").write_text(json.dumps({"lines": run["lines"], "chars": run["chars"],
                                                "envelope": run["envelope"]}, ensure_ascii=False), encoding="utf-8")
    if state == "done":
        set_task_state(store, t, "queued", external_id=f"align-{t}")
        set_task_state(store, t, "done", result_path=f"outputs/tasks/{t}/subtitles.srt", result_sha256="x")
    else:
        set_task_state(store, t, "failed", error="判卷不过:…")
    return t


def test_extra_speech_for_the_current_video(store, sample, tmp_path):
    _aligned(store, tmp_path, sample["seg"], "videos/s1.mp4", REAL["2026-09-29"])
    got = aligner.extra_speech_for(store, tmp_path, sample["seg"])
    assert got == {"runs": [{"text": "林用普通话说", "start": 0.0, "end": 4.76}], "problem": ""}


def test_failed_alignment_still_counts_and_latest_wins(store, sample, tmp_path):
    _aligned(store, tmp_path, sample["seg"], "videos/s1.mp4", REAL["2026-09-29"])
    _aligned(store, tmp_path, sample["seg"], "videos/s1.mp4", REAL["2026-10-09"], state="failed")
    assert aligner.extra_speech_for(store, tmp_path, sample["seg"]) == {"runs": [], "problem": ""}


def test_alignment_of_an_old_video_is_ignored(store, sample, tmp_path):
    _aligned(store, tmp_path, sample["seg"], "videos/s1.mp4", REAL["2026-09-29"])
    repo.update(store, "segment", sample["seg"], video_path="videos/s1b.mp4")       # 换了视频
    assert aligner.extra_speech_for(store, tmp_path, sample["seg"]) is None
    repo.update(store, "segment", sample["seg"], video_path=None)
    assert aligner.extra_speech_for(store, tmp_path, sample["seg"]) is None


def test_videos_panel_warns_without_blocking(client, runtime, store, sample):
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "data-extra-speech" not in page
    _aligned(store, runtime.data_root, sample["seg"], "videos/s1.mp4", REAL["2026-09-29"])
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "人物在台词外多说了:「林用普通话说」(0.0–4.8 秒)" in page and "建议换种子重出视频" in page
    assert "data-generate-video" in page                                          # 只提示、不拦


def test_videos_panel_says_when_it_cannot_judge(client, runtime, store, sample):
    bad = {**REAL["2026-10-09"], "lines": ["今天天气真不错"]}
    _aligned(store, runtime.data_root, sample["seg"], "videos/s1.mp4", bad, state="failed")
    assert "识别与台词对不上,无法判断是否多说" in client.get(f"/episode/{sample['episode']}/videos").text
