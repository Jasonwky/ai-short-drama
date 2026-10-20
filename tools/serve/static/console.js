// AI 短剧控制台 —— 只做四件事:切集、分镜实时校验、局部替换拖框、有在途任务时自动刷新。其余全是服务端渲染的表单。
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

  // 3. 参考帧局部替换:在图上拖框,按原图像素(naturalWidth / 显示宽度)换算成 x/y/w/h 填进表单
  document.querySelectorAll("[data-region]").forEach((box) => {
    const img = box.querySelector("[data-region-img]");
    const rect = box.querySelector("[data-region-box]");
    const info = box.querySelector("[data-region-info]");
    const submit = box.querySelector("[data-region-submit]");
    const field = (f) => box.querySelector(`[data-region-${f}]`);
    let start = null;
    const at = (e) => {
      const r = img.getBoundingClientRect();
      return [Math.min(Math.max(e.clientX - r.left, 0), r.width), Math.min(Math.max(e.clientY - r.top, 0), r.height)];
    };
    const draw = (a, b) => {
      const [x, y, w, h] = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1])];
      Object.assign(rect.style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` });
      rect.hidden = false;
      return [x, y, w, h];
    };
    img.addEventListener("mousedown", (e) => { e.preventDefault(); start = at(e); });
    window.addEventListener("mousemove", (e) => { if (start) draw(start, at(e)); });
    window.addEventListener("mouseup", (e) => {
      if (!start) return;
      const scale = img.naturalWidth / img.getBoundingClientRect().width;
      const px = draw(start, at(e)).map((v) => Math.round(v * scale));
      start = null;
      ["x", "y", "w", "h"].forEach((f, i) => { field(f).value = px[i]; });
      const small = px[2] < 128 || px[3] < 128;
      info.textContent = `框 ${px[0]},${px[1]} ${px[2]}×${px[3]}(原图 ${img.naturalWidth}×${img.naturalHeight} 像素)`
        + (small ? " —— 太小了,宽高都要至少 128 像素" : "");
      submit.disabled = small || !box.querySelector("[data-region-asset]").value;
    });
  });

  // 4. 页面上有在途任务(待提交/已提交/生成中/等云机)时,每 10 秒刷新一次看结果;正在框选局部替换时不刷(框会丢)
  if (document.querySelector("[data-inflight]")) {
    const reload = () => (document.querySelector("[data-region][open]") ? setTimeout(reload, 10000) : location.reload());
    setTimeout(reload, 10000);
  }
});
