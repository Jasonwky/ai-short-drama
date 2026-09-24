// AI 短剧控制台 —— 只做三件事:切集、分镜实时校验、有在途任务时自动刷新。其余全是服务端渲染的表单。
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  // 1. 顶栏切集:保持当前面板,换 episode_id
  const sw = document.querySelector("[data-ep-switch]");
  if (sw) {
    sw.addEventListener("change", () => {
      const m = location.pathname.match(/^\/episode\/\d+(\/.*)$/);
      location.href = m ? `/episode/${sw.value}${m[1]}` : `/episode/${sw.value}/storyboard`;
    });
  }

  // 2. 分镜编辑:描述/时长一改就调代码校验(与保存时同一套规则)
  const form = document.querySelector("[data-live-check]");
  if (form) {
    const out = form.querySelector("[data-live-result]");
    let timer = null;
    const run = async () => {
      const body = new URLSearchParams({
        description: form.elements.description.value,
        duration_sec: form.elements.duration_sec.value,
      });
      const r = await fetch("/api/segment/check", { method: "POST", body });
      const data = await r.json();
      out.innerHTML = data.problems.length
        ? data.problems.map((p) => `<div class="bad">❌ ${p.replace(/</g, "&lt;")}</div>`).join("")
        : `<div class="ok">✅ ${data.ok_msg}</div>`;
    };
    form.addEventListener("input", (e) => {
      if (e.target.name === "description" || e.target.name === "duration_sec") {
        clearTimeout(timer);
        timer = setTimeout(run, 300);
      }
    });
    run();
  }

  // 3. 页面上有在途任务(待提交/已提交/生成中/等云机)时,每 10 秒刷新一次看结果
  if (document.querySelector("[data-inflight]")) {
    setTimeout(() => location.reload(), 10000);
  }
});
