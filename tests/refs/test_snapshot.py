# -*- coding: utf-8 -*-
import re
from pathlib import Path

import pytest

from tools.refs import snapshot


def test_chinese_text_is_replaced_with_same_length_fakes():
    out = snapshot.sanitize("<p>沈明珠母亲早逝</p>")
    assert out.startswith("<p>") and out.endswith("</p>") and len(out) == len("<p>沈明珠母亲早逝</p>")
    assert "沈明珠" not in out


def test_markers_digits_and_private_use_survive():
    s = "本章共3307字 8339万热度 评分9.5 下载App 开通会员 第1章 "
    assert snapshot.sanitize(s) == s


def test_jjwxc_markers_survive():
    s = "文章积分:45,184,614,400 [添加书签] [推荐给朋友] [收藏此章节] [投诉]"
    assert snapshot.sanitize(s) == s


def test_same_char_maps_to_same_fake():
    out = snapshot.sanitize("玫玫")
    assert out[0] == out[1] and out[0] != "玫"


def test_review_nicknames_and_avatars_are_removed():
    s = '{"user_name":"小喬129","user_avatar":"http://p3/x.jpg","content":"好看"}'
    out = snapshot.sanitize(s)
    assert '"user_name":"已去除"' in out and '"user_avatar":""' in out
    esc = "&quot;user_name&quot;:&quot;小喬129&quot;"
    assert "129" not in snapshot.sanitize(esc)


@pytest.mark.parametrize("before,after", [
    ('<img src="https://p3.douyinpic.com/aweme/100x100/aweme-avatar/tos-cn-i/abc.jpeg?x=1">', '<img src="">'),
    ('{"avatar":"https://cdn.static.17k.com/user/avatar/13/73/60/26716073.jpg-88x88?v=1"}', '{"avatar":""}'),
    ("&quot;avatar&quot;:&quot;https://p3.x.com/aweme-avatar/a.jpg&quot;", "&quot;avatar&quot;:&quot;&quot;"),
    (r'\&quot;avatar\&quot;:\&quot;https://p3.x.com/aweme-avatar/a.jpg\&quot;,',
     r'\&quot;avatar\&quot;:\&quot;\&quot;,'),
    (r'{"pic":"https:\u002F\u002Fstatic.zongheng.com\u002Fuserimage\u002Fb6\u002Fx.png"}', '{"pic":""}'),
    (r'{"pic":"https:\/\/a.cn\/upload\/avatar\/9.png"}', '{"pic":""}'),
    ('<img src="https://cdn.qimao.com/bookimg/cover/1.jpg">', '<img src="https://cdn.qimao.com/bookimg/cover/1.jpg">'),
])
def test_avatar_urls_are_blanked(before, after):
    assert snapshot.sanitize(before) == after


def test_17k_user_pages_lose_their_ids():
    assert (snapshot.sanitize('<a href="http://user.17k.com/see/www/97458810.html">')
            == '<a href="http://user.17k.com/see/www/0.html">')


def test_zongheng_user_pages_lose_their_ids():
    assert (snapshot.sanitize('<a href="//home.zongheng.com/show/userInfo/12763884.html">')
            == '<a href="//home.zongheng.com/show/userInfo/0.html">')


@pytest.mark.parametrize("before,after", [
    ('{"uid":"1049346978351911","bookId":"7"}', '{"uid":"0","bookId":"7"}'),
    ('{"userId":123,"user_id":456}', '{"userId":0,"user_id":0}'),
    ("{userId:17588911,bookId:1}", "{userId:0,bookId:1}"),
    ("&quot;userId&quot;:&quot;123&quot;", "&quot;userId&quot;:&quot;0&quot;"),
    ('{"uuid":"7705317684404028953","author_id":"9"}', '{"uuid":"7705317684404028953","author_id":"9"}'),
])
def test_user_ids_become_zero(before, after):
    assert snapshot.sanitize(before) == after


@pytest.mark.parametrize("before,after", [
    ('{"nickname":"abc1"}', '{"nickname":"已去除"}'),
    ('{"nick_name":"abc1"}', '{"nick_name":"已去除"}'),
    ('{nickName:"abc1",x:1}', '{nickName:"已去除",x:1}'),
    ("&quot;nickName&quot;:&quot;abc1&quot;", "&quot;nickName&quot;:&quot;已去除&quot;"),
    (r"\&quot;nickname\&quot;:\&quot;abc1\&quot;,", r"\&quot;nickname\&quot;:\&quot;已去除\&quot;,"),
    (r"\&quot;user_name\&quot;:\&quot;abc1\&quot;,", r"\&quot;user_name\&quot;:\&quot;已去除\&quot;,"),
    ('<span class="loading__nickname-J" aria-hidden="true"></span>', '<span class="loading__nickname-J" aria-hidden="true"></span>'),
])
def test_nicknames_are_removed(before, after):
    assert snapshot.sanitize(before) == after


def test_hongguo_review_user_name_element_is_removed():
    assert (snapshot.sanitize('<div class="pc-user-name-BAuNS5 ">abc129</div>')
            == '<div class="pc-user-name-BAuNS5 ">已去除</div>')


def test_fanqie_abstract_is_emptied():
    bs = chr(92)                                   # 反斜杠:值里有转义的引号与 u0041
    raw = '{"abstract":"x' + bs + '"y' + bs + 'u0041","bookId":"1"}'
    assert snapshot.sanitize(raw) == '{"abstract":"","bookId":"1"}'


FIXTURES = Path(__file__).parent / "fixtures"
PERSONAL = [r"aweme-avatar", r"(?i)userimage", snapshot.AVATAR_URL.pattern, r"see/www/[1-9]", r"userInfo/[1-9]",
            r"(?i)user_?id(?:\\?&quot;|\")?\s*:\s*(?:\\?&quot;|\")?[1-9]\d{3,}", r"\"uid\":\"[1-9]",
            r"(?i)nick_?name(?:\\?&quot;|\")?\s*:\s*(?:\\?&quot;|\")(?!已去除)[^\"&\\]",
            r"pc-user-name[^>]*>(?!已去除)[^<]", r"\"abstract\":\"[^\"]"]


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.iterdir()))
def test_fixtures_have_no_personal_info(name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    found = [(pat, m.group()[:60]) for pat in PERSONAL if (m := re.search(pat, text))]
    assert not found
