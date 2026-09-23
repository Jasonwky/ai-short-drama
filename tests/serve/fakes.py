# -*- coding: utf-8 -*-
"""行为像 ComfyClient 的假云机:队列、history、产物、断线都可由测试操控。"""
from __future__ import annotations

from pathlib import Path

from tools.gen.comfy_client import ComfyClient, ComfyError, ComfyUnreachable


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
