# -*- coding: utf-8 -*-
from tools.gen.comfy_client import ComfyClient


def test_queue_ids_reads_prompt_ids_not_just_counts():
    c = ComfyClient("x:1")
    c._request = lambda path, payload=None: {
        "queue_running": [[7, "mine", {}, {}, []]],
        "queue_pending": [[8, "someone-else", {}, {}, []], [9, "mine-2", {}, {}, []]],
    }
    assert c.queue_ids() == (["mine"], ["someone-else", "mine-2"])


def test_queue_ids_on_empty_queue():
    c = ComfyClient("x:1")
    c._request = lambda path, payload=None: {"queue_running": [], "queue_pending": []}
    assert c.queue_ids() == ([], [])
