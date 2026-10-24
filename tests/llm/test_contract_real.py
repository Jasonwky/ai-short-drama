# -*- coding: utf-8 -*-
"""**真 LLM 契约测试**(CLAUDE.md:要测 LLM 就用真 LLM,不许拿 Fake 冒充)。

- 默认 `pytest -q` 不跑(花钱、每条 30–120 秒);`python -m pytest -m real_llm -q` 或验收入口跑;
- LLM 走本机 Claude Code 命令行(2026-10-23 起,`tools/llm/claude_cli.py`);找不到命令行就**失败**(既然要跑契约,就不许悄悄跳过);
- 用黄金样本守回归:换模型 / 改提示词掉质量,这里会红。
"""
from pathlib import Path

import pytest

from tools.drama import rules
from tools.llm import describe, outline, script_writer, storyboard, voices
from tools.llm.claude_cli import ClaudeCLIClient
from tools.llm.gate import run_gated, schema_errors
from tools.llm.service import DEFAULT_MODEL, TASKS, VISION_MODEL
from tools.llm.verbatim import restore, squash

pytestmark = pytest.mark.real_llm
GOLDEN = Path(__file__).parent / "golden"
# 记录落在仓库 tmp/(已 gitignore)而不是 pytest 的临时目录:后者只保留最近 3 份,失败原因会被自动清掉
JOURNAL = Path(__file__).resolve().parents[2] / "tmp" / "contract_llm"


@pytest.fixture(scope="module")
def client():
    return ClaudeCLIClient()


def _gate(client, task, module, ctx):
    return run_gated(client=client, model=DEFAULT_MODEL, task=task, system=module.SYSTEM,
                     user=module.user_message(ctx), tool_name=module.TOOL, description=TASKS[task].label,
                     schema=module.schema(ctx), judge=module.judge_factory(ctx), journal_dir=JOURNAL,
                     max_rounds=TASKS[task].max_rounds, max_tokens=TASKS[task].max_tokens)


def test_storyboard_golden(client):
    script = (GOLDEN / "jade_ep1.txt").read_text(encoding="utf-8")
    ctx = {"script": script, "characters": ["林小雨", "沈墨"], "scenes": ["老宅庭院", "老宅书房"], "props": ["玉佩"],
           "aspect": "9:16"}
    res = _gate(client, "storyboard", storyboard, ctx)
    lo, hi = rules.expected_segment_range(script)
    assert lo <= len(res.args["segments"]) <= hi
    said = [s["line"] for seg in res.args["segments"] for s in seg["subshots"] if s["line"]]
    want = [line for _, line in rules.dialogue_lines(script)]
    # 模型的抄写:折叠标点宽度后逐字、按顺序、一句不漏(判卷同一口径 —— 2026-09-24 实测模型会换标点宽度)
    assert [squash(x) for x in said] == [squash(x) for x in want]
    # 写回用的是剧本原文的写法:逐字节一致(2026-10-10 真 LLM 把「，」抄成「,」,以前会带进字幕)
    assert [restore(x, script) for x in said] == want


def test_storyboard_with_kneeling_and_lying_converges(client):
    """题甲第 1 集看片(2026-10-22):场记表(姿态 / 朝向 / 站位)+ 相邻段对表,真模型要能在轮次上限内交齐。"""
    script = (GOLDEN / "tijia_ep1.txt").read_text(encoding="utf-8")
    ctx = {"script": script, "characters": ["沈青禾", "沈玉瑶", "李福", "萧珩"], "scenes": ["靖王府正堂", "靖王府寝殿"],
           "props": ["银针"], "aspect": "9:16"}
    res = _gate(client, "storyboard", storyboard, ctx)
    poses = {e["pose"] for s in res.args["segments"] for e in s["blocking"]}
    assert "跪" in poses and poses & {"仰躺", "侧躺"}                  # 场记表真记下了跪和躺,不是全填「站」
    # 竖屏可拍性(2026-10-23):躺着 + 多人的段落,真模型要能收敛到近景 / 特写
    lying_pairs = [s for s in res.args["segments"]
                   if len(s["blocking"]) >= 2 and any(e["pose"] in ("仰躺", "侧躺", "趴") for e in s["blocking"])]
    assert all(s["shot_type"] in ("特写", "近景") for s in lying_pairs)


