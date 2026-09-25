# -*- coding: utf-8 -*-
"""从图反推描述:确定性代码路径(校验数字、schema、判卷、调度、写回)。LLM 契约另见 test_contract_real.py。"""
import json
import struct

import pytest

from tools.cut import media
from tools.drama import repo
from tools.gen.assets import _png
from tools.llm import describe, runs, service
from tools.llm.fake import DESCRIBE_SAMPLE, FakeLLM
from tools.llm.gate import image_part, run_gated, strict_schema_problems
from tools.llm.service import LLMBackend


def test_check_number_is_stable_and_three_digits():
    a, b = _png(40, 60, 100), _png(40, 60, 101)
    assert describe.check_number(a) == describe.check_number(a) and 100 <= describe.check_number(a) <= 999
    assert describe.check_number(a) != describe.check_number(b)


def test_marked_image_is_downscaled_with_a_strip_below(tmp_path):
    (tmp_path / "big.png").write_bytes(_png(900, 1600, 128))
    img, n = describe.marked(tmp_path / "big.png")
    (tmp_path / "m.png").write_bytes(img)
    w, h = struct.unpack(">II", img[16:24])                              # PNG 头里的宽高
    assert (w, h) == (432, 768 + 96)                                     # 最长边 768,加 1/8 高的色带
    strip = media.gray_frame(tmp_path / "m.png", 0, w, h)[-96 * w:]     # 整条色带
    assert max(strip) > 200 and min(strip) < 60      # 蓝底(灰度约 41)上有白字(数字印上去了)
    assert n == describe.check_number(_png(900, 1600, 128))


@pytest.mark.parametrize("table", ["character", "scene", "prop"])
def test_schemas_are_strict_and_cover_the_writable_fields(table):
    s = describe.schema({"table": table})
    assert strict_schema_problems(s) == []
    assert set(s["properties"]) == set(describe.FIELDS[table]) | {"check_number"}
    assert set(describe.FIELDS[table]) <= repo._COLUMNS[table]           # 写回只落在白名单里


def test_the_number_never_appears_in_the_text():
    ctx = {"table": "prop", "name": "玉佩", "check_number": 473}
    assert "473" not in describe.user_message(ctx) and "473" not in describe.SYSTEM


def _args(table, **over):
    return {**{k: DESCRIBE_SAMPLE[k] for k in describe.schema({"table": table})["properties"]}, "check_number": "473",
            **over}


def test_judge_catches_a_blind_model_and_bad_fields():
    judge = describe.judge_factory({"table": "character", "check_number": 473})
    assert judge(_args("character")) == []
    assert judge(_args("character", check_number=" 473 ")) == []
    blind = judge(_args("character", check_number="000"))
    assert "check_number 读错了" in blind[0] and "没有收到图片" in blind[0]
    assert any("final_prompt 要写英文" in p for p in judge(_args("character", final_prompt="一位年轻女性")))
    assert any("不许用「像某某」" in p for p in judge(_args("character", appearance="长得像某明星")))
    assert any("styling 不能为空" in p for p in judge(_args("character", styling=" ")))


def _asset(store, root, gray=128):
    d = repo.create_drama(store, "新剧", "9:16", "realistic")
    rid = repo.add_prop(store, d, "玉佩")
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "assets/p.png").write_bytes(_png(64, 64, gray))
    repo.update(store, "prop", rid, image_path="assets/p.png")
    return rid


def test_context_needs_an_image_on_disk(store, tmp_path):
    d = repo.create_drama(store, "新剧", "9:16", "realistic")
    rid = repo.add_prop(store, d, "玉佩")
    with pytest.raises(ValueError, match="还没有参考图"):
        describe.context(store, "prop", rid, tmp_path)
    repo.update(store, "prop", rid, image_path="assets/gone.png")
    with pytest.raises(ValueError, match="参考图不在本机"):
        describe.context(store, "prop", rid, tmp_path)


def test_images_go_to_the_model_and_the_journal_keeps_only_hashes(tmp_path):
    seen = {}

    class Rec(FakeLLM):
        def call_tool(self, *, model, messages, **kw):
            seen["content"] = messages[1]["content"]
            return super().call_tool(model=model, messages=messages, **kw)

    res = run_gated(client=Rec({"submit": {}}), model="m", task="t", system="s", user="u", tool_name="submit",
                    description="d", schema={"type": "object", "additionalProperties": False, "required": [],
                                             "properties": {}},
                    judge=lambda a: [], journal_dir=tmp_path, images=[b"\x89PNG\r\n\x1a\nA"])
    assert [c["type"] for c in seen["content"]] == ["text", "image_url"]
    assert seen["content"][1] == image_part(b"\x89PNG\r\n\x1a\nA")
    assert seen["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    text = res.journal_path.read_text(encoding="utf-8")
    assert json.loads(text)["images"][0]["bytes"] == 9 and "base64" not in text


def test_fake_llm_is_blind_and_gets_caught(store, tmp_path):
    rid = _asset(store, tmp_path)
    fake = LLMBackend(FakeLLM(), "m", "fake")
    run, _ = service.begin(store, "describe_prop", rid, {}, fake, tmp_path)
    service.finish(store, run, fake, tmp_path / "journal", data_root=tmp_path)
    row = runs.get(store, run)
    assert row["state"] == "failed" and "check_number 读错了" in row["error"]


class _Sees(FakeLLM):
    """知道正确数字的「看得见图」的替身:只用来测调度与写回,不声称覆盖 LLM 契约。"""
    def __init__(self, number):
        super().__init__({describe.TOOL: lambda schema: {k: _args("prop")[k] for k in schema["properties"]}
                          | {"check_number": str(number)}})

    def call_tool(self, *, model, **kw):
        self.model = model
        return super().call_tool(model=model, **kw)


def test_service_uses_the_vision_model_and_image_hash(store, tmp_path):
    rid = _asset(store, tmp_path)
    llm = LLMBackend(_Sees(describe.check_number(_png(64, 64, 128))), service.DEFAULT_MODEL, "real")
    run, _ = service.begin(store, "describe_prop", rid, {}, llm, tmp_path)
    assert runs.get(store, run)["model"] == service.VISION_MODEL                # 默认模型看不见图,换视觉模型
    service.finish(store, run, llm, tmp_path / "journal", data_root=tmp_path)
    assert runs.get(store, run)["state"] == "proposed" and llm.client.model == service.VISION_MODEL
    service.approve(store, run, tmp_path)
    prop = repo.get(store, "prop", rid)
    assert prop["description"] == DESCRIBE_SAMPLE["description"] and prop["final_prompt"] == DESCRIBE_SAMPLE["final_prompt"]

    again, reused = service.begin(store, "describe_prop", rid, {}, llm, tmp_path)
    assert reused and again == run                                              # 同一张图:锁定版直接复用
    (tmp_path / "assets/p.png").write_bytes(_png(64, 64, 30))
    _, reused = service.begin(store, "describe_prop", rid, {}, llm, tmp_path)
    assert not reused                                                           # 换了图:不再命中锁定版


def test_describe_without_data_root_is_refused(store, tmp_path):
    rid = _asset(store, tmp_path)
    with pytest.raises(ValueError, match="需要素材根"):
        service.begin(store, "describe_prop", rid, {}, LLMBackend(FakeLLM(), "m", "fake"))
