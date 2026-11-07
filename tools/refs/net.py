# -*- coding: utf-8 -*-
"""参考库取页(spec 1.1 第 1、2 条、4.1)。

- UA 写明身份;每个 Fetcher 实例(= 一轮任务)**重读一次 robots**,原文进 journal;robots 禁止 / 读不到 → disallowed(来源暂停);
- 同一域名两次请求之间至少 interval 秒;
- robots 判定按 RFC 9309 / Google 语义自己实现(`Robots`):标准库 robotparser 不认 `*` / `$`、按先出现的规则生效,
  会把红果 `Disallow: /player/*/*` 判成允许;
- 403 / 412 / 429、页面可见文字开头有验证码字样、被跳到别的主机或登录 / 验证页 → blocked(当天熔断,不重试);
  连不上 / 超时 / 读一半断了 → unreachable(下一轮重试);
- `browser_get`:普通 HTTP 抓不到的页面用 Playwright **无头**取(用户 2026-11-06 定的唯一无头例外,只限参考库后台抓取);
  用哪种由适配器写死,这里不做运行时切换。
"""
from __future__ import annotations

import http.client
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from tools.refs import rjournal

TOKEN = "ai-short-drama-refs"           # robots 里认的 UA 标识
UA = f"Mozilla/5.0 (compatible; {TOKEN}/1.0; reference research, low rate)"
BLOCK_STATUS = {403, 412, 429}
CAPTCHA = ("安全验证", "拖动下方滑块", "访问被拒绝", "captcha")
CAPTCHA_HEAD = 3000       # 验证页很短;正文页靠前是标题导航,深处的字样是正文 / 评论,不算
LOGIN_PATH = ("login", "passport", "verify", "captcha")
TIMEOUT = 20
_HIDDEN = re.compile(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", re.S | re.I)


def visible_text(html: str) -> str:
    """验证码字样只在人看得见的字里查:脚本路径里的 captcha(番茄引了安全 SDK)不算。"""
    return re.sub(r"<[^>]+>", "", _HIDDEN.sub("", html))


def _captcha(html: str) -> bool:
    head = re.sub(r"\s+", " ", visible_text(html))[:CAPTCHA_HEAD]
    return any(m in head for m in CAPTCHA)


class Robots:
    """robots.txt 判定(RFC 9309 / Google 语义)。

    - 按 User-agent 分组(连续多行 UA 同一组);有组的 UA 含我们的标识(不分大小写)就用那些组,否则用 `*` 组,都没有 = 全允许;
    - 路径 = path + ?query;规则里 `*` 匹配任意串、结尾 `$` 锚定结尾;取规则串最长的那条,一样长 Allow 优先;没规则匹配 = 允许;
    - 空的 `Disallow:` 忽略;`#` 后是注释;不认识的行忽略;百分号编码不做归一。
    """

    def __init__(self, rules: list[tuple[bool, str]]):
        self.rules = [(allow, pat, self._regex(pat)) for allow, pat in rules]

    @staticmethod
    def _regex(pat: str) -> re.Pattern:
        end = pat.endswith("$")
        body = ".*".join(map(re.escape, (pat[:-1] if end else pat).split("*")))
        return re.compile(body + ("$" if end else ""), re.S)

    @classmethod
    def parse(cls, text: str, token: str = TOKEN) -> "Robots":
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        in_ua = False
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            key, sep, value = line.partition(":")
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if not in_ua:
                    groups.append(([], []))
                groups[-1][0].append(value.lower())
                in_ua = True
            elif key in ("allow", "disallow"):
                in_ua = False
                if groups and value:
                    groups[-1][1].append((key == "allow", value))
        token = token.lower()
        mine = ([g for g in groups if any(a != "*" and token in a for a in g[0])]
                or [g for g in groups if "*" in g[0]])
        return cls([r for g in mine for r in g[1]])

    def allowed(self, url: str) -> bool:
        u = urlsplit(url)
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        best: tuple[int, bool] | None = None
        for allow, pat, rx in self.rules:
            if rx.match(path):
                cand = (len(pat), allow)              # 长的优先;一样长 True(Allow)> False
                if best is None or cand > best:
                    best = cand
        return True if best is None else best[1]


class FetchError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind, self.status = kind, status


def urllib_opener(url: str, headers: dict, timeout: float):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", url


def headless_html(url: str) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)      # 唯一无头例外:参考库后台抓取(CLAUDE.md、spec 1.1)
        try:
            page = b.new_page(user_agent=UA)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            return page.content()
        finally:
            b.close()


