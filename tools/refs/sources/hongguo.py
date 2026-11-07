# -*- coding: utf-8 -*-
"""红果短剧(spec 第 0 节;调研 2026-11-07-红果短剧内容获取实测.md)。

能拿:4 个榜单、详情(热度 / 点赞 / 收藏 / 标签 / 角色 / 简介 / 集数)、剧评(详情页自带的那几条)、sitemap。
不碰:`/player/*/*`、`/series/`、`/query/`(robots 禁止)。正片录屏是 1b,而且永远要人点。
剧评**不取昵称与头像**(spec 1.1 第 4 条)。
"""
from __future__ import annotations

import html as htmllib
import json
import re

from tools.refs.sources.base import (Adapter, AdapterError, RankItem, Review, UnitRef, WorkMeta, check_ranks,
                                     field, json_after, need, num)

BASE = "https://hongguoduanju.com"
BOARDS = {"hot-drama": "热播榜", "hot-real-drama": "真人剧榜", "hot-ai-drama": "AI 剧榜", "hot-comic-drama": "漫剧榜"}
WEB_EPISODES = 5      # 登录后网页能看的集数(调研实测:第 6 集起带锁)
RAW = ("heatText", "scoreText", "favoriteText", "likeText", "tags", "episodeCount", "openEpisodeCount", "statusTags")


def _loads(s: str):
    try:
        return json.loads(s)
    except ValueError as e:
        raise AdapterError(f"data-fn-args 解析失败:{e}") from e


def _fn_args(page: str, name: str) -> dict:
    """两种交付形态(同一网址时而 A 时而 B):
    A `data-fn-args='[路由, name, {数据}]'`;
    B 只有 mergeLoaderData:`data-fn-args="[路由, [{key: name, routerDataFnName, routerDataFnArgs: ["<数据的 JSON 字符串>"]}]]"`
    (A 形态的页面也带一条 B 形态的属性,但那里 routerDataFnArgs 是字符串不是数据块 —— 跳过)。"""
    for m in re.finditer(r"""data-fn-args=(?:'([^']*)'|"([^"]*)")""", page):
        args = _loads(htmllib.unescape(m.group(1) if m.group(1) is not None else m.group(2)))
        if not isinstance(args, list) or len(args) < 2:
            continue
        if len(args) >= 3 and args[1] == name:
            need(isinstance(args[2], dict), f"{name} 数据块不是对象")
            return args[2]
        if isinstance(args[1], list):
            for e in args[1]:
                fa = e.get("routerDataFnArgs") if isinstance(e, dict) and e.get("key") == name else None
                if isinstance(fa, list) and fa and isinstance(fa[0], str) and isinstance(d := _loads(fa[0]), dict):
                    return d
    raise AdapterError(f"页面里没有 {name} 数据块 —— 网页可能改版了")


def parse_rank(page: str) -> list[RankItem]:
    c = _fn_args(page, "content")
    need(c.get("isSuccess") and isinstance(c.get("rankList"), list), "榜单数据块不完整")
    return check_ranks([_rank_item(x) for x in c["rankList"]])


def _rank_item(x) -> RankItem:
    rank = field(x, "rank")
    need(isinstance(rank, int) or str(rank).isdigit(), f"榜单名次不是整数:{rank!r}")
    return RankItem(str(field(x, "seriesId")), str(field(x, "title")), int(rank), num(x.get("heatText")),
                    {k: x.get(k) for k in RAW})


def _router(page: str) -> dict:
    return field(json_after(page, "_ROUTER_DATA = "), "loaderData")


def _tag(t) -> str:
    return t if isinstance(t, str) else (t.get("tag_name") or t.get("name") or "")


def parse_detail(page: str) -> tuple[WorkMeta, list[Review]]:
    p = field(_router(page), "detail_page")
    sd, ss = field(p, "seriesDetail"), p.get("seriesSocialInfo") or {}
    sid, name = str(field(sd, "series_id")), field(sd, "series_name")
    need(sid and name, "详情页缺剧号 / 剧名")
    rv = ss.get("reviews") or {}
    meta = WorkMeta(
        external_id=sid, title=name, author="", url=f"{BASE}/detail?series_id={sid}",
        tags=[x for x in map(_tag, sd.get("tags") or []) if x], intro=sd.get("series_intro", ""),
        heat=num((ss.get("hot_score_data") or {}).get("score")),
        stats={"episodes": sd.get("episode_cnt"), "likes": num(ss.get("series_like_count")),
               "favorites": num(ss.get("series_favorite_count")), "reviews_total": num(rv.get("total_count")),
               "rank_label": ss.get("rank_label", "")},
        units=[UnitRef(n, f"第 {n} 集", f"{BASE}/player/{sid}", True)
               for n in range(1, min(WEB_EPISODES, sd.get("episode_cnt") or 0) + 1)],
        roles=[c["sub_title"].removeprefix("饰").strip() for c in sd.get("celebrities") or []
               if str(c.get("sub_title", "")).startswith("饰")])
    reviews = [Review(r["content"], r.get("rating"), num(r.get("digg_count")), str(r.get("create_time", "")))
               for r in rv.get("reviews") or [] if r.get("content")]
    return meta, reviews


def parse_sitemap(xml: str) -> list[str]:
    ids = re.findall(r"detail\?series_id=(\d+)", xml)
    need(ids, "sitemap 解析出 0 条")
    return ids


def _match(url: str) -> str | None:
    m = re.search(r"hongguoduanju\.com/(?:detail\?series_id=|player/)(\d+)", url)
    return m.group(1) if m else None


ADAPTER = Adapter(
    key="hongguo", boards=BOARDS, content="reviews",
    list_ranks=lambda f, board: parse_rank(f.get(f"{BASE}/rank/{board}")),
    fetch_meta=lambda f, ext: parse_detail(f.get(f"{BASE}/detail?series_id={ext}"))[0],
    fetch_reviews=lambda f, ext: parse_detail(f.get(f"{BASE}/detail?series_id={ext}"))[1],
    match=_match,
    sitemap=lambda f: parse_sitemap(f.get(f"{BASE}/seo-important.xml")))
