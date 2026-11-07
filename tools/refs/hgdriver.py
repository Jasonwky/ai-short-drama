# -*- coding: utf-8 -*-
"""红果网页录屏的真 Driver(`recorder.Driver`)与扫码登录:Playwright **有头**(录屏不在「后台抓取可无头」的例外里)。

依据调研里跑通的脚本(spike-results/2026-11-07-红果短剧内容获取实测.md):
- 浏览器参数 `--auto-accept-this-tab-capture`(标签页录制不弹选择框)、`--autoplay-policy=no-user-gesture-required`,
  去掉 Playwright 默认的 `--mute-audio`(否则录不到声音);登录态存在独占的配置目录(`outputs/refs/profile/`);
- 标签页录制:注入一个按钮点一下(getDisplayMedia 要用户激活)→ `getDisplayMedia({audio: true, preferCurrentTab: true})`
  → MediaRecorder(vp9 + opus,4 Mbps);录出来分 1 MB 一块 base64 取回;
- 没登录时播放页会弹 `.web-login-scan-code__content`(加载后约 10 秒、试着播放后才出来,所以等 15 秒);
- 播完一集网站自己跳到下一集(地址变成 `/player/<剧>/<集>`)。

**robots 口径**:只 `goto` `/player/<剧号>`(robots 允许,`player_url` 只造得出这种地址);`/player/<剧号>/<集号>` 是 robots 禁止的,
**绝不 goto** —— 切集只像人一样点页面「选集」里的集号(或等网站自己跳)。

页面结构(class 名带哈希,不依赖 class)是按 2026-11-07 的网页写的假设,Task 10 真机手工核对:见 `PICK_EPISODE_JS` 等处注释。
"""
from __future__ import annotations

import base64
import math
import re
import time
from pathlib import Path

from tools.refs.sources.hongguo import BASE

OVERLAY = ".web-login-scan-code__content"       # 登录浮层(调研实测)
LOCK_WAIT = 30         # 秒:30 秒拿不到片长 = 没加载出来(网慢或页面变了),任务失败;不当带锁
CLICK_WAIT = 20        # 秒:点了集号之后等地址里出现集号
CHUNK = 1 << 20        # 录屏分块取回的大小


def player_url(series_id: str) -> str:
    """程序唯一会打开的地址:`/player/<剧号>`(robots 允许)。集号地址造不出来。"""
    if not re.fullmatch(r"\d+", series_id or ""):
        raise ValueError(f"剧号应为纯数字(收到「{series_id}」)—— 程序只打开 /player/<剧号>")
    return f"{BASE}/player/{series_id}"


def launch_kwargs() -> dict:
    return {"headless": False,                                         # 有头:录屏不在无头例外内
            "viewport": {"width": 1280, "height": 900},
            "args": ["--auto-accept-this-tab-capture", "--autoplay-policy=no-user-gesture-required"],
            "ignore_default_args": ["--mute-audio"]}


def record_deadline(duration: float) -> float:
    return duration + 30


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def logged_out(overlay: bool, text: str) -> bool:
    return overlay or "扫码登录" in _squash(text)


def lock_text(text: str) -> bool:
    return "扫码进入App端" in _squash(text)


