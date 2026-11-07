# -*- coding: utf-8 -*-
import pytest

from tools.refs import net, rjournal


class Net:
    """假网络:url → (status, bytes);记下请求顺序。"""
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if url not in self.pages:
            raise OSError("connection refused")
        status, body = self.pages[url]
        return status, body.encode("utf-8") if isinstance(body, str) else body, url


ALLOW = (200, "User-agent: *\nAllow: /\n")


def fetcher(tmp_path, pages, **kw):
    return net.Fetcher(tmp_path, "jjwxc", opener=Net(pages), sleep=lambda s: None, **kw)


def test_get_returns_text_and_journals_url_status_and_robots(tmp_path):
    f = fetcher(tmp_path, {"https://a.cn/robots.txt": ALLOW, "https://a.cn/x": (200, "正文")})
    assert f.get("https://a.cn/x") == "正文"
    ev = rjournal.read(tmp_path, source="jjwxc")
    assert [e["event"] for e in ev] == ["fetch", "robots"]
    assert ev[0]["status"] == 200 and "Allow: /" in ev[1]["text"]


def test_robots_disallow_raises_before_fetching(tmp_path):
    f = fetcher(tmp_path, {"https://a.cn/robots.txt": (200, "User-agent: *\nDisallow: /player/\n")})
    with pytest.raises(net.FetchError) as e:
        f.get("https://a.cn/player/1/2")
    assert e.value.kind == "disallowed" and f.opener.calls == ["https://a.cn/robots.txt"]


def test_missing_robots_allows_but_unreadable_robots_refuses(tmp_path):
    f = fetcher(tmp_path, {"https://a.cn/robots.txt": (404, ""), "https://a.cn/x": (200, "ok")})
    assert f.get("https://a.cn/x") == "ok"
    g = fetcher(tmp_path, {"https://b.cn/robots.txt": (202, "<script src=probe.js>")})
    with pytest.raises(net.FetchError, match="robots.txt 读不到"):
        g.get("https://b.cn/x")


@pytest.mark.parametrize("status,body", [(412, ""), (403, ""), (200, "<div>安全验证 · 拖动下方滑块完成拼图</div>")])
def test_risk_control_is_blocked(tmp_path, status, body):
    f = fetcher(tmp_path, {"https://a.cn/robots.txt": ALLOW, "https://a.cn/x": (status, body)})
    with pytest.raises(net.FetchError) as e:
        f.get("https://a.cn/x")
    assert e.value.kind == "blocked"


def test_captcha_word_in_script_path_is_not_blocked(tmp_path):
    # 番茄 2026-11-07:页面引了 .../sec_sdk_build/3.8.0/captcha/index.js,数据本身正常
    body = ('<script src="https://lf-c-flwb.bytetos.com/obj/rc-client-security/sec_sdk_build/3.8.0/captcha/index.js">'
            '</script><style>.captcha{}</style><div>榜单</div>')
    f = fetcher(tmp_path, {"https://a.cn/robots.txt": ALLOW, "https://a.cn/x": (200, body)})
    assert f.get("https://a.cn/x") == body


def test_visible_text_drops_scripts_styles_and_tags():
    html = '<SCRIPT type="x">var captcha=1;</SCRIPT><style>\n.captcha{}\n</style><p>安全<b>验证</b></p>'
    assert net.visible_text(html) == "安全验证"


def test_unreachable_and_other_http(tmp_path):
    f = fetcher(tmp_path, {"https://a.cn/robots.txt": ALLOW, "https://a.cn/500": (500, "")})
    with pytest.raises(net.FetchError) as e:
        f.get("https://a.cn/gone")
    assert e.value.kind == "unreachable"
    with pytest.raises(net.FetchError) as e:
        f.get("https://a.cn/500")
    assert e.value.kind == "http"


def test_same_host_requests_are_spaced(tmp_path):
    # 请求顺序:robots.txt(第一次,不等)→ /1(等 interval)→ /2(等 interval);时钟不走,每次都等满
    waits, t = [], [100.0]
    f = net.Fetcher(tmp_path, "jjwxc", opener=Net({"https://a.cn/robots.txt": ALLOW, "https://a.cn/1": (200, "1"),
                                                     "https://a.cn/2": (200, "2")}),
                    sleep=waits.append, clock=lambda: t[0], interval=3.0)
    f.get("https://a.cn/1")
    f.get("https://a.cn/2")
    assert f.opener.calls == ["https://a.cn/robots.txt", "https://a.cn/1", "https://a.cn/2"]
    assert len(waits) == 2 and abs(sum(waits) - 2 * 3.0) < 1e-6


def test_incomplete_read_is_unreachable(tmp_path):
    import http.client

    def opener(url, headers, timeout):
        if url.endswith("robots.txt"):
            return 200, b"User-agent: *\nAllow: /\n", url
        raise http.client.IncompleteRead(b"abc", 100)
    f = net.Fetcher(tmp_path, "jjwxc", opener=opener, sleep=lambda s: None)
    with pytest.raises(net.FetchError) as e:
        f.get("https://a.cn/x")
    assert e.value.kind == "unreachable"


@pytest.mark.parametrize("final", ["https://passport.other.cn/x", "https://a.cn/login?next=/x",
                                   "https://a.cn/passport/sso", "https://a.cn/verify", "https://a.cn/captcha/1"])