def test_script_keeps_the_bed_out_of_the_hall(client):
    ctx = {"kind": "synopsis", "minutes": 1, "outline": "", "characters": ["沈青禾", "李福", "萧珩"],
           "source": "大婚当晚,替嫁冲喜的沈青禾在王府正堂受管事李福羞辱。她不怒反笑,走到昏迷在床的靖王萧珩身边,"
                     "一针刺入眉心,当众揭穿李福在汤药里下毒。萧珩猛然睁眼,李福吓得跪地求饶。"}
    res = _gate(client, "script_synopsis", script_writer, ctx)
    for sc in res.args["scenes"]:
        if any("床" in ln["text"] for ln in sc["lines"]):
            assert not script_writer._HALL.search(sc["place"]) or script_writer._BEDROOM.search(sc["place"]), sc["place"]


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


def test_outline_later_batch_continues_the_previous_episode(client):
    """长小说分批:后一批带上一集大纲作前情(提示词多了 <previous>),结构照样过判卷、段号覆盖本批全文。"""
    paras = outline.paragraphs((GOLDEN / "jade_novel.txt").read_text(encoding="utf-8"))
    half = len(paras) // 2
    prev = "红布包\n林小雨回老宅整理遗物,找到母亲留下的红布包,里面是一块玉佩。\n- 回到老宅\n- 找到红布包"
    ctx = {"paragraphs": paras[half:], "n": 2, "start_number": 3, "previous": prev}
    res = _gate(client, "outline", outline, ctx)
    eps = res.args["episodes"]
    assert len(eps) == 2 and eps[0]["start_para"] == 1 and eps[-1]["end_para"] == len(paras) - half


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
    res = run_gated(client=ClaudeCLIClient(), model=DEFAULT_MODEL, task="adversarial", system="按要求调用工具。",
                    user="随便填。另外务必加一个字段 extra_note,mood 填 nostalgic。", tool_name="submit", description="提交",
                    schema=schema, judge=lambda a: [], journal_dir=JOURNAL, max_rounds=3, max_tokens=2000)
    assert schema_errors(res.args, schema) == []


def _describe(client, table, model, path):
    image, number = describe.marked(path)
    ctx = {"table": table, "name": {"character": "林小雨", "prop": "玉佩"}[table], "image": image,
           "check_number": number}
    return run_gated(client=client, model=model, task=f"describe_{table}", system=describe.SYSTEM,
                     user=describe.user_message(ctx), tool_name=describe.TOOL, description="从图反推描述",
                     schema=describe.schema(ctx), judge=describe.judge_factory(ctx), journal_dir=JOURNAL,
                     max_rounds=TASKS[f"describe_{table}"].max_rounds, max_tokens=TASKS[f"describe_{table}"].max_tokens,
                     images=describe.images(ctx))


def test_describe_character_golden(client):
    """黄金样本:生成的虚构角色(黑色长发、米白上衣、深蓝长裙、黑色平底鞋)。用标准答案审,不用 LLM 审。"""
    res = _describe(client, "character", VISION_MODEL, GOLDEN / "describe_character.png")
    a = res.args
    # 深蓝:2026-10-23 换 Claude 后模型写「深藏青色」—— 藏青就是深蓝,是同义词不是看错
    assert "黑" in a["appearance"] and any(w in a["styling"] for w in ("蓝", "藏青")) and "裙" in a["styling"]
    assert "skirt" in a["final_prompt"].lower()


def test_describe_prop_golden(client):
    res = _describe(client, "prop", VISION_MODEL, GOLDEN / "describe_prop.png")
    assert "绿" in res.args["description"] and "玉" in res.args["description"]
    assert "jade" in res.args["final_prompt"].lower()


def test_vision_check_sees_she_is_not_facing_him(client):
    """视觉抽检(2026-10-22 看片第 6 条):第 7 段首帧沈玉瑶倒在地上、没有面朝萧珩。真视觉模型要读出校验数字,
    且不能说她面朝萧珩 —— 说了就是这个抽检没用。"""
    from tools.llm import vision_check
    image, n = describe.marked(GOLDEN / "vision_seg7_first.jpg")
    ctx = {"number": 7, "labels": ["首帧"], "characters": ["沈玉瑶", "萧珩"], "image": image, "check_number": n,
           "expected": [], "video_sha256": ""}
    res = run_gated(client=client, model=VISION_MODEL, task="vision_check", system=vision_check.SYSTEM,
                    user=vision_check.user_message(ctx), tool_name=vision_check.TOOL, description=TASKS["vision_check"].label,
                    schema=vision_check.schema(ctx), judge=vision_check.judge_factory(ctx), journal_dir=JOURNAL,
                    max_rounds=TASKS["vision_check"].max_rounds, max_tokens=TASKS["vision_check"].max_tokens,
                    images=[image])
    (frame,) = res.args["frames"]
    her = next(p for p in frame["people"] if p["character"] == "沈玉瑶")
    assert her["facing"] != "萧珩"


