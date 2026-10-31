# -*- coding: utf-8 -*-
"""资产图审(spec 2026-10-28-成片质量闸门-第一期-design.md 第 6.4 节):每张资产图一张卡 ——
图、代码检查、AI 逐维度结论、全剧对照挑出的毛病;人对每张图:定稿(有不通过项要写理由)/ 重出 / 改描述再重出。

重出的新图和平常一样进历史(写回只填空位),卡片上列出来,人「用这一版」后流程自动再查。

页面底部「角色声音」(spec 2026-10-30-台词配音与口型闸门-design.md 第 3 节):按声音描述一次出 3 版试听音,
每版有 Whisper 按拼音回听的结果;人试听、挑版、定稿(`asset_approval` slot = voice)。声音没有 AI 检查,回听不过要写理由。
"""
from __future__ import annotations

import json
import random

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from tools.cut import prosody
from tools.drama import approval, repo
from tools.gen import assetscan
from tools.gen.assets import locked_seed, plan_asset_image, plan_fourview
from tools.gen.voice import EMO_PRESETS, plan_template, plan_voice
from tools.llm import asset_check, asset_review
from tools.serve import voicer
from tools.serve.dispatch import sha256_file
from tools.serve.panels.common import render, rt

router = APIRouter()
VOICE_TAKES = 3
VOICE_FLYING = ("pending", "queued", "running", "waiting_host")


def _anchor(table: str, row_id: int, slot: str) -> str:
    return f"{table}-{row_id}-{slot}"


def _back(store, table: str, row_id: int, slot: str) -> RedirectResponse:
    d = repo.get(store, table, row_id)["drama_id"]
    return RedirectResponse(f"/drama/{d}/asset-review#{_anchor(table, row_id, slot)}", status_code=303)


def _versions(store, table: str, row_id: int, slot: str, current: str | None) -> list:
    """重出过、还没用上的版本(新的在前)。"""
    col = approval.field(table, slot)
    return store.conn.execute(
        "SELECT * FROM task WHERE target_type = ? AND target_id = ? AND state = 'done'"
        " AND json_extract(params_json, '$.writes') = ? AND result_path IS NOT ? ORDER BY id DESC LIMIT 4",
        (table, row_id, col, current)).fetchall()


def card(store, root, it: dict, issues: dict) -> dict:
    """一张卡要显示的全部东西,以及定稿时算「不通过」的项(代码 + AI + 全剧对照)。"""
    c = dict(it, anchor=_anchor(it["table"], it["id"], it["slot"]), versions=_versions(
        store, it["table"], it["id"], it["slot"], it.get("path")))
    if not it["path"]:
        return c
    try:
        c["code"], c["code_error"] = assetscan.cached(root, it["path"], it["table"], it["slot"]), ""
    except Exception as e:      # 图坏了 / ffmpeg 失败:写在卡上,定稿时算不通过(不吞、不当通过)
        c["code"], c["code_error"] = [], f"{type(e).__name__}: {e}"
    c["drama_issues"] = issues.get((it["table"], it["id"]), []) if it["slot"] == "image" else []
    c["failed"] = ([x.name for x in c["code"] if not x.ok] + (["代码检查出错"] if c["code_error"] else [])
                   + (asset_review.ai_failed(it) if it.get("out") else ["AI 检查没有结果"])   # 没经 AI 看过也要写理由
                   + [x.split("(")[0] for x in c["drama_issues"]])
    c["clean"] = not c["failed"] and it.get("out") is not None
    row = repo.get(store, it["table"], it["id"])
    c["final_prompt"] = row["final_prompt"]
    c["suggestion"] = next((x["suggestion"] for x in (it.get("out") or {}).get("items", [])
                            if x["verdict"] == "不通过" and x["fix"] == "改描述"), "")
    c["locked"] = locked_seed(store, it["table"], it["id"]) if it["slot"] == "image" else None
    return c


def voice_cards(store, root, drama_id: int) -> list[dict]:
    """每个角色一张声音卡:当前试听音 + 回听结果 + 定稿 + 还没用上的版本 + 在途几版;定稿时算「不通过」的项。"""
    out = []
    for ch in store.conn.execute("SELECT * FROM character WHERE drama_id = ? ORDER BY id", (drama_id,)).fetchall():
        path = ch["voice_ref_path"]
        check = voicer.check_of(root, path)
        failed = [] if not path else (["回听没有结果"] if check is None else
                                      [] if check["ok"] else [f"回听没念对(拼音命中 {check['hit']:.2f})"])
        out.append({"id": ch["id"], "name": ch["name"], "desc": ch["voice_desc"], "path": path,
                    "text": ch["voice_ref_text"], "check": check, "failed": failed,
                    "approval": approval.current(store, "character", ch["id"], "voice"),
                    "anchor": _anchor("character", ch["id"], "voice"),
                    "versions": [dict(v, check=voicer.check_of(root, v["result_path"]))
                                 for v in _versions(store, "character", ch["id"], "voice", path)],
                    "template_id": ch["voice_template_id"],
                    "candidates": [dict(t, prosody=voicer.prosody_of(root, t["result_path"])) for t in store.conn.execute(
                        "SELECT * FROM task WHERE kind = 'voice' AND target_id = ? AND state = 'done'"
                        " AND json_extract(params_json, '$.writes') = 'voice_template_candidate' ORDER BY id DESC LIMIT 6",
                        (ch["id"],)).fetchall()],
                    "flying": store.conn.execute(
                        f"SELECT COUNT(*) FROM task WHERE kind = 'voice' AND target_id = ? AND state IN"
                        f" ({','.join('?' * len(VOICE_FLYING))})", (ch["id"], *VOICE_FLYING)).fetchone()[0]})
    return out


