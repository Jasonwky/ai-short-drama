# -*- coding: utf-8 -*-
"""主机:登记云机、改 SSH 端口(AutoDL 每次开机都换)、登记卡的型号(估时按它)、看两层判活与 worker 日志。

私钥只填**文件路径**(`hosts.add_host` 拒绝密钥内容)。改端口后 worker 下一轮自动重建隧道;
接管来的旧隧道杀不掉时,worker 日志里会如实提示去手工结束。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.gen import nodecache
from tools.gen.capability import GPU_TIMINGS
from tools.serve import journal
from tools.serve.hosts import add_host, get_host, set_gpu, set_ssh_port
from tools.serve.panels.common import render, rt

router = APIRouter()


@router.get("/hosts")
def page(request: Request):
    runtime = rt(request)
    hosts = [{"host": get_host(runtime.store, hid), "status": runtime.status.get(hid)} for hid in runtime.host_ids()]
    return render(request, "hosts.html", "hosts", hosts=hosts, log=journal.tail(runtime.data_root, 40),
                  last_error=runtime.last_error, nodes=nodecache.meta(runtime.data_root), gpus=sorted(GPU_TIMINGS))


@router.post("/hosts")
def add(request: Request, label: str = Form(...), ssh_host: str = Form(...), ssh_port: int = Form(...),
        ssh_user: str = Form("root"), key_ref: str = Form(...), local_port: int = Form(8188),
        vram_gb: float = Form(24.0), models_dir: str = Form(...), gpu: str = Form(...)):
    add_host(rt(request).store, label=label, ssh_host=ssh_host, ssh_port=ssh_port, ssh_user=ssh_user,
             key_ref=key_ref, local_port=local_port, vram_gb=vram_gb, models_dir=models_dir, gpu=gpu)
    return RedirectResponse("/hosts", status_code=303)


@router.post("/host/{host_id}/object_info")
def refresh_nodes(request: Request, host_id: int):
    """抓一份云机的节点清单存本机 —— 关机后编辑工作流靠它。"""
    rt(request).refresh_object_info(host_id)
    return RedirectResponse("/hosts", status_code=303)


@router.post("/host/{host_id}/port")
def change_port(request: Request, host_id: int, ssh_port: int = Form(...)):
    set_ssh_port(rt(request).store, host_id, ssh_port)
    return RedirectResponse("/hosts", status_code=303)


@router.post("/host/{host_id}/gpu")
def change_gpu(request: Request, host_id: int, gpu: str = Form(...)):
    """卡的型号只给估时用(按已登记里最慢的一张算);改它不重建隧道。"""
    set_gpu(rt(request).store, host_id, gpu)
    return RedirectResponse("/hosts", status_code=303)
