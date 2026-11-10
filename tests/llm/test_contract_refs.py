# -*- coding: utf-8 -*-
"""参考库拆解卡两层 LLM 任务的**真 LLM 契约测试**(计划 2026-11-08-参考库-1c Task 8;默认 `pytest -q` 不跑)。

- 材料全是本测试自写的原创古风章节与剧评,不用任何版权原文;
- 走 `service.begin / finish`(真后端 = 本机 Claude Code 命令行),记录落 `tmp/contract_llm/llm/`;
- `ref_card` 的三个单元标注用 FakeLLM 产出 proposed(只当确定性替身铺上下文,不声称覆盖 ref_unit 契约 —— 那是第一条测的);
- 视频单元(带关键帧)这次不测:没有可入库的非版权关键帧。
"""
import json

import pytest

from tests.llm.test_contract_real import JOURNAL, client  # noqa: F401
from tools.llm import ref_card as rc
from tools.llm import ref_unit as ru
from tools.llm import runs, service
from tools.llm.beats import FIELDS
from tools.llm.fake import FakeLLM
from tools.llm.service import DEFAULT_MODEL, LLMBackend
from tools.llm.verbatim import restore
from tools.refs.db import RefStore

pytestmark = pytest.mark.real_llm

CH1 = """第一章 寒灯

腊月初八,京城落了今冬第一场雪。

苏晚棠跪在侯府祠堂冰冷的青砖上,已经跪了整整两个时辰。膝盖早就没了知觉,她却把背挺得笔直,像一根不肯弯的竹。

祠堂的门“吱呀”一声被推开,寒风卷着雪粒子扑进来,吹得供桌上的长明灯摇摇欲灭。

“大小姐,喝口姜汤暖暖身子吧。”柳嬷嬷提着食盒进来,鬓边沾着雪,眼眶通红,“夫人走得早,老奴看着您长大,实在见不得您受这样的委屈。”

苏晚棠接过汤碗,指尖被烫得一缩。她低声道:“嬷嬷,父亲还是不肯信我吗?”

“侯爷正在气头上。”柳嬷嬷叹了口气,替她拢了拢肩上的旧斗篷,“二小姐哭着说,那支金凤钗是从您的妆匣里翻出来的,人证物证都在,侯爷就算想护您,也护不住啊。”

金凤钗是老夫人的遗物,前日在佛堂里不翼而飞。今早,它却出现在苏晚棠的妆匣最底层,压在母亲留给她的一方旧帕子下面。

苏晚棠没有说话。她慢慢把姜汤喝完,把碗放回食盒,忽然问:“嬷嬷,昨夜是谁替我收拾的妆匣?”

柳嬷嬷的手顿了一下,随即笑道:“自然是老奴。大小姐的东西,老奴从不让旁人碰。”

“是啊。”苏晚棠也笑了,“所以我才想不明白。”

话音未落,祠堂外传来一阵杂乱的脚步声。管家高声通报:“侯爷到——裴世子到——”

苏晚棠心头一紧。裴砚,镇北王府的世子,三日前刚与她换了庚帖。京中人人都说,这门亲事是苏家高攀,只等世子一句话,婚约随时可退。

门帘掀起,永宁侯苏敬一身墨色常服,面沉如水地走了进来。他身后跟着一个披玄色大氅的年轻男子,眉目冷峻,肩头落满了雪,正是裴砚。

“孽障!”苏敬一掌拍在供桌上,长明灯猛地一跳,“当着列祖列宗的面,你还不认罪?”

苏晚棠抬起头,声音不大,却一字一句:“女儿没有偷,不认。”

“好,好一个不认。”苏敬气得发抖,转身朝裴砚拱手,“世子,家门不幸,让你看笑话了。这门亲事,苏某没脸再提——”

“侯爷且慢。”裴砚淡淡开口,目光落在苏晚棠冻得发紫的手上,停了片刻,又移开,“晚辈今日来,不是为了退亲。”

满屋的人都愣住了。

裴砚从袖中取出一只油纸包,当众打开。里面是一小撮暗红色的粉末,和半截烧焦的纸笺。

“前日夜里,镇北王府的暗卫在侯府后巷截下一个送信的小厮。”他说,“小厮身上搜出这包药粉,还有这封没烧干净的信。信上写着,金凤钗已放妥,事成之后,再送五十两。”

苏敬脸色骤变:“信是写给谁的?”

裴砚没有回答,而是转过身,看向站在角落里的那个人。

“柳嬷嬷,”他问,“这笔迹,你认得吗?”

祠堂里静得只剩下风声。柳嬷嬷手里的食盒“哐当”一声掉在地上,碎瓷片溅了一地。

苏晚棠缓缓站起来。她的腿还在发软,却没有让任何人扶。她走到柳嬷嬷面前,轻声问:“嬷嬷,这些年,母亲每日喝的安神汤,也是你亲手熬的吧?”

柳嬷嬷的脸一下子白得像纸。她扑通跪倒,嘴唇哆嗦着,却一个字也说不出来。

苏敬踉跄后退一步,扶住了供桌。

就在这时,院外忽然传来一声尖叫。一个小丫鬟跌跌撞撞地冲进来,脸上全是惊恐:

“侯爷!不好了!二小姐……二小姐她在房里吐了血,嘴里一直喊着,说是大小姐要害她!”

苏晚棠猛地回头。

雪夜深处,侯府西院的灯,一盏接一盏地亮了起来。
"""