@router.get("/drama/{drama_id}/asset-review")
def page(request: Request, drama_id: int):
    runtime = rt(request)
    store = runtime.store
    drama = repo.get(store, "drama", drama_id)
    issues = asset_review.drama_issues(store, drama_id)
    cards = [card(store, runtime.data_root, it, issues) for it in asset_review.items(store, drama_id)]
    state, detail = asset_review.summary(store, drama_id)
    return render(request, "asset_review.html", "asset_review", drama_id=drama_id, drama=drama, cards=cards,
                  state=state, detail=detail, drama_run=asset_review.drama_run(store, drama_id),
                  voices=voice_cards(store, runtime.data_root, drama_id),
                  templates=repo.voice_templates(store, drama_id), presets=list(EMO_PRESETS),
                  clean=sum(1 for c in cards if c.get("clean") and c.get("approval") is None))


@router.post("/asset/{table}/{row_id}/{slot}/check")
def check(request: Request, table: str, row_id: int, slot: str):
    runtime = rt(request)
    path = repo.get(runtime.store, table, row_id)[approval.field(table, slot)]
    runtime.start_llm(asset_review.task_of(table), row_id, {"slot": slot, "path": path})
    return _back(runtime.store, table, row_id, slot)


@router.post("/asset/{table}/{row_id}/{slot}/approve")
def approve(request: Request, table: str, row_id: int, slot: str, reason: str = Form("")):
    runtime = rt(request)
    store = runtime.store
    d = repo.get(store, table, row_id)["drama_id"]
    if slot == "voice":
        c = next(v for v in voice_cards(store, runtime.data_root, d) if v["id"] == row_id)
        approval.approve(store, runtime.data_root, table, row_id, slot, failed=c["failed"], reason=reason,
                         checks={"listen": c["check"]})
        return _back(store, table, row_id, slot)
    it = next(x for x in asset_review.items(store, d) if (x["table"], x["id"], x["slot"]) == (table, row_id, slot))
    c = card(store, runtime.data_root, it, asset_review.drama_issues(store, d))
    approval.approve(store, runtime.data_root, table, row_id, slot, failed=c["failed"], reason=reason,
                     check_run=it["run"]["id"] if it.get("run") is not None else None,
                     checks={"code": [x.__dict__ for x in c["code"]], "ai": it.get("out"), "drama": c["drama_issues"]})
    runtime.advance_flow(d)
    return _back(store, table, row_id, slot)


@router.post("/drama/{drama_id}/asset-review/approve-clean")
def approve_clean(request: Request, drama_id: int):
    """只定稿代码 + AI(+ 全剧对照)全部通过、还没定稿的图。"""
    runtime = rt(request)
    store = runtime.store
    issues = asset_review.drama_issues(store, drama_id)
    for it in asset_review.items(store, drama_id):
        if it["path"] and it.get("approval") is None:
            c = card(store, runtime.data_root, it, issues)
            if c["clean"]:
                approval.approve(store, runtime.data_root, it["table"], it["id"], it["slot"], failed=[],
                                 check_run=it["run"]["id"], checks={"code": [x.__dict__ for x in c["code"]], "ai": it["out"]})
    runtime.advance_flow(drama_id)
    return RedirectResponse(f"/drama/{drama_id}/asset-review", status_code=303)


def _regenerate(store, table: str, row_id: int, slot: str) -> int:
    if slot == "fourview":
        return plan_fourview(store, row_id, seed=random.randrange(1, 2**31))
    locked = locked_seed(store, table, row_id)
    if locked is not None:
        raise ValueError(f"「{repo.get(store, table, row_id)['name']}」的种子锁定为 {locked},换种子重出没意义 —— "
                         "改描述再重出,或先到资产面板解锁")
    return plan_asset_image(store, table, row_id, seed=random.randrange(1, 2**31))


@router.post("/character/{row_id}/voice/design")
def voice_design(request: Request, row_id: int):
    """按声音描述出 3 版试听音(3 个随机种子,各一条任务;第一版写进空位,其余进历史)。"""
    store = rt(request).store
    for _ in range(VOICE_TAKES):
        plan_voice(store, row_id, seed=random.randrange(1, 2**31))
    return _back(store, "character", row_id, "voice")


# ── 念法模板(IndexTTS2 港片念法,spec 2026-10-31-IndexTTS2港片配音-design.md §3)────────────
TEMPLATE_DIR = "voice_templates"


