# -*- coding: utf-8 -*-
"""AI 出资产表与全剧审的**真 LLM 契约测试**(spec 2026-10-25-全剧审与资产表-design.md 第 7 节;默认 `pytest -q` 不跑)。
材料取自《回到古代做将军》第 5、6 集(同一个人有「陈老三 / 老陈」两种叫法,另有泛称「老兵」「敌兵」)。"""
import pytest

from tests.llm.test_contract_real import _gate, client  # noqa: F401
from tools.drama import repo
from tools.drama.db import Store
from tools.llm import cast, drama_review

pytestmark = pytest.mark.real_llm

EP5 = """【先锋营营房 · 白天】
燕无咎穿着洗得发白的伙夫旧衣,背着行囊走进先锋营。
老兵说：「哟,伙夫也配住这儿?锅铲带来了吗?」
燕无咎说：「脏了,洗洗还能用。」

【先锋营校场 · 黄昏】
燕无咎说：「陈老三,小六,跟我走。」
陈老三说：「我们……都是被嫌弃的废人啊。」
燕无咎说：「我要的,是能活着回来的人。」"""
EP6 = """【营后山林 · 夜】
燕无咎把泥灰抹在老兵老陈脸上,又给新兵小六披上草衣。
小六说：「头儿,这像野鬼,真管用?」
燕无咎说：「敌人看不见你,你才活得久。」
老陈说：「行,都听你的。」

【返程山脊 · 夜】
敌兵说：「搜!刚才这边有动静!」
燕无咎说：「别慌,都贴紧岩壁。」"""


def _drama():
    s = Store(":memory:")
    d = repo.create_drama(s, "回到古代做将军", "9:16", "realistic")
    e5 = repo.add_episode(s, d, 1, "script", script=EP5)
    e6 = repo.add_episode(s, d, 2, "script", script=EP6)
    return s, d, e5, e6


def test_cast_covers_every_speaker_and_place(client):  # noqa: F811
    s, d, *_ = _drama()
    res = _gate(client, "cast", cast, cast.context(s, d, []))
    own = cast.owners(res.args)
    assert all(w in own for w in ("老兵", "陈老三", "老陈", "小六", "敌兵", "燕无咎"))
    assert own["老陈"] == own["陈老三"]                     # 剧本写明「老兵老陈」,和第 1 集的陈老三是同一人


def test_drama_review_script_quotes_verbatim(client):  # noqa: F811
    s, d, *_ = _drama()
    ctx = drama_review.context(s, d, "script")
    res = _gate(client, "drama_review", drama_review, ctx)
    assert 1 <= len(res.args["issues"]) <= drama_review.MAX_ISSUES       # 引用逐字由判卷保证


def test_drama_review_assets_and_storyboard(client):  # noqa: F811
    s, d, e5, e6 = _drama()
    for n in ("燕无咎", "陈老三", "小六"):
        repo.add_character(s, d, n, appearance="清瘦青年" if n != "陈老三" else "断臂老卒", styling="粗布军服",
                           final_prompt="A soldier in a coarse uniform.")
    yard = repo.add_scene(s, d, "先锋营", location="军营", time="白天", lighting="日光", final_prompt="A camp.")
    res = _gate(client, "drama_review", drama_review, drama_review.context(s, d, "assets"))
    assert res.args["issues"]
    for e in (e5, e6):
        repo.add_segment(s, e, 1, scene_id=yard, shot_type="中景", angle="平视", movement="固定",
                         description="【镜头1】燕无咎走进先锋营营房,众人冷眼。")
        repo.add_segment(s, e, 2, scene_id=yard, shot_type="中景", angle="平视", movement="固定",
                         description="【镜头1】燕无咎说：「我要的,是能活着回来的人。」")
    ctx = drama_review.context(s, d, "storyboard")
    res = _gate(client, "drama_review", drama_review, ctx)
    assert all(drama_review.segment_of(ctx, it["where"], it["quote"]) for it in res.args["issues"])
