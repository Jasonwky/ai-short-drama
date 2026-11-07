# -*- coding: utf-8 -*-
"""只测纯函数与 JS 文本;真浏览器不进单测(手工验证见计划 Task 10)。"""
import re

import pytest

from tools.refs import hgdriver


def test_player_url_only_ever_builds_the_series_page():
    assert hgdriver.player_url("7561390625552092222") == "https://hongguoduanju.com/player/7561390625552092222"
    for bad in ("7561390625552092222/7561394905621220414", "abc", ""):
        with pytest.raises(ValueError):
            hgdriver.player_url(bad)


def test_capture_js_records_the_tab_with_audio():
    js = hgdriver.CAPTURE_JS
    assert "getDisplayMedia" in js and "audio: true" in js and "preferCurrentTab: true" in js
    assert "video/webm;codecs=vp9,opus" in js and "4000000" in js
    assert "'playing'" in js and "'ended'" in js         # 录制随播放开始 / 播完停,不靠轮询掐头尾


def test_launch_is_headed_with_tab_capture_and_sound():
    kw = hgdriver.launch_kwargs()
    assert kw["headless"] is False
    assert "--auto-accept-this-tab-capture" in kw["args"] and "--mute-audio" in kw["ignore_default_args"]
    assert kw["viewport"] == {"width": 1280, "height": 900}


def test_record_deadline_is_duration_plus_thirty_seconds():
    assert hgdriver.record_deadline(61.6) == pytest.approx(91.6)


def test_logged_out_markers():
    assert hgdriver.logged_out(True, "") is True
    assert hgdriver.logged_out(False, "首页 扫码登录 搜索") is True
    assert hgdriver.logged_out(False, "首页 我的 选集 1 2 3") is False


def test_lock_marker():
    assert hgdriver.lock_text("第 6 集 扫码进入 App 端 继续观看") is True
    assert hgdriver.lock_text("选集 1-15") is False


def test_pick_episode_js_searches_near_the_episode_label_by_exact_text():
    js = hgdriver.PICK_EPISODE_JS
    assert "'选集'" in js and "data-refs-ep" in js and "String(n)" in js
    assert "goto" not in js and "location" not in js          # 切集只点,不改地址


class _Page:
    """只回答 locked() 用到的两句 JS:页面文字、片长。"""
    def __init__(self, text="", duration=0.0):
        self.text, self.duration, self.url = text, duration, ""

    def evaluate(self, js, *a):
        if js == hgdriver.TEXT_JS:
            return {"overlay": False, "text": self.text}
        if js == hgdriver.DURATION_JS:
            return self.duration
        raise AssertionError(js)

    def wait_for_timeout(self, ms):
        pass


def _driver(page):
    d = hgdriver.PlaywrightDriver("unused")
    d.page = page
    return d


def test_locked_returns_the_reason_or_none(monkeypatch):
    assert _driver(_Page(text="第 6 集 扫码进入 App 端")).locked() == "网页带锁(扫码进入 App 端)"
    assert _driver(_Page(duration=61.0)).locked() is None


def test_slow_page_is_an_error_not_a_lock(monkeypatch):
    clock = iter(range(0, 1000, 10))                  # 每问一次过 10 秒
    monkeypatch.setattr(hgdriver.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match=re.escape("30 秒内没加载出视频(网慢或页面变了)")):
        _driver(_Page()).locked()


def test_login_is_decided_by_the_session_cookie():
    from tools.refs.hgdriver import has_session
    assert has_session(["ttwid", "passport_csrf_token", "sessionid"]) is True
    assert has_session(["ttwid", "passport_csrf_token", "passport_csrf_token_default", "gfkadpd"]) is False


def test_capture_never_stops_the_tab_capture_tracks():
    """真机 2026-11-07:停掉「录当前标签页」的轨道,Chrome 会把标签页和浏览器一起关掉(第 1 集播完就关)。"""
    from tools.refs.hgdriver import CAPTURE_JS
    assert ".stop()" not in CAPTURE_JS.replace("r.stop()", "")
    assert "window.__refsStream" in CAPTURE_JS