def _templates_back(drama_id: int) -> RedirectResponse:
    return RedirectResponse(f"/drama/{drama_id}/asset-review#voice-templates", status_code=303)


@router.post("/drama/{drama_id}/voice-template/import")
def template_import(request: Request, drama_id: int, name: str = Form(...), source: str = Form(""),
                    file: UploadFile = File(...)):
    """导入一段我们自己合成 / 自己录的音频当念法模板:来源必填(V9),过韵律门槛(V2)才收。"""
    runtime = rt(request)
    repo.get(runtime.store, "drama", drama_id)
    if not source.strip():
        raise ValueError("念法模板要写来源(合成任务号,或这段音频的来历)—— 不得用他人作品里的声音")
    tmp = runtime.data_root / TEMPLATE_DIR / "_incoming.wav"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(file.file.read())
    try:
        problems = prosody.template_problems(*prosody.load_wav(tmp))
    except Exception as e:                       # 读不了 = 不是 16 位 PCM wav:说清楚,不当通过
        tmp.unlink()
        raise ValueError(f"读不了这个音频(要 16 位 PCM 的 wav):{type(e).__name__}: {e}") from e
    if problems:
        tmp.unlink()
        raise ValueError("念法不够格:" + ";".join(problems))
    sha = sha256_file(tmp)
    rel = f"{TEMPLATE_DIR}/{sha[:16]}.wav"
    tmp.replace(runtime.data_root / rel)
    repo.add_voice_template(runtime.store, drama_id, name=name, path=rel, sha256=sha, source=f"upload:{source.strip()}", note="")
    return _templates_back(drama_id)


@router.post("/character/{row_id}/voice-template")
def template_pick(request: Request, row_id: int, template_id: str = Form("")):
    """角色的默认念法模板(他的台词默认都用它;段落里可以逐句换)。空 = 不选。"""
    store = rt(request).store
    ch = repo.get(store, "character", row_id)
    tid = int(template_id) if template_id.strip() else None
    if tid is not None and repo.get(store, "voice_template", tid)["drama_id"] != ch["drama_id"]:
        raise ValueError("这个念法模板不属于本剧")
    repo.update(store, "character", row_id, voice_template_id=tid)
    return _back(store, "character", row_id, "voice")


@router.post("/character/{row_id}/voice-template/generate")
def template_generate(request: Request, row_id: int, preset: str = Form(...)):
    """按预设出 3 个念法候选(用角色的定稿音色;云机 IndexTTS2)。"""
    store = rt(request).store
    for _ in range(VOICE_TAKES):
        plan_template(store, row_id, preset=preset, seed=random.randrange(1, 2**31))
    return _back(store, "character", row_id, "voice")


@router.post("/task/{task_id}/save-template")
def template_save(request: Request, task_id: int, name: str = Form(...)):
    """把一个念法候选存成模板:过了韵律门槛才行;来源记 synth:<任务号>。"""
    runtime = rt(request)
    t = repo.get(runtime.store, "task", task_id)
    if t["state"] != "done" or not t["result_path"]:
        raise ValueError("这个候选还没生成完")
    report = voicer.prosody_of(runtime.data_root, t["result_path"])
    if report is None:
        raise ValueError("这个候选没有韵律量值,不能存成模板")
    if report["problems"]:
        raise ValueError("念法不够格:" + ";".join(report["problems"]))
    drama = repo.get(runtime.store, "character", t["target_id"])["drama_id"]
    repo.add_voice_template(runtime.store, drama, name=name, path=t["result_path"], sha256=t["result_sha256"],
                            source=f"synth:{task_id}", note=json.loads(t["params_json"]).get("preset", ""))
    return _templates_back(drama)


@router.post("/asset/{table}/{row_id}/{slot}/regenerate")
def regenerate(request: Request, table: str, row_id: int, slot: str):
    store = rt(request).store
    _regenerate(store, table, row_id, slot)
    return _back(store, table, row_id, slot)


@router.post("/asset/{table}/{row_id}/{slot}/rewrite")
def rewrite(request: Request, table: str, row_id: int, slot: str, final_prompt: str = Form(...)):
    """改生成描述再重出(锁了种子的照样用锁定种子 —— 长相尽量不跑,改的是描述)。"""
    store = rt(request).store
    text = final_prompt.strip()
    if not text:
        raise ValueError("生成描述不能为空")
    repo.update(store, table, row_id, final_prompt=text)
    if slot == "fourview":
        plan_fourview(store, row_id, seed=random.randrange(1, 2**31))
    else:
        locked = locked_seed(store, table, row_id)
        plan_asset_image(store, table, row_id, seed=locked if locked is not None else random.randrange(1, 2**31))
    return _back(store, table, row_id, slot)


@router.post("/drama/{drama_id}/asset-review/drama-check")
def drama_check(request: Request, drama_id: int):
    runtime = rt(request)
    runtime.start_llm("asset_check_drama", drama_id, {"key": asset_check.sheet_key(runtime.store, drama_id)})
    return RedirectResponse(f"/drama/{drama_id}/asset-review", status_code=303)
