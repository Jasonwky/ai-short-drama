# -*- coding: utf-8 -*-
"""假执行面:行为像 ComfyClient 的假云机、假隧道、自动出片的假 GPU。

**只给测试与 Playwright 自测用**。控制台的正式入口(`tools.serve.console`)不接受假后端 ——
对应 CLAUDE.md「不可用就明确报错,不静默切 Fake」。自测在独立的临时 data_root 里跑,假产物碰不到真工程。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from tools.gen.assets import _png
from tools.gen.comfy_client import ComfyClient, ComfyError, ComfyUnreachable

OBJECT_INFO_MIN = Path(__file__).with_name("fixtures") / "object_info_min.json"


class FakeComfy:
    def __init__(self):
        self.reachable = True
        self.reject_next_submit: str | None = None   # 模拟 HTTP 400(工作流本身写错)
        self.uploaded: list[str] = []
        self.submitted: dict[str, dict] = {}
        self.running: list[str] = []
        self.pending: list[str] = []
        self.records: dict[str, dict] = {}
        self.files: dict[str, bytes] = {}
        self._n = 0
        self.vram_gb = 20.0          # 剩余显存
        self.freed = 0               # 被要求释放模型的次数
        self.free_releases = True    # False:占显存的不是 ComfyUI,释放了也没用

    # ── ComfyClient 的接口 ──────────────────────────
    def _up(self):
        if not self.reachable:
            raise ComfyUnreachable("fake: 连不上 ComfyUI")

    def upload_image(self, path, subfolder=""):
        self._up()
        self.uploaded.append(Path(path).as_posix())
        return f"{subfolder}/{Path(path).name}" if subfolder else Path(path).name

    def submit(self, workflow):
        self._up()
        if self.reject_next_submit:
            msg, self.reject_next_submit = self.reject_next_submit, None
            raise ComfyError(f"/prompt 返回 HTTP 400:{msg}")
        self._n += 1
        pid = f"p{self._n}"
        self.submitted[pid] = workflow
        self.pending.append(pid)
        return pid

    def queue_ids(self):
        self._up()
        return list(self.running), list(self.pending)

    def vram_free_gb(self):
        self._up()
        return self.vram_gb

    def object_info(self):
        """真实节点清单的瘦身版(2026-10-08 抓自云机,只留我们会用到的节点)。"""
        self._up()
        return json.loads(OBJECT_INFO_MIN.read_text(encoding="utf-8"))

    def system_stats(self):
        self._up()
        return {"system": {"comfyui_version": "0.34.0", "required_frontend_version": "1.51.9"},
                "devices": [{"name": "fake", "vram_free": int(self.vram_gb * 2 ** 30)}]}

    def free_models(self):
        self._up()
        self.freed += 1
        if self.free_releases:
            self.vram_gb = 20.0

    def history(self, prompt_id):
        self._up()
        return self.records.get(prompt_id)

    def outputs(self, record):
        return ComfyClient.outputs(self, record)

    def fetch(self, item, dest_dir):
        self._up()
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / item["filename"]
        out.write_bytes(self.files[item["filename"]])
        return out

    # ── 测试操控 ────────────────────────────────────
    def start(self, pid):
        self.pending.remove(pid)
        self.running.append(pid)

    def finish(self, pid, filename, data=b"generated"):
        for q in (self.pending, self.running):
            if pid in q:
                q.remove(pid)
        self.files[filename] = data
        self.records[pid] = {"status": {"status_str": "success", "completed": True},
                             "outputs": {"9": {"images": [{"filename": filename, "subfolder": "", "type": "output"}]}}}

    def fail(self, pid, message):
        for q in (self.pending, self.running):
            if pid in q:
                q.remove(pid)
        self.records[pid] = {"status": {"status_str": "error", "completed": False,
                                        "messages": [["execution_error", {"exception_message": message}]]},
                             "outputs": {}}

    def vanish(self, pid):
        """任务从云机消失:不在队列、也不在 history(比如 ComfyUI 被重启过)。"""
        for q in (self.pending, self.running):
            if pid in q:
                q.remove(pid)


class FakeTunnel:
    def __init__(self, up=True, owned=True):
        self.up, self.owned, self.stopped = up, owned, []

    def ensure(self, host):
        return self.up

    def owns(self, host_id):
        return self.owned

    def stop(self, host_id):
        self.stopped.append(host_id)


def sample_mp4(seconds: float = 1) -> bytes:
    """seconds 秒(默认 1)384x672 的测试图样 mp4(带音轨,和 H3 产物一样),用本机 ffmpeg 现生成。没有 ffmpeg 就报错,不假装。"""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("假 GPU 要用 ffmpeg 生成可播放的样片,本机找不到 ffmpeg")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "sample.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=384x672:rate=24",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=32000", "-t", f"{seconds:g}",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", str(out)], check=True)
        return out.read_bytes()


class FakeGpu:
    """把假云机队列里的任务一次跑完:H3(工作流里有 noise_seed)出 mp4,其余出 png。
    demo:自测给的真产物 {"videos": [mp4…], "frames": [竖版 png…], "fourview": 横版 png} —— 轮流交出去,
    页面上看到的是真画面(用户 2026-10-28:自测别用彩条 / 灰图);没给就现造测试图样(单测用)。"""
    def __init__(self, comfy: FakeComfy, demo: dict | None = None):
        self.comfy = comfy
        self.demo = demo
        self._png = _png(96, 160, 90)
        self._mp4: bytes | None = None
        self._n = {"videos": 0, "frames": 0}

    def _next(self, kind: str) -> bytes:
        items = self.demo[kind]
        self._n[kind] += 1
        return items[(self._n[kind] - 1) % len(items)]

    def _image(self, graph: dict) -> bytes:
        if not self.demo:
            return self._png
        size = next(((n["inputs"]["width"], n["inputs"]["height"]) for n in graph.values()
                     if isinstance(n["inputs"].get("width"), int) and isinstance(n["inputs"].get("height"), int)), (0, 1))
        return self.demo["fourview"] if size[0] > size[1] else self._next("frames")    # 横版的是四视图

    def run_pending(self) -> int:
        done = 0
        for pid in list(self.comfy.pending) + list(self.comfy.running):
            graph = self.comfy.submitted[pid]
            is_h3 = any("noise_seed" in n["inputs"] for n in graph.values())
            if is_h3:
                if self.demo:
                    self.comfy.finish(pid, f"{pid}.mp4", self._next("videos"))
                else:
                    self._mp4 = self._mp4 or sample_mp4()
                    self.comfy.finish(pid, f"{pid}.mp4", self._mp4)
            else:
                self.comfy.finish(pid, f"{pid}.png", self._image(graph))
            done += 1
        return done


class FakeAligner:
    """假的云机对齐脚本(行为像 ssh runner):把 `heard` 这段话的字均匀摊在 0.1–0.9 s 上,输出与真脚本同格式的 JSON。
    默认什么也没听见 —— 判卷会因命中率为 0 判失败;测试 / 自测按段落台词设置 `heard`。"""
    def __init__(self, heard: str = ""):
        self.heard = heard
        self.calls: list[list[str]] = []

    def __call__(self, args, stdin):
        self.calls.append(list(args))
        n = len(self.heard)
        chars = [[c, round(0.1 + 0.8 * i / n, 3), round(0.1 + 0.8 * (i + 1) / n, 3)] for i, c in enumerate(self.heard)]
        return 0, "Loading weights ...\n" + json.dumps({"chars": chars, "asr_sec": 0.1}, ensure_ascii=False)


class FakeVoice:
    """假的云机配音脚本(行为像 ssh runner):回一段 0.5 秒静音 wav,与真脚本(`voice_remote`)同格式的 JSON。"""
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args, stdin):
        import base64
        import io
        import wave
        self.calls.append(list(args))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\0\0" * 12000)
        return 0, "Loading weights ...\n" + json.dumps(
            {"wav_b64": base64.b64encode(buf.getvalue()).decode(), "sr": 24000, "sec": 0.5, "gen_sec": 0.1})


class FakeTts:
    """假的云机台词合成脚本(行为像 ssh runner):每句 1 秒静音,与真脚本(`tts_remote`)同格式的 JSON(含每句起止)。"""
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args, stdin):
        import base64
        import io
        import wave
        self.calls.append(list(args))
        job = json.loads(args[1])
        n = len(job["lines"])
        sec = job["lead"] + n + job["gap"] * max(n - 1, 0) + job["tail"]
        spans = [[round(job["lead"] + k * (1 + job["gap"]), 3), round(job["lead"] + k * (1 + job["gap"]) + 1, 3)]
                 for k in range(n)]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\0\0" * int(24000 * sec))
        return 0, json.dumps({"wav_b64": base64.b64encode(buf.getvalue()).decode(), "sr": 24000, "sec": round(sec, 3),
                              "spans": spans, "gen_sec": 0.1})


def fake_tts_runner():
    """假后端的配音 ssh:按送过去的脚本分派 —— 台词合成脚本走 FakeTts,试听音脚本走 FakeVoice。"""
    voice, lines = FakeVoice(), FakeTts()

    def run(args, stdin):
        clone = b"generate_voice_clone" in stdin or b"IndexTTS2" in stdin    # 台词合成:Qwen3 克隆 / IndexTTS2 港片念法
        return (lines if clone else voice)(args, stdin)
    run.voice, run.lines = voice, lines
    return run


def lip_measure_following_audio(path):
    """假后端的口型量法:画面里一张脸,嘴随音量开合。假 GPU 的视频不是照台词音频生成的(演示图 / 测试图样),
    真量它的口型没有意义 —— 自测用它走通口型闸门放行的那条路;判不过、自动重出由单测覆盖(tests/serve/test_lipgate.py)。"""
    import math
    from tools.cut import media
    fps = 24.0
    n = int(media.probe(Path(path)).duration * fps)
    env = [0.05 + 0.04 * math.sin(2 * math.pi * t / 7) for t in range(n)]   # 测试图样的音轨是恒定正弦,没有起伏可跟:自造一段
    return fps, [[(0.5, 0.02 + 0.5 * e)] for e in env], env


def fake_backend(demo: dict | None = None):
    """(Backend, FakeComfy):界面上标明「假后端」;每轮 tick 前假 GPU 把队列跑完。LoRA 预检一律放行(假后端没有模型文件)。
    LLM 用假 LLM:产物标 fake,永远过不了审批。"""
    from tools.llm.fake import FakeLLM
    from tools.llm.service import DEFAULT_MODEL, LLMBackend
    from tools.serve.runtime import Backend   # 延迟导入:runtime → worker → … 不依赖本模块,避免环
    comfy = FakeComfy()
    gpu = FakeGpu(comfy, demo)
    aligner = FakeAligner()
    tts = fake_tts_runner()
    backend = Backend("假后端(测试专用)", lambda host: comfy, FakeTunnel(), lambda host: (lambda args, stdin: (0, "")),
                      fake=True, before_tick=gpu.run_pending,
                      llm=lambda: LLMBackend(FakeLLM(), DEFAULT_MODEL, "fake"), align_for=lambda host: aligner,
                      tts_for=lambda host: tts, idx_for=lambda host: tts)
    return backend, comfy
