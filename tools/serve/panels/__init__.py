# -*- coding: utf-8 -*-
"""面板注册表。**Playwright 自测遍历的就是这张表** —— 新面板只要登记进来,自测自动覆盖它。

scope 决定 URL 要什么上下文:global 不要;drama 要 drama_id;episode 要 episode_id。
面板有了真后端才登记 —— 没有后端的面板只能是空壳,违反 CLAUDE.md「每个环节是它自己真正的操作面板」。
大纲在 P4 接上 LLM 后登记;成片在 P5 接上 ffmpeg 导出后登记。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Panel:
    key: str
    title: str
    scope: str      # global / drama / episode
    path: str       # 可含 {drama_id} / {episode_id}


REGISTRY: tuple[Panel, ...] = (
    Panel("projects", "立项", "global", "/"),
    Panel("outline", "大纲", "drama", "/drama/{drama_id}/outline"),
    Panel("script", "剧本", "drama", "/drama/{drama_id}/script"),
    Panel("assets", "资产", "drama", "/drama/{drama_id}/assets"),
    Panel("storyboard", "分镜", "episode", "/episode/{episode_id}/storyboard"),
    Panel("frames", "参考帧", "episode", "/episode/{episode_id}/frames"),
    Panel("videos", "视频", "episode", "/episode/{episode_id}/videos"),
    Panel("final", "成片", "episode", "/episode/{episode_id}/final"),
    Panel("workflows", "工作流", "global", "/workflows"),
    Panel("hosts", "主机", "global", "/hosts"),
    Panel("trace", "留痕", "global", "/trace"),
)


def url_for(panel: Panel, drama_id: int | None, episode_id: int | None) -> str | None:
    """这个上下文下面板的地址;缺上下文(还没选剧 / 剧里还没有集)返回 None。"""
    if panel.scope == "drama" and drama_id is None:
        return None
    if panel.scope == "episode" and episode_id is None:
        return None
    url = panel.path.format(drama_id=drama_id, episode_id=episode_id)
    # 全局面板带上当前剧(?d=),从「主机」回来时左侧步骤不丢上下文
    return f"{url}?d={drama_id}" if panel.scope == "global" and drama_id is not None else url
