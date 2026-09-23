# -*- coding: utf-8 -*-
"""ComfyUI HTTP 客户端 —— 只用标准库,不引三方依赖。

**为什么不直接用 requests**:云机上 ComfyUI 的 venv 是它自己的,
我们的代码要能在控制面和云机两边都跑起来,少一个依赖少一分环境问题。

**三条坑是别人真机踩出来的**(见 docs/superpowers/spike-results/2026-09-23-云机环境勘察与模型部署.md),
不是理论问题,这里逐条处理:

1. `/history/<id>` 在 ComfyUI 0.26+ **有时返回空**。只查它会一直轮询到超时,
   必须再查一次全量 `/history` 兜底。
2. **队列已空但任务不在 history**:多进程堆积或版本差异下会出现
   running=0 / pending=0 却查不到任务。持续 `IDLE_GIVEUP_SEC` 即认为已结束,
   直接去 output 找文件,而不是傻等超时。
3. `client_id` **不只是个标识**:ComfyUI 的进度是按提交那条 prompt 的 client_id
   **定向投递**的,不是广播。谁提交谁监听必须同一个 id,否则握手成功、
   `/queue` 正常,却一条进度都收不到。
"""
from __future__ import annotations

import http.client
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

DEFAULT_SERVER = "127.0.0.1:8188"

# 队列空但 history 未捕获任务时,等多久就认定任务已结束(秒)
IDLE_GIVEUP_SEC = 90

# 等待期间连续连不上多久才放弃盯梢(秒)。云机上的任务不会因为我们断线而停 ——
# 盯梢的人一走,产物就成了孤儿。闸门 0-4 实测:一次瞬时抖动让执行器崩掉,
# 云机上那条 H3 照样跑完,产物没人取、溯源没人写。
UNREACHABLE_BUDGET_SEC = 600

# 瞬时网络异常:读响应超时、连接被重置、协议层异常。它们**不是** URLError,
# 不显式接住就会穿透成未处理异常。
_TRANSIENT = (TimeoutError, ConnectionError, http.client.HTTPException)


class ComfyError(RuntimeError):
    """任务层面的失败。"""


class ComfyUnreachable(ComfyError):
    """连不上服务。单独成类 —— 它的处理方式和「任务失败」完全不同:
    前者可恢复(机器没起来/隧道断了),后者要人看错误。"""