def test_redirect_to_login_or_other_host_is_blocked(tmp_path, final):
    def opener(url, headers, timeout):
        if url.endswith("robots.txt"):
            return 200, b"User-agent: *\nAllow: /\n", url
        return 200, "<div>正常页面</div>".encode(), final
    f = net.Fetcher(tmp_path, "jjwxc", opener=opener, sleep=lambda s: None)
    with pytest.raises(net.FetchError, match="登录 / 验证页") as e:
        f.get("https://a.cn/x")
    assert e.value.kind == "blocked"


def test_redirect_within_same_host_is_fine(tmp_path):
    def opener(url, headers, timeout):
        if url.endswith("robots.txt"):
            return 200, b"User-agent: *\nAllow: /\n", url
        return 200, b"ok", "http://a.cn/x/"
    assert net.Fetcher(tmp_path, "jjwxc", opener=opener, sleep=lambda s: None).get("https://a.cn/x") == "ok"


def test_captcha_word_deep_in_long_page_is_not_blocked(tmp_path):
    body = "<p>" + "甲" * 5000 + "访问被拒绝</p>"
    f = fetcher(tmp_path, {"https://a.cn/robots.txt": ALLOW, "https://a.cn/x": (200, body)})
    assert f.get("https://a.cn/x") == body


# ---- robots 判定(RFC 9309 / Google 语义) ----

HONGGUO = "User-agent: *\nAllow: /\nDisallow: /player/*/*\nDisallow: /series/\nDisallow: /query/\n"


def ok(robots, path):
    return net.Robots.parse(robots).allowed("https://a.cn" + path)


@pytest.mark.parametrize("path,allowed", [("/player/1/2", False), ("/player/123", True),
                                          ("/detail?series_id=1", True), ("/series/x", False), ("/query/a", False)])
def test_robots_hongguo(path, allowed):
    assert ok(HONGGUO, path) is allowed


def test_robots_longest_match_wins_regardless_of_order():
    jj = "User-agent: *\nAllow: /\nDisallow: /sp\n"
    assert not ok(jj, "/sp/x") and ok(jj, "/onebook.php?novelid=1")


def test_robots_wildcard_and_end_anchor():
    assert not ok("User-agent: *\nDisallow: /*.js\n", "/a/b.js")
    assert not ok("User-agent: *\nDisallow: /*.js\n", "/a.json")          # 前缀匹配,.json 也命中(标准语义)
    assert ok("User-agent: *\nDisallow: /*.js$\n", "/a.json")
    assert not ok("User-agent: *\nDisallow: /*.js$\n", "/a.js")


def test_robots_equal_length_allow_wins_and_query_counts():
    assert ok("User-agent: *\nDisallow: /a\nAllow: /a\n", "/a")
    assert not ok("User-agent: *\nDisallow: /*?\n", "/x?y=1") and ok("User-agent: *\nDisallow: /*?\n", "/x")


def test_robots_empty_disallow_is_ignored_and_comments_dropped():
    assert ok("User-agent: *\nDisallow:\n", "/x")
    assert not ok("User-agent: * # all\nDisallow: /x # no\n", "/x/1")


def test_robots_star_group_applies_when_other_groups_are_for_other_bots():
    ks = "User-agent: Googlebot\nAllow: /\n\nUser-agent: *\nDisallow: /\n"
    assert not ok(ks, "/x")
    ks2 = "User-agent: *\nDisallow: /\n\nUser-agent: Googlebot\nAllow: /\n"
    assert not ok(ks2, "/x")


def test_robots_our_own_group_beats_star_and_matches_case_insensitive_substring():
    r = "User-agent: *\nDisallow: /\n\nUser-agent: AI-Short-Drama-Refs\nDisallow: /private\n"
    assert ok(r, "/x") and not ok(r, "/private/1")


def test_robots_consecutive_user_agents_share_one_group():
    r = "User-agent: Googlebot\nUser-agent: *\nDisallow: /a\n\nUser-agent: Bingbot\nDisallow: /\n"
    assert not ok(r, "/a/1") and ok(r, "/b")


def test_robots_without_star_group_allows_everything():
    assert ok("User-agent: Googlebot\nDisallow: /\n", "/x")
    assert ok("", "/x") and ok("Sitemap: https://a.cn/s.xml\nfoo bar\n", "/x")


def test_gb18030_pages_decode(tmp_path):
    f = net.Fetcher(tmp_path, "jjwxc", opener=Net({"https://a.cn/robots.txt": ALLOW,
                                                     "https://a.cn/x": (200, "晋江".encode("gb18030"))}), sleep=lambda s: None)
    assert f.get("https://a.cn/x", encoding="gb18030") == "晋江"


def test_browser_get_checks_robots_and_uses_the_injected_browser(tmp_path):
    f = fetcher(tmp_path, {"https://a.cn/robots.txt": ALLOW}, browser=lambda url: f"<html>{url}</html>")
    assert f.browser_get("https://a.cn/y") == "<html>https://a.cn/y</html>"


def test_events_carry_the_work_id(tmp_path):
    f = fetcher(tmp_path, {"https://a.cn/robots.txt": ALLOW, "https://a.cn/x": (200, "正文")}, work_id=7)
    f.get("https://a.cn/x")
    with pytest.raises(net.FetchError):
        f.get("https://a.cn/down")
    assert [e["event"] for e in rjournal.read(tmp_path, work_id=7)] == ["fetch", "fetch", "robots"]
    plain = fetcher(tmp_path / "b", {"https://a.cn/robots.txt": ALLOW, "https://a.cn/x": (200, "正文")})
    plain.get("https://a.cn/x")
    assert all("work_id" not in e for e in rjournal.read(tmp_path / "b"))
