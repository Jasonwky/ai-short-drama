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


def test_free_models_asks_comfyui_to_unload_and_vram_is_read_from_system_stats():
    c = ComfyClient("x:1")
    calls = []
    c._request = lambda path, payload=None: calls.append((path, payload)) or {
        "devices": [{"name": "cuda:0", "vram_total": 25250627584, "vram_free": 4134265200}]}
    c.free_models()
    assert calls[-1] == ("/free", {"unload_models": True, "free_memory": True})
    assert round(c.vram_free_gb(), 2) == 3.85 and calls[-1] == ("/system_stats", None)