class ComfyClient:
    def __init__(self, server: str = DEFAULT_SERVER, timeout: int = 30,
                 client_id: str | None = None):
        self.base = f"http://{server}"
        self.timeout = timeout
        self.client_id = client_id or str(uuid.uuid4())

    # ---------- 底层 ----------

    def _request(self, path: str, payload: dict | None = None) -> dict:
        url = self.base + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(
            url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:800]
            raise ComfyError(f"{path} 返回 HTTP {e.code}:{detail}") from e
        except (urllib.error.URLError, *_TRANSIENT) as e:
            raise ComfyUnreachable(
                f"连不上 ComfyUI({self.base}):{type(e).__name__}: {e}。确认服务已启动、隧道已连通。") from e

    # ---------- 健康 ----------

    def system_stats(self) -> dict:
        return self._request("/system_stats")

    def queue_counts(self) -> tuple[int, int]:
        """(running, pending)。**这是判活的第二层** —— ssh 进程活着不代表转发通,
        能拿到队列数才算真的通。"""
        q = self._request("/queue")
        return len(q.get("queue_running", [])), len(q.get("queue_pending", []))

    # ---------- 素材上传 ----------

    def upload_image(self, path: str | Path, subfolder: str = "") -> str:
        """把本地图片传到 ComfyUI 的 input 目录,返回 LoadImage 该填的文件名。

        `LoadImage` 吃的是**服务端 input 目录下的文件名**,不是本地路径 ——
        参考图必须先上传,否则工作流报找不到文件。
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"参考图不存在:{path}")

        boundary = f"----comfy{uuid.uuid4().hex}"
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts: list[bytes] = []

        def field(name: str, value: str) -> None:
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n".encode("utf-8"))

        field("overwrite", "true")
        if subfolder:
            field("subfolder", subfolder)
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode("utf-8"))
        parts.append(path.read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

        req = urllib.request.Request(
            self.base + "/upload/image", data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 120)) as resp:
                info = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise ComfyUnreachable(f"上传失败({self.base}):{e}") from e

        name = info.get("name") or path.name
        sub = info.get("subfolder") or ""
        return f"{sub}/{name}" if sub else name

    # ---------- 提交与轮询 ----------

    def submit(self, workflow: dict) -> str:
        """提交 API 格式工作流,返回 prompt_id。

        ⚠️ ComfyUI 对**校验失败**返回 HTTP 400 且 body 里带 `node_errors` ——
        那是工作流本身写错了(缺输入、模型名不存在),不是运行时错误,
        应当立刻暴露给人,而不是重试。
        """
        res = self._request("/prompt", {"prompt": workflow, "client_id": self.client_id})
        pid = res.get("prompt_id")
        if not pid:
            raise ComfyError(f"提交未返回 prompt_id:{json.dumps(res)[:500]}")
        return pid

    def history(self, prompt_id: str) -> dict | None:
        """查一条任务的 history。**两级兜底**,见模块文档串第 1 条。
        连不上(ComfyUnreachable)原样抛出 —— 「查不到」和「连不上」是两回事,不能混成 None。"""
        h = self._request(f"/history/{prompt_id}")
        if h.get(prompt_id):
            return h[prompt_id]
        # 兜底:全量 history。0.26+ 上单条查询偶尔返回空。
        return self._request("/history").get(prompt_id)

    def wait(self, prompt_id: str, timeout_sec: int = 7200,
             poll_sec: float = 5.0, on_tick=None) -> dict:
        """等一条任务结束。返回 history 里的记录。

        **不靠单一信号判结束**:history 有记录就算完;
        若队列已空且 history 一直查不到,持续 IDLE_GIVEUP_SEC 后按「已结束」处理
        并抛出带说明的错误,由调用方去 output 目录捞 —— 而不是傻等到 timeout。
        """
        started = time.time()
        idle_since: float | None = None
        down_since: float | None = None
        while True:
            try:
                rec = self.history(prompt_id)
                running, pending = self.queue_counts() if rec is None else (0, 0)
                down_since = None
            except ComfyUnreachable as e:
                # 断线不放弃:任务还在云机上跑。预算内持续重试,超预算才 fail-loud。
                down_since = down_since or time.time()
                if time.time() - down_since > UNREACHABLE_BUDGET_SEC:
                    raise ComfyUnreachable(
                        f"连续 {UNREACHABLE_BUDGET_SEC}s 连不上,放弃盯梢 {prompt_id}。"
                        f"任务可能仍在云机上运行 —— 用 prompt_id 接管,不要重新提交。最后错误:{e}") from e
                if on_tick:
                    on_tick(int(time.time() - started), -1, -1)
                time.sleep(poll_sec)
                continue
            if rec is not None:
                status = (rec.get("status") or {})
                if status.get("status_str") == "error" or status.get("completed") is False:
                    msgs = json.dumps(status.get("messages", []), ensure_ascii=False)[:800]
                    raise ComfyError(f"任务失败 {prompt_id}:{msgs}")
                return rec

            if running == 0 and pending == 0:
                idle_since = idle_since or time.time()
                if time.time() - idle_since > IDLE_GIVEUP_SEC:
                    raise ComfyError(
                        f"队列已空但 history 查不到 {prompt_id}(空转 {IDLE_GIVEUP_SEC}s)。"
                        f"任务可能已结束,去 output 目录核对产物。")
            else:
                idle_since = None

            if time.time() - started > timeout_sec:
                raise ComfyError(f"等待超时 {timeout_sec}s:{prompt_id}(running={running} pending={pending})")

            if on_tick:
                on_tick(int(time.time() - started), running, pending)
            time.sleep(poll_sec)

    # ---------- 取回 ----------

    def outputs(self, record: dict) -> list[dict]:
        """从 history 记录里抽出所有产物描述 {filename, subfolder, type}。"""
        found: list[dict] = []
        for node_out in (record.get("outputs") or {}).values():
            for key in ("images", "videos", "gifs", "audio"):
                for item in node_out.get(key, []) or []:
                    if item.get("filename"):
                        found.append(item)
        return found

    def fetch(self, item: dict, dest_dir: str | Path) -> Path:
        """下载一个产物到本地。"""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        q = urllib.parse.urlencode({
            "filename": item["filename"],
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        })
        url = f"{self.base}/view?{q}"
        try:
            with urllib.request.urlopen(url, timeout=max(self.timeout, 300)) as resp:
                data = resp.read()
        except urllib.error.URLError as e:
            raise ComfyUnreachable(f"取回产物失败:{e}") from e
        out = dest_dir / item["filename"]
        out.write_bytes(data)
        return out
