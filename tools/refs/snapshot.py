# -*- coding: utf-8 -*-
"""单测快照脱敏(spec 4.2):页面结构留下,版权正文与个人信息不进 git。

- 汉字逐个映射成假字(固定映射、长度不变);KEEP 里的标记词原样留(解析器 / 判定靠它们);私用区字符原样留;
- 个人信息清掉(JSON、JS、HTML 转义、再转义一层几种写法):昵称 → 「已去除」,头像地址整条清空,
  用户编号(userId / user_id / uid)→ 0,17K / 纵横用户主页链接里的编号 → 0;番茄字体加密的简介清空。

开发时抓一份快照(**不是产品功能**,只给写适配器的人用):
    python -X utf8 -m tools.refs.snapshot <url> <输出文件> [--encoding gb18030]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KEEP = ("本章共", "字", "热度", "万", "亿", "评分", "收藏", "点赞", "人次读过", "第", "章", "集", "免费",
        "下载", "开通会员", "订阅", "请至", "阅读", "积分", "热播", "榜", "全", "饰", "安全验证", "正文", "完结", "连载", "已去除",
        "文章积分", "添加书签", "推荐给朋友", "收藏此章节", "投诉")
FAKE = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
_KEEP_RE = re.compile("|".join(sorted(map(re.escape, KEEP), key=len, reverse=True)))
_Q = r'(?:\\?&quot;|")'          # 引号的三种写法:JSON 的 "、HTML 转义 &quot;、再转义一层 \&quot;
_NAME = r"(?:user_name|nick_?name)"
_URL_CH = r"(?:[^\s\"'<>&\\]|\\u002F|\\/)"      # URL 里的字符(含 JSON 的 \/ 与 \u002F)
AVATAR_URL = re.compile(r"(?i)https?:" + _URL_CH + r"*(?:avatar|userimage)" + _URL_CH + "*")
_NICK = [(AVATAR_URL, ""),                                                     # 头像地址整条清掉
         (re.compile(r'("user_avatar"\s*:\s*")[^"]*(")'), r"\1\2"),
         (re.compile(r"(&quot;user_avatar&quot;:&quot;).*?(&quot;)"), r"\1\2"),
         (re.compile(r'(?i)((?<!\w)"?' + _NAME + r'"?\s*:\s*")[^"]*(")'), r"\1已去除\2"),          # JSON / JS
         (re.compile(r"(?i)((?<!\w)" + _NAME + r"\\?&quot;\s*:\s*\\?&quot;).*?(\\?&quot;)"), r"\1已去除\2"),
         (re.compile(r'(class="pc-user-name[^"]*"[^>]*>)[^<]*(<)'), r"\1已去除\2"),             # 红果剧评昵称
         (re.compile(r"(?i)((?<!\w)" + _Q + r"?(?:user_?id|uid)" + _Q + r"?\s*:\s*" + _Q + r"?)\d+"), r"\g<1>0"),
         (re.compile(r"(user\.17k\.com/see/www/)\d+"), r"\g<1>0"),               # 17K 用户主页
         (re.compile(r"(home\.zongheng\.com/show/userInfo/)\d+"), r"\g<1>0"),    # 纵横用户主页
         (re.compile(r'("abstract"\s*:\s*")(?:[^"\\]|\\.)*(")'), r"\1\2")]       # 番茄简介:字体加密,解析不用


def _fake(ch: str) -> str:
    return FAKE[ord(ch) % len(FAKE)] if ch not in FAKE else ch


def sanitize(text: str) -> str:
    for pat, rep in _NICK:
        text = pat.sub(rep, text)
    out, i = [], 0
    for m in _KEEP_RE.finditer(text):
        out.append(re.sub(r"[一-鿿]", lambda c: _fake(c.group()), text[i:m.start()]))
        out.append(m.group())
        i = m.end()
    out.append(re.sub(r"[一-鿿]", lambda c: _fake(c.group()), text[i:]))
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    from tools.refs.net import Fetcher
    ap = argparse.ArgumentParser(description="抓一份脱敏快照给适配器单测用")
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--encoding", default="utf-8")
    a = ap.parse_args(argv)
    html = Fetcher(Path("tmp/snapshot"), "dev").get(a.url, encoding=a.encoding)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(sanitize(html), encoding="utf-8")
    print(f"已存 {a.out}({len(html)} 字符,已脱敏)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
