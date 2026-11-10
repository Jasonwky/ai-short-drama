# -*- coding: utf-8 -*-
"""参考库的可调阈值。都是**暂定值**:「涨得快」等 1a 跑出一段真实热度后标定,标定结果落 spike-results 并改这里。"""
GROWTH = 0.20       # 同榜相邻两天热度涨幅
RANK_JUMP = 10      # 同榜相邻两天名次上升
NEW_TOP = 10        # 第一次上榜就进前几名
SITEMAP_DAYS = 7    # 红果 sitemap 多久增量看一次
FETCH_RETRIES = 2   # 连不上 / 超时的重试次数(之后判失败)
TRIP_AFTER = 3      # 连续几次解析失败熔断
TRANSCRIBE_RETRIES = 3   # 转写 ssh 连不上 / 显存被抢,连续几次就判失败(参考库 1b 审查 I3:不设上限会每个 GPU 空档重试一次、永远停在 waiting)
# 冷门对照(spec 2.2,contrast.py):某个特征热门组与冷门组的占比差 ≥ high 个百分点算区分度高,≥ mid 算中,否则低。
# **暂定值**(没有标定:1c 库里有标注的作品还太少),样本够了再量、落 spike-results 并改这里。
# by_rank:没有详情热度的平台(七猫只有榜单名次),按最新一天的最好名次分组,名次小 = 热。
CONTRAST = {"high": 0.30, "mid": 0.15, "by_rank": ("qimao",)}
# 创作视图的原文高频片段提示(creation.frequent_fragments):单元原文里出现至少 PROPER_MIN 次、长 PROPER_LEN 字起的片段才列出来。
# PROPER_MIN 暂定(没标定);PROPER_LEN = 3 依据两张真卡:2 字命中 25 个全是常用词 / 称谓,一个专名都没有
# (spike-results/2026-11-10-原文高频片段提示标定.md)。2 字专名(九州、青云)靠人在「不可借鉴」里「去掉」兜底。
PROPER_MIN = 3
PROPER_LEN = 3
# 整卡汇总的输入上限:所有单元原文合计这么多字以内(spec 2.1「每部作品有 token 上限」)。超了拒,不截断。
# **暂定值**(约合 Sonnet 一次能稳妥读完的量,没有标定),真拆几部长篇后按耗时 / 截断情况再定。
CARD_INPUT_CHARS = 60000
