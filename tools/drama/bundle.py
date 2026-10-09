# -*- coding: utf-8 -*-
"""工程导出 / 导入(设计 §九「工程导出/导入(含图片历史与帧绑定)」)。

一部剧打成一个 zip:`manifest.json`(全部行 + 每个文件的 sha256)+ `files/<相对路径>`。
- **带历史**:这部剧的全部 task(图片 / 视频的每一版)、段落与资产的绑定、LLM 记录(含审批锁定);
- **不带**:云机登记(私钥路径是本机的)、成片导出记录(那是发布产物,ProduceID 不能在两处重复);
- **导出前拒绝**:有在途任务 / 在途 LLM(导出后永远收不回来)、被引用的文件不在本机 —— 列出原因,不导出残缺包。

导入 = **新建一部剧**,不碰已有数据:
- 全部 id 重新分配,外键逐个映射;
- 文件放到 `imports/<导入编号>/` 下,库里所有路径统一加这个前缀 —— 与已有产物(如 outputs/tasks/2/…)不会撞名;
- 每个文件按 manifest 的 sha256 校验,zip 里的路径不许跳出目录;库结构版本不一致直接拒绝;
- 先落文件、再一个事务写库;写库失败就删掉刚落的文件。原来机器上的 host 不导入,task 的 host_id 置空。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from tools.drama.db import SCHEMA_VERSION, Store, now
from tools.drama.repo import next_segment_id

FORMAT = 1
IN_FLIGHT = ("pending", "queued", "running", "waiting_host")
PATH_FIELDS = {"character": ("turnaround_path", "fourview_path", "voice_ref_path"), "scene": ("image_path",), "prop": ("image_path",),
               "segment": ("first_frame_path", "last_frame_path", "video_path", "subtitle_path")}
PARAM_PATH_KEYS = ("image_refs", "audio_ref", "video")      # task.params_json 里存路径的键(video:字幕对齐的输入)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rows(store: Store, sql: str, args: tuple) -> list[dict]:
    return [dict(r) for r in store.conn.execute(sql, args)]


def _in(ids: list[int]) -> str:
    return ",".join(str(int(i)) for i in ids) or "NULL"


def collect(store: Store, drama_id: int) -> dict:
    """这部剧要导出的全部行。"""
    t: dict[str, list[dict]] = {"drama": _rows(store, "SELECT * FROM drama WHERE id = ?", (drama_id,))}
    if not t["drama"]:
        raise KeyError(f"drama {drama_id} 不存在")
    for table in ("episode", "character", "scene", "prop"):
        t[table] = _rows(store, f"SELECT * FROM {table} WHERE drama_id = ? ORDER BY id", (drama_id,))
    eps = [r["id"] for r in t["episode"]]
    t["segment"] = _rows(store, f"SELECT * FROM segment WHERE episode_id IN ({_in(eps)}) ORDER BY id", ())
    segs = [r["id"] for r in t["segment"]]
    for link in ("segment_character", "segment_prop"):
        t[link] = _rows(store, f"SELECT * FROM {link} WHERE segment_id IN ({_in(segs)}) ORDER BY segment_id, position", ())
    owned = {"segment": segs, **{k: [r["id"] for r in t[k]] for k in ("character", "scene", "prop")}}
    t["task"] = _rows(store, " UNION ".join(
        f"SELECT * FROM task WHERE target_type = '{k}' AND target_id IN ({_in(v)})" for k, v in owned.items())
        + " ORDER BY id", ())
    llm_owned = {"drama": [drama_id], "episode": eps, **{k: owned[k] for k in ("character", "scene", "prop")}}
    t["llm_run"] = _rows(store, " UNION ".join(
        f"SELECT * FROM llm_run WHERE target_type = '{k}' AND target_id IN ({_in(v)})" for k, v in llm_owned.items())
        + " ORDER BY id", ())
    return t


def _paths(tables: dict) -> set[str]:
    out = set()
    for table, fields in PATH_FIELDS.items():
        for r in tables[table]:
            out |= {r[f] for f in fields if r[f]}
    for r in tables["task"]:
        out |= set(json.loads(r["refs_json"]))
        if r["result_path"]:
            out.add(r["result_path"])
    return out


def export_bundle(store: Store, data_root: Path, drama_id: int) -> str:
    """打包到 `<data_root>/outputs/bundles/`,返回相对 data_root 的路径。"""
    root = Path(data_root).resolve()
    tables = collect(store, drama_id)
    problems = [f"task #{r['id']} 还在「{r['state']}」—— 等它结束再导出(导出后收不回来)"
                for r in tables["task"] if r["state"] in IN_FLIGHT]
    problems += [f"LLM 任务 #{r['id']} 还在生成 —— 等它结束再导出" for r in tables["llm_run"] if r["state"] == "running"]
    files = {p: root / p for p in sorted(_paths(tables))}
    journals = {}
    for r in tables["llm_run"]:
        if r["journal_path"]:
            j = Path(r["journal_path"]).resolve()
            if root not in j.parents or not j.is_file():
                problems.append(f"LLM 任务 #{r['id']} 的记录文件不在素材根下:{r['journal_path']}")
            else:
                rel = j.relative_to(root).as_posix()
                files[rel] = j
                journals[r["id"]] = rel
    problems += [f"文件不在本机:{p}" for p, f in files.items() if not f.is_file()]
    if problems:
        raise ValueError(";".join(problems))
    manifest = {"format": FORMAT, "schema_version": SCHEMA_VERSION, "exported_at": now(), "tables": tables,
                "llm_journals": journals, "files": {p: _sha256(f) for p, f in files.items()}}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rel = f"outputs/bundles/drama-{drama_id}-{stamp}.zip"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(root / rel, "w", zipfile.ZIP_STORED) as z:     # 图片视频本就压缩过
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        for p, f in files.items():
            z.write(f, f"files/{p}")
    return rel


def _safe(rel: str) -> bool:
    p = PurePosixPath(rel)
    return bool(rel) and not p.is_absolute() and ".." not in p.parts and "\\" not in rel and ":" not in rel


def import_bundle(store: Store, data_root: Path, bundle: Path) -> int:
    """导入成一部新剧,返回新 drama id。"""
    root = Path(data_root).resolve()
    with zipfile.ZipFile(bundle) as z:
        try:
            m = json.loads(z.read("manifest.json"))
        except KeyError as e:
            raise ValueError("不是工程包:缺少 manifest.json") from e
        if m.get("format") != FORMAT:
            raise ValueError(f"工程包格式 {m.get('format')} 不认识(本版只认 {FORMAT})")
        if m.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"工程包来自库结构 v{m.get('schema_version')},本机是 v{SCHEMA_VERSION} —— 不许跨版本导入")
        bad = [p for p in m["files"] if not _safe(p)]
        if bad:
            raise ValueError(f"工程包里有不安全的路径:{bad[:5]}")
        missing = [p for p in _paths(m["tables"]) | set(m["llm_journals"].values()) if p not in m["files"]]
        if missing:
            raise ValueError(f"工程包不完整,缺文件:{sorted(missing)[:5]}")
        prefix = f"imports/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        dest = root / prefix
        try:
            for p, sha in m["files"].items():
                out = dest / p
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(f"files/{p}") as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                if _sha256(out) != sha:
                    raise ValueError(f"文件 {p} 的 sha256 与清单不符 —— 工程包损坏或被改过")
            return _insert(store, m, prefix, root)
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise


def _insert(store: Store, m: dict, prefix: str, root: Path) -> int:
    t = m["tables"]

    def mv(p):
        return f"{prefix}/{p}" if p else p

    def insert(c, table: str, row: dict, **over) -> int:
        row = {k: v for k, v in row.items() if k != "id"} | over      # over 里可以显式给 id
        return c.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                         tuple(row.values())).lastrowid

    ids: dict[str, dict[int, int]] = {k: {} for k in ("drama", "episode", "character", "scene", "prop", "segment")}
    with store.write() as c:
        [d] = t["drama"]
        ids["drama"][d["id"]] = new_drama = insert(c, "drama", d)
        for r in t["episode"]:
            ids["episode"][r["id"]] = insert(c, "episode", r, drama_id=new_drama)
        for table in ("character", "scene", "prop"):
            for r in t[table]:
                over = {"drama_id": new_drama, **{f: mv(r[f]) for f in PATH_FIELDS[table]}}
                if table == "scene" and r["episode_id"] is not None:
                    over["episode_id"] = ids["episode"][r["episode_id"]]
                ids[table][r["id"]] = insert(c, table, r, **over)
        for r in t["segment"]:
            over = {"episode_id": ids["episode"][r["episode_id"]], **{f: mv(r[f]) for f in PATH_FIELDS["segment"]}}
            if r["scene_id"] is not None:
                over["scene_id"] = ids["scene"][r["scene_id"]]
            ids["segment"][r["id"]] = insert(c, "segment", r, id=next_segment_id(c), **over)   # 编号永不复用
        for link, col, table in (("segment_character", "character_id", "character"), ("segment_prop", "prop_id", "prop")):
            for r in t[link]:
                c.execute(f"INSERT INTO {link} (segment_id, {col}, position) VALUES (?, ?, ?)",
                          (ids["segment"][r["segment_id"]], ids[table][r[col]], r["position"]))
        for r in t["task"]:
            params = json.loads(r["params_json"])
            for k in PARAM_PATH_KEYS:
                if isinstance(params.get(k), list):
                    params[k] = [mv(p) for p in params[k]]
                elif params.get(k):
                    params[k] = mv(params[k])
            for node in params.get("graph", {}).values():     # 改参重跑的整份工作流:「加载图像」节点也写着素材库路径
                if node.get("class_type") == "LoadImage" and node["inputs"].get("image"):
                    node["inputs"]["image"] = mv(node["inputs"]["image"])
            insert(c, "task", r, target_id=ids[r["target_type"]][r["target_id"]], host_id=None,
                   result_path=mv(r["result_path"]), refs_json=json.dumps([mv(p) for p in json.loads(r["refs_json"])],
                                                                          ensure_ascii=False),
                   params_json=json.dumps(params, ensure_ascii=False, sort_keys=True))
        for r in t["llm_run"]:
            j = m["llm_journals"].get(str(r["id"]))
            insert(c, "llm_run", r, target_id=ids[r["target_type"]][r["target_id"]],
                   journal_path=str(root / prefix / j) if j else None)
    return new_drama
