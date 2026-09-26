# -*- coding: utf-8 -*-
"""云机脚本里唯一的纯逻辑:字节级 token 合并。用假分词器(token = 一段 UTF-8 字节)测。"""
import ast
from pathlib import Path

from tools.serve.align_remote import merge_tokens

JADE = "玉佩".encode("utf-8")                  # 6 字节,whisper 实测把它拆成 4 个 token
VOCAB = {1: "这块".encode(), 2: JADE[:2], 3: JADE[2:3], 4: JADE[3:5], 5: JADE[5:], 6: "，".encode(), 7: "是".encode(),
         8: b" ", 50257: b""}


def decode(ids):
    return b"".join(VOCAB[i] for i in ids).decode("utf-8", "replace")


def test_byte_tokens_are_merged_before_decoding():
    """「玉」的 3 个字节分在两个 token 里:两个攒齐就出字,时间段跨这两个 token;「佩」同理。"""
    chars = merge_tokens([50257, 1, 2, 3, 4, 5, 6, 8, 7], [0.0, 5.5, 5.6, 5.7, 5.8, 5.9, 5.9, 6.0, 6.7], decode)
    assert chars == [("这", 0.0, 5.5), ("块", 0.0, 5.5), ("玉", 5.5, 5.7), ("佩", 5.7, 5.9), ("，", 5.9, 5.9),
                     ("是", 6.0, 6.7)]


def test_trailing_broken_bytes_are_dropped_not_invented():
    assert merge_tokens([7, 2], [1.0, 1.2], decode) == [("是", 0.0, 1.0)]


def test_top_level_imports_are_stdlib_only():
    """脚本经 stdin 送到云机,本机导入它(单测)也不能拉 torch / transformers。"""
    tree = ast.parse(Path("tools/serve/align_remote.py").read_text(encoding="utf-8"))
    top = {a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names}
    top |= {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom)}
    assert top <= {"__future__", "json", "subprocess", "sys", "time", "pathlib"}
