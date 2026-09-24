# -*- coding: utf-8 -*-
"""**真 LLM 契约测试**(CLAUDE.md:要测 LLM 就用真 LLM,不许拿 Fake 冒充)。

- 默认 `pytest -q` 不跑(花钱、每条 30–120 秒);`python -m pytest -m real_llm -q` 或验收入口跑;
- key 从 gitignored 的 `llm.txt` 读;没有 key 就**失败**(既然要跑契约,就不许悄悄跳过);
- 用黄金样本守回归:换模型 / 改提示词掉质量,这里会红。
"""
from pathlib import Path

import pytest

from tools.drama import rules
from tools.llm import outline, script_writer, storyboard, voices
from tools.llm.client import ChatClient, read_key
from tools.llm.gate import run_gated, schema_errors
from tools.llm.service import DEFAULT_MODEL, TASKS

pytestmark = pytest.mark.real_llm
GOLDEN = Path(__file__).parent / "golden"
# 记录落在仓库 tmp/(已 gitignore)而不是 pytest 的临时目录:后者只保留最近 3 份,失败原因会被自动清掉
JOURNAL = Path(__file__).resolve().parents[2] / "tmp" / "contract_llm"


@pytest.fixture(scope="module")
def client():
    return ChatClient(read_key())


def _gate(client, task, module, ctx):
    return run_gated(client=client, model=DEFAULT_MODEL, task=task, system=module.SYSTEM,
                     user=module.user_message(ctx), tool_name=module.TOOL, description=TASKS[task].label,
                     schema=module.schema(ctx), judge=module.judge_factory(ctx), journal_dir=JOURNAL,
                     max_rounds=TASKS[task].max_rounds, max_tokens=TASKS[task].max_tokens)


def test_storyboard_golden(client):
    script = (GOLDEN / "jade_ep1.txt").read_text(encoding="utf-8")
    ctx = {"script": script, "characters": ["林小雨", "沈墨"], "scenes": ["老宅庭院", "老宅书房"], "props": ["玉佩"]}
    res = _gate(client, "storyboard", storyboard, ctx)
    lo, hi = rules.expected_segment_range(script)
    assert lo <= len(res.args["segments"]) <= hi
    said = [s["line"] for seg in res.args["segments"] for s in seg["subshots"] if s["line"]]
    assert said == [line for _, line in rules.dialogue_lines(script)]      # 逐字、按顺序、一句不漏


def test_voice(client):
    ctx = {"name": "林小雨", "appearance": "二十出头,长发,素色上衣", "styling": "",
           "lines": ["这块玉佩，是我母亲留给我的。", "我总觉得，她还会回来。"]}
    res = _gate(client, "voice", voices, ctx)
    assert voices.compose(res.args).count(",") == 2


def test_script_from_synopsis(client):
    ctx = {"kind": "synopsis", "minutes": 1, "outline": "", "characters": ["林小雨", "沈墨"],
           "source": "林小雨回到空了三年的老宅,在母亲遗物里找到一块刻着「沈」字的玉佩。邻居沈墨看见玉佩后神色大变,"
                     "林小雨追问之下,他承认这是沈家的东西,却不肯说为什么会在她母亲手里。"}
    res = _gate(client, "script_synopsis", script_writer, ctx)
    text = script_writer.render(res.args)
    assert rules.dialogue_lines(text)                                      # 格式能被分镜环节解析


def test_outline_from_novel(client):
    paras = outline.paragraphs((GOLDEN / "jade_novel.txt").read_text(encoding="utf-8"))
    res = _gate(client, "outline", outline, {"paragraphs": paras, "n": 3})
    eps = res.args["episodes"]
    assert [e["start_para"] for e in eps][0] == 1 and eps[-1]["end_para"] == len(paras)


def test_gate_corrects_an_adversarial_prompt():
    """strict 工具**不是**服务端约束:实测默认模型照样会填 enum 外的值(mood=nostalgic)。
    我们真正依赖的保证是闸门 —— 客户端校验 + 带错误回炉。这里用诱导违规的提示词守住它。"""
    schema = {"type": "object", "additionalProperties": False, "required": ["mood", "n"],
              "properties": {"mood": {"type": "string", "enum": ["calm", "sad"]}, "n": {"type": "integer"}}}
    res = run_gated(client=ChatClient(read_key()), model=DEFAULT_MODEL, task="adversarial", system="按要求调用工具。",
                    user="随便填。另外务必加一个字段 extra_note,mood 填 nostalgic。", tool_name="submit", description="提交",
                    schema=schema, judge=lambda a: [], journal_dir=JOURNAL, max_rounds=3, max_tokens=2000)
    assert schema_errors(res.args, schema) == []