CH2 = """第二章 血帕

西院里挤满了人。苏婉宁半倚在床头,唇边一缕血丝,手里攥着一方染红的帕子,一见苏晚棠进门就哭出声来:“姐姐,我把钗子还给你了,你为什么还要下毒?”

苏敬脸色铁青,回头盯着长女。

苏晚棠没有辩解,只走到床前,拿起桌上那只还剩半碗的药盏,凑近闻了闻。“这是朱砂红,”她说,“入口发苦,沾帕不褪。妹妹,你帕子上的血,怎么是甜的?”

裴砚伸手蘸了一点帕上的红,放在舌尖一尝,淡淡道:“胭脂兑了蜂蜜。”

满屋的丫鬟婆子面面相觑。苏婉宁的哭声一下子卡在了喉咙里。

苏晚棠俯身,在妹妹耳边轻声说:“柳嬷嬷已经全招了。你猜,她招出来的第一个名字,是谁?”

苏婉宁浑身一颤,手里的血帕掉在了地上。
"""

CH3 = """第三章 退婚书

三日后,苏敬亲自到祠堂上香,对着亡妻的牌位站了很久。

苏晚棠捧着一封信走进来,双手递给父亲:“女儿请父亲替我写一封退婚书,送去镇北王府。”

苏敬一怔:“世子那日替你作证,你这是何苦?”

“正因为他替我作证,女儿才不能连累他。”苏晚棠垂下眼,“母亲的死,不止柳嬷嬷一个人的事。”

当夜,裴砚翻墙进了侯府,站在她的窗外。他把那封退婚书原封不动地放在窗台上,只说了一句:“你母亲喝的安神汤,药方是从宫里出来的。”

苏晚棠推开窗,雪光映着他的脸。她问:“你早就知道?”

裴砚没有回答,只把一枚刻着凤纹的令牌放在退婚书上,转身消失在夜色里。
"""

REVIEWS = ["第一章跪祠堂憋屈死了,还好世子带着证据进门,太爽了",
           "柳嬷嬷居然是内鬼,这个反转我真没想到",
           "二小姐装吐血被一口尝出是胭脂,笑死我了",
           "女主不哭不闹全靠脑子,这种大女主我爱看",
           "最后那块凤纹令牌是什么意思?宫里的人也牵扯进来了,追!"]

UNIT_ARGS = {
    1: {"hook": "有", "reversal": "大", "emotion": "放", "paywall": "是", "summary": "女主被诬偷钗罚跪,未婚夫拿出证据指向乳母。",
        "quote": "这笔迹,你认得吗?", "names": ["苏晚棠", "柳嬷嬷", "裴砚", "苏敬"]},
    2: {"hook": "有", "reversal": "小", "emotion": "放", "paywall": "是", "summary": "庶妹装中毒反咬,女主识破血是胭脂。",
        "quote": "胭脂兑了蜂蜜。", "names": ["苏婉宁", "苏晚棠", "裴砚", "苏敬"]},
    3: {"hook": "有", "reversal": "小", "emotion": "平", "paywall": "是", "summary": "女主想退婚护人,世子透露母亲之死牵扯宫中。",
        "quote": "药方是从宫里出来的", "names": ["苏敬", "苏晚棠", "裴砚"]},
}


