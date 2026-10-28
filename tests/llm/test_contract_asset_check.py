# -*- coding: utf-8 -*-
"""资产图 AI 检查的**真 LLM 契约测试**(spec 2026-10-28-成片质量闸门-第一期-design.md 第 6.3 节;默认 `pytest -q` 不跑)。
黄金样本取自《回到古代做将军》真资产图(缩到一半):孙老栓四视图是近代制服(胸前贴袋、翻领、纽扣),
燕无咎正面图右下角有一团黑烟 —— 第 1 集成片问题的根因之一(spike-results/2026-10-28-第1集成片问题根因.md)。"""
import shutil

import pytest

from tests.llm.test_contract_real import GOLDEN, JOURNAL, client  # noqa: F401
from tools.drama import repo
from tools.drama.db import Store
from tools.llm import asset_check
from tools.llm.gate import run_gated
from tools.llm.service import TASKS, VISION_MODEL

pytestmark = pytest.mark.real_llm
ART = '{"era": "架空古代,类北宋边军", "costume": "交领右衽、粗布短褐或皮甲,无纽扣、无口袋、无翻领", "banned": ["uniform", "pocket"]}'


def _world(tmp_path):
    for name in ("asset_sun_front.jpg", "asset_sun_fourview.jpg", "asset_yan_front.jpg"):
        shutil.copyfile(GOLDEN / name, tmp_path / name)
    s = Store(":memory:")
    d = repo.create_drama(s, "回到古代做将军", "9:16", "realistic")
    s.conn.execute("UPDATE drama SET art_json = ? WHERE id = ?", (ART, d))
    sun = repo.add_character(s, d, "孙老栓", appearance="六十岁左右瘦小老兵,满脸皱纹,花白胡须", styling="破旧灰布军服,腰别旱烟杆",
                             final_prompt="Elderly thin ancient Chinese camp veteran, worn grey cloth clothes.",
                             turnaround_path="asset_sun_front.jpg", fourview_path="asset_sun_fourview.jpg")
    yan = repo.add_character(s, d, "燕无咎", appearance="二十五六岁青年,身形精悍", styling="粗布围裙、洗得发白的伙夫旧衣",
                             final_prompt="Young ancient Chinese man in a faded cook's tunic and apron.",
                             turnaround_path="asset_yan_front.jpg")
    return s, sun, yan


def _check(client, ctx):
    task = f"asset_check_{ctx['table']}"
    return run_gated(client=client, model=VISION_MODEL, task=task, system=asset_check.SYSTEM,
                     user=asset_check.user_message(ctx), tool_name=asset_check.TOOL, description=TASKS[task].label,
                     schema=asset_check.schema(ctx), judge=asset_check.judge_factory(ctx), journal_dir=JOURNAL,
                     max_rounds=TASKS[task].max_rounds, max_tokens=TASKS[task].max_tokens, images=asset_check.images(ctx))


def test_modern_uniform_fails_era_and_costume(client, tmp_path):  # noqa: F811
    s, sun, _ = _world(tmp_path)
    res = _check(client, asset_check.context(s, "character", sun, "fourview", tmp_path))
    assert "时代与服饰" in asset_check.failed(res.args)


def test_smoke_puff_fails_the_flaw_check(client, tmp_path):  # noqa: F811
    s, _, yan = _world(tmp_path)
    res = _check(client, asset_check.context(s, "character", yan, "image", tmp_path))
    assert "瑕疵" in asset_check.failed(res.args)
