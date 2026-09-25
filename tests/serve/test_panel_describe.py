# -*- coding: utf-8 -*-
"""资产面板:上传参考图 + 从图反推描述(假后端,产物标 fake,批准按钮是灰的)。"""
from tools.drama import repo
from tools.gen.assets import _png
from tools.drama.tasks import tasks_for
from tools.llm import runs

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32


def _drama(store):
    d = repo.create_drama(store, "新剧", "9:16", "realistic")
    return d, repo.add_prop(store, d, "玉佩")


def test_upload_sets_the_reference_image_and_shows_in_history(client, runtime, store):
    d, p = _drama(store)
    page = client.get(f"/drama/{d}/assets").text
    assert "data-upload-file" in page and "data-llm-describe disabled" in page        # 没图时反推按钮灰着
    r = client.post(f"/asset/prop/{p}/upload", files={"file": ("jade.png", PNG, "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    rel = repo.get(store, "prop", p)["image_path"]
    assert rel.startswith("assets/uploads/") and (runtime.data_root / rel).read_bytes() == PNG
    page = client.get(f"/drama/{d}/assets").text
    assert f'src="/media/{rel}"' in page and "data-llm-describe disabled" not in page
    assert [t["model"] for t in tasks_for(store, "prop", p)] == ["manual:upload"]


def test_upload_over_used_image_asks_first(client, store, sample):
    r = client.post(f"/asset/character/{sample['lin']}/upload", files={"file": ("lin.png", PNG, "image/png")})
    assert r.status_code == 200 and "重跑 1 条视频" in r.text and 'name="rel"' in r.text
    assert repo.get(store, "character", sample["lin"])["turnaround_path"] == "assets/lin.png"   # 还没换
    rel = r.text.split('name="rel" value="')[1].split('"')[0]
    client.post(f"/asset/character/{sample['lin']}/upload/confirm",
                data={"rel": rel, "filename": "lin.png", "confirm": "1"})
    assert repo.get(store, "character", sample["lin"])["turnaround_path"] == rel


def test_bad_upload_is_a_readable_400(client, store):
    d, p = _drama(store)
    r = client.post(f"/asset/prop/{p}/upload", files={"file": ("a.gif", b"GIF89a", "image/gif")})
    assert r.status_code == 400 and "只收 PNG 或 JPEG" in r.text


def test_fake_describe_is_caught_and_shown_as_failed(client, runtime, store):
    """假 LLM 看不见图:校验数字必错,闸门拦下 —— 页面上是失败 + 原因,不是一份编出来的描述。"""
    d, p = _drama(store)
    client.post(f"/asset/prop/{p}/upload", files={"file": ("jade.png", _png(64, 64, 90), "image/png")})
    r = client.post(f"/llm/start/describe_prop/{p}", follow_redirects=False)
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    runtime.wait_llm()
    assert runs.get(store, rid)["state"] == "failed"
    page = client.get(f"/llm/run/{rid}").text
    assert "check_number 读错了" in page and "gpt-5.6-luna" in page                  # 记的是视觉模型
    assert f'data-llm-run="{rid}"' in client.get(f"/drama/{d}/assets").text


def test_review_page_shows_current_vs_proposed(client, store):
    d, p = _drama(store)
    repo.update(store, "prop", p, description="旧描述")
    rid = runs.start(store, task="describe_prop", target_type="prop", target_id=p, model="gpt-5.6-luna",
                     input_sha="s", llm_mode="real", params={})
    runs.propose(store, rid, {"description": "椭圆形绿色玉佩", "final_prompt": "A green jade pendant.",
                              "check_number": "473"}, "j")
    page = client.get(f"/llm/run/{rid}").text
    assert "data-describe-preview" in page and "旧描述" in page and "椭圆形绿色玉佩" in page
    assert f"/drama/{d}/assets" in page                                               # 返回资产面板
