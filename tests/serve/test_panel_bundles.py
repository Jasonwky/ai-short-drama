# -*- coding: utf-8 -*-
"""立项面板的工程导出 / 导入。"""
import io
import zipfile

from tools.drama import repo


def _files(root):
    for rel in ("assets/lin.png", "assets/shen.png", "assets/yard.png", "assets/jade.png", "frames/s1.png",
                "videos/s1.mp4"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(rel.encode())


def test_export_lists_the_bundle_and_import_makes_a_new_drama(client, runtime, store, sample):
    _files(runtime.data_root)
    r = client.post(f"/drama/{sample['drama']}/bundle", follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/").text
    [link] = [x for x in page.split('"') if x.startswith("/media/outputs/bundles/")]
    assert "data-bundle-link" in page and client.get(link).status_code == 200

    data = client.get(link).content
    r = client.post("/bundles/import", files={"file": ("p.zip", data, "application/zip")}, follow_redirects=False)
    new = int(r.headers["location"].split("/")[2])
    assert r.status_code == 303 and new != sample["drama"]
    assert repo.get(store, "drama", new)["title"] == "玉佩"
    log = (runtime.data_root / "journal/worker.log").read_text(encoding="utf-8")
    assert "工程导出 drama" in log and f"→ drama {new}" in log


def test_export_refusal_is_a_readable_400(client, sample):
    r = client.post(f"/drama/{sample['drama']}/bundle")
    assert r.status_code == 400 and "文件不在本机" in r.text


def test_import_of_garbage_is_a_readable_400(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.txt", "hi")
    r = client.post("/bundles/import", files={"file": ("x.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 400 and "缺少 manifest.json" in r.text