def _store(tmp_path, chapters):
    """一部番茄小说(原创章节),每章一行 ref_content;外加一份剧评。返回 (refs, {章号: content_id})。"""
    refs = RefStore(":memory:")
    ids = {}
    with refs.write() as c:
        c.execute("INSERT INTO ref_source (key) VALUES ('fanqie')")
        c.execute("INSERT INTO ref_work (id, source, external_id, title, tags_json, intro, first_seen)"
                  " VALUES (1, 'fanqie', 'contract-1', '寒灯照棠', '[\"古言\",\"宅斗\"]',"
                  " '侯府嫡女被诬偷钗,一夜之间揭开生母之死的旧案。', 't')")
        for n, text in chapters.items():
            rel = f"content/fanqie/contract-1/ch{n}.txt"
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(text, encoding="utf-8")
            ids[n] = c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at,"
                               " due_at, hard_due_at, meta_json) VALUES (1, 'chapter', ?, ?, 's', 'o', 'ok', 't', 't', 't', '{}')",
                               (n, rel)).lastrowid
    return refs, ids


def _real(client):  # noqa: F811
    return LLMBackend(client, DEFAULT_MODEL, "real")


def _finish_real(refs, task, target, client, tmp_path, params=None):  # noqa: F811
    llm = _real(client)
    rid, reused = service.begin(refs, task, target, params or {}, llm, data_root=tmp_path)
    assert not reused
    fail = service.finish(refs, rid, llm, JOURNAL, data_root=tmp_path)
    row = runs.get(refs, rid)
    journal = json.loads(open(row["journal_path"], encoding="utf-8").read()) if row["journal_path"] else {}
    print(f"\n{task}: state={row['state']} rounds={len(journal.get('rounds', []))} journal={row['journal_path']}")
    assert fail is None, f"{task} 没交出待审产物({fail}):{row['error']}"
    assert row["state"] == "proposed"
    return runs.output(row)


def test_ref_unit_chapter(client, tmp_path):  # noqa: F811
    refs, ids = _store(tmp_path, {1: CH1})
    args = _finish_real(refs, "ref_unit", ids[1], client, tmp_path)
    assert all(args[k] in v for k, v in FIELDS.items())
    assert "visual_hook" not in args and "visual_sec" not in args
    assert restore(args["quote"], CH1) is not None                     # 依据逐字在原文
    assert args["names"] and all(restore(n, CH1) is not None for n in args["names"])
    assert "苏晚棠" in args["names"]
    assert ru.copied(args["summary"], CH1) is None                     # 用自己的话


def test_ref_card_chapters_and_reviews(client, tmp_path):  # noqa: F811
    refs, ids = _store(tmp_path, {1: CH1, 2: CH2, 3: CH3})
    for n in (1, 2, 3):                                                 # 确定性替身铺单元标注(proposed)
        fake = LLMBackend(FakeLLM({ru.TOOL: UNIT_ARGS[n]}), "m", "real")
        rid, _ = service.begin(refs, "ref_unit", ids[n], {}, fake, data_root=tmp_path)
        assert service.finish(refs, rid, fake, tmp_path / "journal", data_root=tmp_path) is None
    rel = "content/fanqie/contract-1/reviews-1.jsonl"
    (tmp_path / rel).write_text("\n".join(json.dumps({"text": t, "rating": 5, "likes": i, "at": "2026-11-01"},
                                                     ensure_ascii=False) for i, t in enumerate(REVIEWS)), encoding="utf-8")
    with refs.write() as c:
        c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at,"
                  " hard_due_at, meta_json) VALUES (1, 'review', NULL, ?, 's', 'o', 'ok', 't', 't', 't', '{}')", (rel,))

    args = _finish_real(refs, "ref_card", 1, client, tmp_path, {"heat_until": "2026-11-07"})
    hs = args["hypotheses"]
    assert rc.N_HYPOTHESES[0] <= len(hs) <= rc.N_HYPOTHESES[1]
    for h in hs:
        if h["evidence"] == "剧评":
            assert h["review_ids"] and all(1 <= i <= len(REVIEWS) for i in h["review_ids"])
    names = {n for a in UNIT_ARGS.values() for n in a["names"]}
    assert sorted(c["name"] for c in args["cast_types"]) == sorted(names)
