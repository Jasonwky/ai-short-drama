// 工作流编辑页:同源 iframe 里的 ComfyUI 画布 ↔ 控制台。
// 载入:app.loadApiJson(工作流, "edit-<令牌>");提交:核对当前标签 → app.graphToPrompt().output → POST 给控制台把关。
// 实测见 spike-results/2026-10-08-ComfyUI前端本机离线编辑实测.md §五。
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const box = document.querySelector("[data-comfy-edit]");
  if (!box) return;
  const frame = document.querySelector("[data-comfy-frame]");
  const out = box.querySelector("[data-edit-result]");
  const tab = `edit-${box.dataset.token}`;
  const graph = JSON.parse(document.querySelector("[data-graph]").textContent);
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const app = () => frame.contentWindow && frame.contentWindow.app;

  const active = (a) => {
    const w = a.extensionManager.workflow.activeWorkflow;
    return w && (w.filename || w.path);
  };
  // 前端启动时会自己开一个默认的「Unsaved Workflow」并切过去(实测比 app.graph 就绪晚)。
  // 所以:等它开完再载入我们的;载入后核对当前标签确实是我们的,不是就再载一次 —— 确认了才算「已载入」。
  const loaded = new Promise((resolve) => {
    let tries = 0;
    const tick = setInterval(async () => {
      const a = app();
      if (!(a && a.graph && a.extensionManager && a.extensionManager.workflow && active(a))) return;
      if (active(a) === tab) {
        clearInterval(tick);
        box.dataset.loaded = "1";
        resolve();
      } else if (tries++ % 5 === 0) {
        // 必须在 iframe 自己的 JS 环境里造这份对象:父页面造的数组在 iframe 里 Array.isArray 认不出,
        // 前端会把连线 ["18", 0] 当普通值扔掉(实测:连线全丢、步数变 NaN)
        await a.loadApiJson(frame.contentWindow.JSON.parse(JSON.stringify(graph)), tab);
      }
    }, 400);
  });

  const show = (html) => { out.innerHTML = html; };

  async function send(confirm) {
    await loaded;
    const a = app();
    const current = a.extensionManager.workflow.activeWorkflow;
    const body = {
      graph: (await a.graphToPrompt()).output,
      token: box.dataset.token,
      tab: current && (current.filename || current.path),
      note: (box.querySelector("[data-note]") || {}).value || "",
      confirm,
    };
    const r = await fetch(box.dataset.action, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await r.json();
    if (data.ok) {
      show(`<p class="status-ok" data-edit-ok>已保存。<a href="${esc(data.back)}">回去看</a></p>`);
    } else if (data.confirm) {
      show(`<div class="confirm card" data-edit-confirm><p class="cost">相对内置模板的改动:</p>` +
           data.lines.map((l) => `<div><code>${esc(l)}</code></div>`).join("") +
           `<button class="btn primary" type="button" data-confirm-save>确认保存为新版本</button></div>`);
      out.querySelector("[data-confirm-save]").addEventListener("click", () => send(true));
    } else {
      show(`<ul class="checklist" data-edit-problems>` +
           data.problems.map((p) => `<li class="bad">${esc(p)}</li>`).join("") + `</ul>`);
    }
  }

  box.querySelector("[data-submit]").addEventListener("click", () => send(false));
});