# 注入录制按钮:点它(用户激活)才开得了 getDisplayMedia。录制随 <video> 的 playing 开始、ended 停(不靠轮询掐头尾,时长才对得上)。
CAPTURE_JS = """() => {
  window.__refsState = 'arming'; window.__refsErr = ''; window.__refsChunks = []; window.__refsBlob = null;
  const b = document.createElement('button');
  b.id = 'refs-rec-btn'; b.textContent = '录';
  b.style = 'position:fixed;left:0;top:0;z-index:2147483647;width:24px;height:24px';
  document.body.appendChild(b);
  b.onclick = async () => {
    b.remove();
    try {
      const v = document.querySelector('video');
      if (!v) throw new Error('页面里没有 <video>');
      const s = (window.__refsStream && window.__refsStream.active) ? window.__refsStream
        : await navigator.mediaDevices.getDisplayMedia({video: {frameRate: 30}, audio: true, preferCurrentTab: true, selfBrowserSurface: 'include'});
      window.__refsStream = s;   // 同一页面里后面几集接着用这一路流
      window.__refsTracks = s.getTracks().map(t => t.kind + ':' + t.label);
      const r = new MediaRecorder(s, {mimeType: 'video/webm;codecs=vp9,opus', videoBitsPerSecond: 4000000});
      r.ondataavailable = e => { if (e.data.size) window.__refsChunks.push(e.data); };
      r.onstop = () => { window.__refsBlob = new Blob(window.__refsChunks, {type: 'video/webm'}); window.__refsState = 'stopped'; };
      // 不许 stop 采集轨道:2026-11-07 真机实测,停掉「录当前标签页」的轨道 Chrome 会把这个标签页连同浏览器一起关掉
      // 缓冲卡住时暂停录制、接着播再续上(真机 2026-11-07:两次各约 9 秒的缓冲让第 1 集录成 80.6 秒,片长 61.6 秒)
      v.addEventListener('playing', () => {
        if (window.__refsState === 'armed') { r.start(1000); window.__refsState = 'recording'; }
        else if (r.state === 'paused') { r.resume(); }
      });
      v.addEventListener('waiting', () => { if (r.state === 'recording') r.pause(); });
      v.addEventListener('ended', () => { if (r.state !== 'inactive') r.stop(); });
      window.__refsV = v; window.__refsRec = r; window.__refsState = 'armed';
    } catch (e) { window.__refsErr = String(e); window.__refsState = 'error'; }
  };
}"""

# 从头播,返回片长、画面在网页上的矩形(CSS 像素)与视口。<video> 默认按比例缩放留黑边(contain):算出真正的画面区域;
# object-fit 是 cover / fill 时画面铺满元素,就用元素矩形。
START_JS = """() => {
  const v = window.__refsV;
  v.pause(); v.muted = false; v.volume = 1; v.currentTime = 0;
  const r = v.getBoundingClientRect();
  let box = [r.x, r.y, r.width, r.height];
  const fit = getComputedStyle(v).objectFit;
  if (fit !== 'cover' && fit !== 'fill' && v.videoWidth && v.videoHeight) {
    const k = Math.min(r.width / v.videoWidth, r.height / v.videoHeight);
    const w = v.videoWidth * k, h = v.videoHeight * k;
    box = [r.x + (r.width - w) / 2, r.y + (r.height - h) / 2, w, h];
  }
  const p = v.play(); if (p) p.catch(e => { window.__refsErr = String(e); });
  return {duration: v.duration, box: box, viewport: [window.innerWidth, window.innerHeight]};
}"""

STATE_JS = """() => ({state: window.__refsState, err: window.__refsErr, tracks: window.__refsTracks || [],
  ended: !!(window.__refsV && window.__refsV.ended), gone: !!(window.__refsV && !window.__refsV.isConnected),
  t: window.__refsV ? window.__refsV.currentTime : 0})"""

FORCE_STOP_JS = "() => { const r = window.__refsRec; if (r && r.state !== 'inactive') r.stop(); }"

CHUNK_JS = """async ([o, n]) => { const buf = await window.__refsBlob.slice(o, o + n).arrayBuffer();
  let s = ''; const u = new Uint8Array(buf);
  for (let i = 0; i < u.length; i += 32768) s += String.fromCharCode(...u.subarray(i, i + 32768));
  return btoa(s); }"""