class Fetcher:
    def __init__(self, root: Path, source: str, *, opener: Callable = urllib_opener,
                 browser: Callable[[str], str] = headless_html, interval: float = 3.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 work_id: int | None = None):
        self.root, self.source, self.opener, self.browser = Path(root), source, opener, browser
        self.work_id = work_id                    # 这一轮是为哪部作品取的(作品详情页按它查取页记录);榜单 / sitemap 是 None
        self.interval, self.sleep, self.clock = interval, sleep, clock
        self._robots: dict[str, Robots] = {}
        self._last: dict[str, float] = {}

    def _log(self, event: str, **fields) -> None:
        if self.work_id is not None:
            fields["work_id"] = self.work_id
        rjournal.append(self.root, event, source=self.source, **fields)

    def _host(self, url: str) -> str:
        u = urlsplit(url)
        return f"{u.scheme}://{u.netloc}"

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.interval - (self.clock() - last)
            if gap > 0:
                self.sleep(gap)
        self._last[host] = self.clock()

    def _open(self, url: str):
        try:
            return self.opener(url, {"User-Agent": UA}, TIMEOUT)
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as e:
            self._log("fetch", url=url, status=None, error=str(e))
            raise FetchError("unreachable", f"连不上 {url}:{e}") from e

    def _robots_for(self, url: str) -> Robots:
        host = self._host(url)
        rp = self._robots.get(host)
        if rp is None:
            self._wait(host)                          # robots.txt 也是一次请求,同样守间隔
            status, body, _ = self._open(host + "/robots.txt")
            text = body.decode("utf-8", "replace")
            self._log("robots", url=host + "/robots.txt", status=status, text=text[:4000])
            if status == 404:
                rp = Robots([])
            elif status == 200:
                rp = Robots.parse(text)
            else:
                raise FetchError("disallowed", f"robots.txt 读不到(HTTP {status})—— 不知道让不让抓,先不抓", status)
            self._robots[host] = rp
        return rp

    def allowed(self, url: str) -> bool:
        """只问 robots 让不让(这一轮读一次、原文进 journal),不取页:录屏打开播放页前用(recorder.run_job)。
        robots 读不到照样抛 FetchError(disallowed)—— 不知道让不让,就当不让。"""
        return self._robots_for(url).allowed(url)

    def _check_robots(self, url: str) -> None:
        if not self.allowed(url):
            raise FetchError("disallowed", f"robots.txt 禁止抓 {url}")

    def get(self, url: str, *, encoding: str = "utf-8") -> str:
        self._check_robots(url)
        self._wait(self._host(url))
        status, body, final = self._open(url)
        text = body.decode(encoding, "replace")
        self._log("fetch", url=url, status=status, bytes=len(body))
        if status in BLOCK_STATUS or _captcha(text):
            raise FetchError("blocked", f"被风控 / 验证码挡住(HTTP {status}):{url}", status)
        fu = urlsplit(final or url)
        if fu.hostname != urlsplit(url).hostname or any(w in fu.path.lower() for w in LOGIN_PATH):
            raise FetchError("blocked", f"被跳到登录 / 验证页({final}):{url}", status)
        if status != 200:
            raise FetchError("http", f"HTTP {status}:{url}", status)
        return text

    def browser_get(self, url: str) -> str:
        self._check_robots(url)
        self._wait(self._host(url))
        html = self.browser(url)
        self._log("fetch", url=url, status=200, bytes=len(html), via="headless")
        if _captcha(html):
            raise FetchError("blocked", f"被验证码挡住:{url}")
        return html