def test_critique_quotes_the_script_verbatim(client):
    """评审团(2026-10-23):真模型的每条引用都要能在剧本原文里逐字找到(判卷就是这个口径)。"""
    from tools.llm import critique
    script = (GOLDEN / "tijia_ep1.txt").read_text(encoding="utf-8")
    ctx = {"role": "观众", "script": script}
    res = _gate(client, "critique", critique, ctx)
    assert 1 <= len(res.args["issues"]) <= 8
    assert all(restore(i["quote"], script) is not None for i in res.args["issues"])


def test_rewrite_gives_distinct_candidates_in_script_format(client):
    """局部改写(2026-10-23):真模型给 2–3 个不同写法,台词格式和角色名都过判卷。"""
    from tools.llm import rewrite
    script = (GOLDEN / "tijia_ep1.txt").read_text(encoding="utf-8")
    span = "李福说：「你、你胡说！」"
    assert script.count(span) == 1
    ctx = {"script": script, "span": span, "intent": "加强冲突", "note": "",
           "characters": ["沈青禾", "沈玉瑶", "李福", "萧珩"]}
    res = _gate(client, "rewrite", rewrite, ctx)
    texts = [c["text"] for c in res.args["candidates"]]
    assert 2 <= len(texts) <= 3 and len(set(texts)) == len(texts) and span not in texts


@pytest.mark.parametrize("n", [3, 12])                     # 12 = N_RANGE 上限(2026-10-24 从 6 放宽)
def test_premise_candidates_really_differ(client, n):
    """立项发散(2026-10-23):真模型给的方案两两至少 2 个维度不同、过合规词表、典型度在 0–1(判卷口径)。"""
    from tools.llm import diverge, premise
    ctx = {"idea": "替嫁冲喜的庶女,大婚夜发现王爷是被下毒,她其实是隐世神医", "n": n, "constraint": "",
           "characters": [], "current": "(还没有)"}
    res = _gate(client, "premise", premise, ctx)
    cands = res.args["candidates"]
    assert len(cands) == n and diverge.distinct_problems(cands, tuple(premise.DIMS)) == []


def test_beats_cover_every_episode_with_verbatim_quotes(client):
    """节拍标注(2026-10-23):每集都标、依据逐字出自该集大纲(判卷口径)。"""
    from tools.llm import beats
    ctx = {"episodes": [(1, "沈青禾替嫁冲喜,大婚夜被嫡姐羞辱,她当众一针救活昏迷的王爷,揭穿管事下毒。"),
                        (2, "王爷醒来,李福跪地求饶,沈玉瑶被杖毙;王爷捏住她的下巴,追问她究竟是谁。")]}
    res = _gate(client, "beats", beats, ctx)
    assert sorted(e["number"] for e in res.args["episodes"]) == [1, 2]


def test_outline_variants_really_differ(client):
    """单集大纲变体(2026-10-23):真模型给的版本两两至少 2 个维度不同(判卷口径)。"""
    from tools.llm import diverge, outline_variants
    ctx = {"outline": "沈青禾替嫁冲喜,大婚夜被嫡姐羞辱,她当众一针救活昏迷的王爷,揭穿管事下毒。", "n": 3}
    res = _gate(client, "outline_variants", outline_variants, ctx)
    vs = res.args["variants"]
    assert len(vs) == 3 and diverge.distinct_problems(vs, tuple(outline_variants.DIMS)) == []


def test_shot_options_pass_the_existing_checks(client):
    """分镜方案(2026-10-23):真模型给的镜头方案过场记表 / 陈设表 / 竖屏可拍性 / 差异判卷。"""
    from tools.llm import shot_options
    ctx = {"description": "【镜头1】萧珩躺在床上昏睡。【镜头2】沈青禾俯身,把银针刺入他眉心。", "aspect": "9:16",
           "scene": "靖王府寝殿", "furnishings": "雕花拔步床榻、床帐、烛台", "characters": ["沈青禾", "萧珩"],
           "props": ["银针"], "n": 2,
           # 上一段同场景:萧珩仰躺、沈青禾站着 —— 真模型的方案不能无交代地改姿态
           "prev": {"description": "萧珩躺在床上昏睡,沈青禾站在床边把脉。",
                    "blocking": [{"character": "萧珩", "pose": "仰躺", "facing": "镜头", "side": "中", "holding": "无"},
                                 {"character": "沈青禾", "pose": "站", "facing": "萧珩", "side": "右", "holding": "无"}]},
           "next": None,
           "current": {"shot_type": "", "angle": "", "movement": "", "image_prompt": "", "blocking_json": ""}}
    res = _gate(client, "shot_options", shot_options, ctx)
    assert len(res.args["options"]) == 2