# 「选集」:页面里文字恰好是「选集」的叶子元素,往上逐层找最近的、含文字恰好等于 n 的可见叶子元素的容器;
# 找到就给它打 data-refs-ep 标记,由 Playwright 真点(鼠标事件,和人一样)。只点,不改地址。
# 假设(Task 10 核对):集号列表与「选集」标题在同一个不超过 6 层的祖先里;前 5 集在默认展开的第一段(1-15)里。
PICK_EPISODE_JS = """(n) => {
  document.querySelectorAll('[data-refs-ep]').forEach(e => e.removeAttribute('data-refs-ep'));
  const leaf = e => e.children.length === 0;
  const label = [...document.querySelectorAll('body *')].find(e => leaf(e) && e.textContent.trim() === '选集');
  if (!label) return 'no-label';
  let box = label.parentElement;
  for (let up = 0; box && up < 6; up++, box = box.parentElement) {
    const hit = [...box.querySelectorAll('*')].find(e => leaf(e) && e.textContent.trim() === String(n) && e.getClientRects().length > 0);
    if (hit) { hit.setAttribute('data-refs-ep', String(n)); return 'ok'; }
  }
  return 'no-episode';
}"""

TEXT_JS = "() => ({overlay: !!document.querySelector('" + OVERLAY + "'), text: document.body ? document.body.innerText : ''})"
DURATION_JS = "() => { const v = document.querySelector('video'); return v && isFinite(v.duration) ? v.duration : 0; }"
TRY_PLAY_JS = "() => { const v = document.querySelector('video'); if (v) { const p = v.play(); if (p) p.catch(() => {}); } }"


SESSION_COOKIES = ("sessionid", "sid_tt")    # 2026-11-07 实测:登录过的配置目录有这些,新目录只有 ttwid / passport_csrf_token
OPEN_LOGIN_JS = """() => { const b = [...document.querySelectorAll('button')].find(e => (e.innerText || '').trim() === '登录');
  if (b) { b.click(); return true; } return false; }"""


def has_session(cookie_names) -> bool:
    """登录与否只看登录 cookie(页面现象不可靠:没登录时视频也能先播几秒、登录框 20 多秒才弹)。"""
    return any(n in SESSION_COOKIES for n in cookie_names)


def session_of(ctx) -> bool:
    return has_session(c["name"] for c in ctx.cookies() if "hongguo" in c["domain"] or c["domain"].lstrip(".") in ("hongguoduanju.com",))


