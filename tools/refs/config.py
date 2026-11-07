# -*- coding: utf-8 -*-
"""参考库的可调阈值。都是**暂定值**:「涨得快」等 1a 跑出一段真实热度后标定,标定结果落 spike-results 并改这里。"""
GROWTH = 0.20       # 同榜相邻两天热度涨幅
RANK_JUMP = 10      # 同榜相邻两天名次上升
NEW_TOP = 10        # 第一次上榜就进前几名
SITEMAP_DAYS = 7    # 红果 sitemap 多久增量看一次
FETCH_RETRIES = 2   # 连不上 / 超时的重试次数(之后判失败)
TRIP_AFTER = 3      # 连续几次解析失败熔断
TRANSCRIBE_RETRIES = 3   # 转写 ssh 连不上 / 显存被抢,连续几次就判失败(参考库 1b 审查 I3:不设上限会每个 GPU 空档重试一次、永远停在 waiting)
