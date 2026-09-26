# -*- coding: utf-8 -*-
import pytest

from tools.gen.workflows import H3_MODEL, QWEN_MODEL, build_h3, build_qwen


def _dangling(wf):
    """所有 [节点id, 输出序号] 形式的连线都必须指向图里还存在的节点。剪节点最容易留下断线。"""
    bad = []
    for nid, node in wf.items():
        for key, val in node["inputs"].items():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str) and val[0] not in wf:
                bad.append(f"{nid}.{key} -> {val[0]}")
    return bad


def _classes(wf):
    return {n["class_type"] for n in wf.values()}


# ── Qwen ──────────────────────────────────────────────

def test_qwen_composition_uses_target_size_and_drops_ref_latent():
    wf = build_qwen("Picture 1 在 Picture 2 拿着 Picture 3", ["a.png", "b.png", "c.png"], 7, size=(928, 1664))
    assert _dangling(wf) == []
    assert wf["27"]["inputs"]["latent_image"] == ["30", 0]
    assert (wf["30"]["inputs"]["width"], wf["30"]["inputs"]["height"]) == (928, 1664)
    assert "25" not in wf and "26" not in wf
    assert wf["1"]["inputs"]["unet_name"] == QWEN_MODEL
    assert wf["27"]["inputs"]["seed"] == 7


def test_qwen_fast_preset_is_4_steps_cfg_1():
    wf = build_qwen("Picture 1", ["a.png"], 1)
    assert wf["7"]["inputs"]["value"] is True
    assert (wf["10"]["inputs"]["value"], wf["13"]["inputs"]["value"]) == (4, 1.0)


def test_qwen_single_ref_follows_ref_size_and_removes_unused_slots():
    wf = build_qwen("<sks> front view eye-level shot wide shot", ["a.png"], 1, angles_lora=True)
    assert _dangling(wf) == []
    assert "30" not in wf and wf["27"]["inputs"]["latent_image"] == ["26", 0]
    assert "19" not in wf and "20" not in wf
    assert [k for k in wf["21"]["inputs"] if k.startswith("image")] == ["image1"]
    assert wf["4"]["inputs"]["value"] is True


def test_qwen_rejects_bad_tags_and_bad_sizes():
    with pytest.raises(ValueError, match="只接入了 2 张"):
        build_qwen("Picture 3", ["a.png", "b.png"], 1)
    with pytest.raises(ValueError, match="16 的倍数"):
        build_qwen("Picture 1", ["a.png"], 1, size=(900, 1600))
    with pytest.raises(ValueError, match="1–3 张"):
        build_qwen("x", ["a", "b", "c", "d"], 1)


# ── H3 ────────────────────────────────────────────────

def test_h3_is_pure_20_steps_without_any_lora():
    wf = build_h3("<Picture 1> 是首帧。林小雨说话", ["f.png"], None, 192, 9, size=(768, 1344))
    assert _dangling(wf) == []
    assert "LoraLoaderModelOnly" not in _classes(wf) and "ComfySwitchNode" not in _classes(wf)
    assert wf["18"]["inputs"]["steps"] == 20
    assert wf["1"]["inputs"]["unet_name"] == H3_MODEL
    r2v = wf["12"]["inputs"]
    assert (r2v["length"], r2v["width"], r2v["height"]) == (192, 768, 1344)
    assert "ref_audios.ref_audio_0" not in r2v and "11" not in wf
    assert wf["19"]["inputs"]["noise_seed"] == 9


def test_h3_with_reference_audio():
    wf = build_h3("<Picture 1> 说话,音色同 <Audio 1>", ["f.png"], "voice.wav", 192, 9, size=(768, 1344))
    assert _dangling(wf) == []
    assert wf["11"]["inputs"]["audio"] == "voice.wav"
    assert wf["12"]["inputs"]["ref_audios.ref_audio_0"] == ["11", 0]


def test_h3_rejects_audio_tag_without_audio():
    # 云机上那个真实 bug 的形态
    with pytest.raises(ValueError, match="Audio 1"):
        build_h3("<Picture 1> and <Audio 1> exactly as it is", ["f.png"], None, 192, 1, size=(768, 1344))


def test_h3_anchors_chain_add_guides_into_the_conditioning():
    """锚点 = LoadImage + MiniMaxH3AddGuide,条件依次串过去,最后接到引导器;latent 仍是 ref2va 的联合 latent
    (2026-09-26 云机实测:ref2va + 串 AddGuide 能把首帧钉在第 0 帧、尾帧钉在最后一帧)。"""
    wf = build_h3("<Picture 1> 是首帧。<Picture 2> 是林小雨的四视图", ["f.png", "fv.png"], None, 192, 9,
                  size=(768, 1344), anchors=[("f.png", 0), ("last.png", -1)])
    assert _dangling(wf) == []
    guides = [n for n in wf.values() if n["class_type"] == "MiniMaxH3AddGuide"]
    assert [g["inputs"]["frame_idx"] for g in guides] == [0, -1]
    assert guides[0]["inputs"]["positive"] == ["12", 0] and guides[1]["inputs"]["positive"] == ["41", 0]
    assert all(g["inputs"]["latent"] == ["12", 1] and g["inputs"]["vae"] == ["6", 0] for g in guides)
    assert wf["13"]["inputs"]["conditioning"] == ["43", 0]
    assert [wf[g["inputs"]["image"][0]]["inputs"]["image"] for g in guides] == ["f.png", "last.png"]


def test_h3_without_anchors_is_unchanged():
    wf = build_h3("<Picture 1> x", ["f.png"], None, 192, 9, size=(768, 1344))
    assert "MiniMaxH3AddGuide" not in _classes(wf) and wf["13"]["inputs"]["conditioning"] == ["12", 0]