class PlaywrightDriver:
    """一部剧一个实例:open 时才起浏览器,close 关掉。配置目录独占(录屏与扫码登录不能同时开,Runtime 管)。"""

    def __init__(self, profile: Path):
        self.profile = Path(profile)
        self._pw = self._ctx = self.page = None

    def open(self, series_id: str) -> None:
        from playwright.sync_api import sync_playwright
        url = player_url(series_id)
        self.profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(str(self.profile), **launch_kwargs())
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)      # 唯一一次 goto:/player/<剧号>
        self.page.wait_for_timeout(3000)

    def logged_in(self) -> bool:
        return session_of(self._ctx)                 # 只认登录 cookie(见 has_session)

    def go_episode(self, n: int, vid: str) -> None:
        if vid in self.page.url:                     # 上一集播完网站自己跳过来了
            return
        r = self.page.evaluate(PICK_EPISODE_JS, n)
        if r != "ok":
            raise RuntimeError(f"点不到第 {n} 集(页面里{'找不到「选集」' if r == 'no-label' else f'「选集」附近没有集号 {n}'})")
        # 点集号会整页跳到 /player/<剧>/<集>(网站自己跳,不是我们 goto);Playwright 默认等这次跳转加载完,
        # 真机 2026-11-07 超过 10 秒就报超时 —— 不让它等,由下面按地址里的集号自己等
        self.page.click("[data-refs-ep]", timeout=10000, no_wait_after=True)
        end = time.monotonic() + CLICK_WAIT
        while vid not in self.page.url:
            if time.monotonic() >= end:
                raise RuntimeError(f"点不到第 {n} 集:点了 {CLICK_WAIT} 秒,地址里还没有集号 {vid}")
            self.page.wait_for_timeout(500)
        self.page.wait_for_load_state("domcontentloaded", timeout=CLICK_WAIT * 1000)
        self.page.wait_for_timeout(3000)

    def locked(self) -> str | None:
        """看到锁的文字 → 原因;拿到片长 → None;30 秒都没有 → RuntimeError(慢网不能误报成带锁)。"""
        end = time.monotonic() + LOCK_WAIT
        while True:
            if lock_text(self.page.evaluate(TEXT_JS)["text"]):
                return "网页带锁(扫码进入 App 端)"
            if self.page.evaluate(DURATION_JS) > 0:
                return None
            if time.monotonic() >= end:
                raise RuntimeError(f"{LOCK_WAIT} 秒内没加载出视频(网慢或页面变了)")   # recorder 会加「第 n 集:」
            self.page.wait_for_timeout(1000)

    def _state(self) -> dict:
        return self.page.evaluate(STATE_JS)

    def _until(self, states: tuple[str, ...], seconds: float) -> dict:
        end = time.monotonic() + seconds
        while (s := self._state())["state"] not in states and time.monotonic() < end:
            self.page.wait_for_timeout(500)
        return s

    def record_current(self, out: Path, on_tick=None) -> dict:
        page = self.page
        page.evaluate(CAPTURE_JS)
        page.click("#refs-rec-btn")
        s = self._until(("armed", "error"), 15)
        if s["state"] != "armed":
            raise RuntimeError(f"标签页录制没开起来:{s['err'] or s['state']}")
        if not any(t.startswith("audio:") for t in s["tracks"]):
            page.evaluate(FORCE_STOP_JS)
            raise RuntimeError(f"标签页录制没拿到声音轨(轨道:{s['tracks']})")
        info = page.evaluate(START_JS)
        dur = float(info["duration"])
        if not math.isfinite(dur) or dur <= 0:
            raise RuntimeError("拿不到这一集的片长")
        s = self._until(("recording", "stopped", "error"), 15)
        if s["state"] != "recording" and s["state"] != "stopped":
            raise RuntimeError(f"视频没开始播放:{s['err'] or s['state']}")
        end = time.monotonic() + record_deadline(dur)
        while (s := self._state())["state"] != "stopped":
            if on_tick is not None:
                on_tick(float(s["t"]), dur)
            if s["gone"] or time.monotonic() >= end:     # 视频元素被换掉 / 超时:停下,时长检查会判
                page.evaluate(FORCE_STOP_JS)
                s = self._until(("stopped",), 10)
                break
            page.wait_for_timeout(2000)
        if s["state"] != "stopped":
            raise RuntimeError("录制停不下来")
        size = page.evaluate("() => window.__refsBlob.size")
        data = bytearray()
        for off in range(0, size, CHUNK):
            data += base64.b64decode(page.evaluate(CHUNK_JS, [off, CHUNK]))
        Path(out).write_bytes(bytes(data))
        return {"duration": dur, "box": tuple(info["box"]), "viewport": tuple(info["viewport"])}

    def close(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
            self._pw = self._ctx = self.page = None


def login(profile: Path, series_id: str, *, timeout: float = 300) -> bool:
    """有头打开 `/player/<剧号>` 等人用红果 App 扫码,最多 timeout 秒(用户说过 2 分钟太短,给 5 分钟)。
    登录框只在播放页、试着播放后才弹(首页不一定弹),所以开播放页而不是首页;打开就点右上角「登录」叫出二维码;登录与否只看登录 cookie(`has_session`),cookie 出现前窗口一直开着。
    返回是否登录成功;结论由调用方(Runtime)经 `loginstate.record` 写 meta 与 journal/refs。"""
    from playwright.sync_api import sync_playwright
    url = player_url(series_id)
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile), **launch_kwargs())
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            ok = session_of(ctx)
            if not ok:
                page.evaluate(OPEN_LOGIN_JS)                   # 直接点右上角「登录」把二维码叫出来,不等 20 多秒自己弹
            end = time.monotonic() + timeout
            while not ok and time.monotonic() < end:       # 等人扫码:登录 cookie 出现才算成功,窗口一直开着
                page.wait_for_timeout(3000)
                ok = session_of(ctx)
            if ok:
                page.wait_for_timeout(3000)          # 让登录态写进配置目录再关
        finally:
            ctx.close()
    return ok
