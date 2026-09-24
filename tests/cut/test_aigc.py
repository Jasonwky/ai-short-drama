# -*- coding: utf-8 -*-
import json

import pytest

from tools.cut import aigc

SHA = "f08a384e6b4664aeadc6d5a265defefc941241557109b4865e567e2f97f67fd4"


def test_build_follows_appendix_e_and_note_1():
    s = aigc.build("91310000MA1234567X", "ASD-1", SHA)
    assert s == ('{"AIGC":{"Label":"1","ContentProducer":"91310000MA1234567X","ProduceID":"ASD-1",'
                 f'"ReservedCode1":"{SHA}","ContentPropagator":"91310000MA1234567X","PropagateID":"ASD-1",'
                 '"ReservedCode2":""}}')


@pytest.mark.parametrize("bad, why", [("", "★必答"), ("有限公司", "不允许的字符"), ("A B", "不允许的字符"),
                                      ('A"B', "不允许的字符"), ("A\\B", "不允许的字符"), ("X" * 65, "太长")])
def test_producer_code_is_required_and_ascii_only(bad, why):
    assert any(why in p for p in aigc.producer_problems(bad))
    with pytest.raises(ValueError, match=why):
        aigc.build(bad, "ASD-1", SHA)


def test_produce_id_is_ascii_and_unique():
    a, b = aigc.new_produce_id(), aigc.new_produce_id()
    assert a != b and a.startswith("ASD-") and not aigc.value_problems("ProduceID", a)


def _check(tags):
    return aigc.check(tags, producer="P1", produce_id="ASD-1", reserved1=SHA)


def test_check_passes_what_build_wrote():
    assert _check({"major_brand": "isom", "AIGC": aigc.build("P1", "ASD-1", SHA)}) == []


def test_check_rejects_two_labels_and_wrong_shapes():
    good = aigc.build("P1", "ASD-1", SHA)
    assert "恰好 1 个" in _check({"AIGC": good, "aigc_copy": good})[0]
    assert "恰好 1 个" in _check({"major_brand": "isom"})[0]
    assert "合法 JSON" in _check({"AIGC": "{broken"})[0]
    assert "整串" in _check({"AIGC": json.dumps({"Label": "1"})})[0]


def test_check_rejects_appendix_f_misspelled_keys():
    body = json.loads(aigc.build("P1", "ASD-1", SHA))["AIGC"]
    body["ReserveCode1"] = body.pop("ReservedCode1")          # 附录 F 示例的拼法
    probs = _check({"AIGC": json.dumps({"AIGC": body})})
    assert any("键应依次为" in p for p in probs) and any("ReservedCode1 应为" in p for p in probs)


def test_check_catches_wrong_values():
    body = json.loads(aigc.build("P1", "ASD-1", SHA))["AIGC"]
    body.update(Label="2", PropagateID="other", ReservedCode2="中文")
    probs = _check({"AIGC": json.dumps({"AIGC": body}, ensure_ascii=False)})
    assert any("Label 应为 '1'" in p for p in probs)
    assert any("PropagateID 应为 'ASD-1'" in p for p in probs)
    assert any("ReservedCode2 含不允许的字符" in p for p in probs)
    assert "ReservedCode1 应为" in _check({"AIGC": aigc.build("P1", "ASD-1", "0" * 64)})[0]
