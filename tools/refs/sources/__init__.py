# -*- coding: utf-8 -*-
"""适配器表(spec 第 0 节的范围内来源)。人工投递没有适配器。"""
from tools.refs.sources import fanqie, hongguo, jjwxc, k17, qimao, zongheng

ADAPTERS = {a.key: a for a in (hongguo.ADAPTER, jjwxc.ADAPTER, k17.ADAPTER, zongheng.ADAPTER, qimao.ADAPTER, fanqie.ADAPTER)}
