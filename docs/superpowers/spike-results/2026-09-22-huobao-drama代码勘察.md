# huobao-drama 代码勘察 —— 学做法,不抄代码

- **日期**:2026-09-22
- **对象**:`chatfire-AI/huobao-drama` ★15.4k,浅克隆到 `thirdparty/huobao-drama`(已 gitignore)
- **授权**:用户明确要求「去读它代码学做法」
- **边界**:🔴 该项目是 **CC BY-NC-SA 4.0(非商用 + 传染性 ShareAlike)**。
  本文只记录**机制与设计决策**,不搬运代码。我方实现必须独立编写。
- **证据等级**:A(源码原文,本机可复核)

---

## 一、结论先说

1. **三层主干**:`dramas`(剧) → `episodes`(集) → `storyboards`(分镜段落)。
   资产(`characters` / `scenes` / `props`)挂在**剧级**,跨集复用 —— 一致性的地基就在这个层级选择上。
2. 🔴 **「分镜」不是一个镜头,是一个 8–15 秒的「段落」,内含 2–4 个子镜头,硬切衔接。
   一个段落 = 一个视频生成任务。** 这条直接改写了产能与成本的算法,见 §五。
3. **三类资产各有专属的参考图形态**,不是随便出张图:
   角色 = **三视图**(正/侧/背) · 场景 = **固定视角无人空镜** · 道具 = **白底单品静物**。
4. **风格用一段结构化 prompt 前缀统一注入**,八维写法 + `avoid` 禁忌项,
   每条都带 `consistent character design across shots`。风格预设是**数据不是代码**。
5. **单一任务表 `sys_task` 同时承载图片与视频**,一条生命周期:
   `创建(processing) → 适配器构建请求 → 同步完成或异步轮询 → 下载落盘 → 回写业务表`。
6. 🔴 **「真人写实」风格被他们下架了**,代码注释里的理由是
   「真人影像过不了平台真人内容审核」。这是别人踩过的合规坑,直接受用。

---

## 二、数据模型(最有价值的部分)

### 主干三层

| 表 | 关键字段 | 备注 |
|---|---|---|
| `dramas` | `style` `aspect_ratio` `total_episodes` `status` | **画幅在剧级**,建完不给改 |
| `episodes` | `content` **`script_content`** `image_config_id` `video_config_id` `resolution` | 原始内容与改写后剧本**分两个字段存**,可回溯 |
| `storyboards` | 见下 | 一行 = 一个生成任务 |

### `storyboards` 的字段设计(照抄这个思路)

```
叙事层: storyboard_number, title, location, time, description, result, atmosphere
镜头语言: shot_type(景别), angle(角度), movement(运镜), duration
提示词: image_prompt, video_prompt, bgm_prompt, sound_effect
产物: first_frame_image, last_frame_image, composed_image,
      reference_images, video_url, subtitle_url, composed_video_url
状态: status
```

**三点值得注意**:

- **`first_frame_image` / `last_frame_image` 是 schema 里的一等字段** —— 首尾帧衔接不是后期补的功能,是数据模型的一部分。
- **提示词分四路**(图 / 视频 / BGM / 音效),不是一个大 prompt 字段。
- **产物字段分得很细**:`video_url`(生成的) 与 `composed_video_url`(合成的)分开存,拼接时
  `sb.videoUrl || sb.composedVideoUrl` 取优先 —— 兼容历史数据而不丢。

### 资产层与绑定关系

```
characters / scenes / props     ← 全部挂 drama_id(剧级,跨集复用)
    ↑
episode_characters / episode_scenes / episode_props   (集 ↔ 资产,多对多)
storyboard_characters / storyboard_props              (镜 ↔ 资产,多对多,复合主键)
storyboards.scene_id                                   (镜 → 场景,一对一外键)
```

⚠️ **场景是一对一不是多对多** —— 因为「一个段落不跨场景」是他们的硬规则(见 §三)。
这是数据模型对业务规则的直接编码,不是疏漏。

`characters` 上有两个字段特别值得抄:
- **`seed_value`** —— 角色级固定种子,保证重生成时形象不漂
- **`appearance` / `styling` / `final_prompt` 三段分开** —— 外观、造型、最终提示词各存各的,
  改造型不用重写外观

---

## 三、分镜拆解规范(`skills/storyboard-breaker/SKILL.md`)

这份 prompt 是整个项目里信息密度最高的东西,**规则全部可量化**:

| 规则 | 内容 |
|---|---|
| **段落定义** | 8–15 秒,含 2–4 个子镜头,每子镜头 2–6 秒,**段落内不跨场景** |
| **节拍边界强制切段** | 开场/触发/高潮/收尾,或地点转移、规则揭示、情绪爆发、反转。**不把一条因果链(铺垫-发生-反应)切散** |
| **总量锚定** | 目标总时长 = 剧本字数 ÷ **500 字/分钟**;段落数 ≈ 总时长 ÷ **12 秒**,±20% |
| **台词时长下限(硬规则)** | 段落时长 ≥ 台词字数 ÷ **4.5 字/秒** + **2 秒表演余量**;装不下的台词必须拆到下一段 |
| **节奏分层** | 过渡段 8–10s · 叙事段 10–15s · 爆点段 12–15s(子镜头可停 4–6 秒) |

**画面描述的写法**也被规定死了:按 `【镜头1】…【镜头2】…` 逐子镜头写,
画面(谁 + 具体动作 + 肢体细节 + 表情)在前,台词写成「角色名说:「台词」」,旁白写「旁白:内容」。

> 这套规则的价值在于:**它把"拆分镜"从一件凭感觉的事变成了可校验的事**。
> 字数÷500、台词÷4.5、段落÷12 都是能写成断言的 —— 正好对上 CLAUDE.md 的
> 「能确定性测的绝不交给 LLM 或人眼判断」(M-5 双层闸门)。

---

## 四、三类资产的参考图形态(`services/final-prompt.ts`)

| 资产 | 提示词形态 | 原文要求 |
|---|---|---|
| 角色 | **三视图** character turnaround | 正面 / 侧面 / 背面 |
| 场景 | **固定视角 + 前景/中景/后景** | 🔴「这是无人物空镜 —— 场景图中不能出现任何的人(**含背影、剪影、倒影、照片里的人**),即使场景描述提到人物活动也必须剔除」 |
| 道具 | **白底单品静物** | single product shot on pure white background |

**道具那条的用意**在分镜规范里点明了:「绑定的道具会作为视频生成的参考图(白底单品图),
**保证道具外观跨段落一致**」—— 与 ai-ecommerce 的 `<Picture N>` 产品锁定是同一个套路。

**降级策略**:`final_prompt` 缺失时跑 `prompt_generator` Agent 生成并保存;
**Agent 失败返回 `''`,由调用方回退到本地拼接提示词** —— 不静默失败也不硬失败。

---

## 五、🔴 产能与成本的重算(本次勘察最重要的产出)

### 生成单位比预想的大得多

之前我按「一镜 4 秒」估算,**错了**。两家独立收敛到同一结论:

| 来源 | 数据 |
|---|---|
| huobao 分镜规范 | 段落 8–15 秒,含 2–4 子镜头,**一段 = 一个生成任务** |
| huobao 真实截图 | 单集 **16 段落 / 184 秒** → **11.5 秒/段** |
| Toonflow 真实截图 | 单集 **29 条分镜 / 12 个 Track**,每 Track ≤15 秒 → **2.4 子镜头/Track** |

两家完全一致:**生成单位是 8–15 秒的段落,不是 4 秒的单镜**。

### 按 MiniMax H3 实测数据折算

ai-ecommerce 云机 4090 实测:**328 帧 / 13.667 秒 / 36.4 分钟**(20 步基线,109.3 秒/步)。
H3 的 13.667 秒正好落在段落时长区间内 —— **一条 H3 = 一个段落**,不需要折算。

| 项 | 推算 |
|---|---|
| 单集 184 秒 ÷ 13.667 秒 | **≈ 13.5 条 H3** |
| × 36.4 分钟 | **≈ 8.2 小时 / 集**(单卡,一次过) |
| 叠加 Toonflow demo 实测的 **33% 废片率** | **≈ 11 小时 / 集** |

⚠️ **这是推算不是实测**。未验证项:H3 在 4090 上跑 20 步的耗时是否随内容波动、
加速 LoRA(PDD 8步/4步)能压到多少、图片生成耗时未计入。

> **修正记录**:本文推翻了 `2026-09-22-AI短剧成熟工作流调研.md` §十一 里
> 「22 镜 × 10 分钟 ≈ 3.7 小时/集」的估算。错因是**把生成单位当成了 4 秒单镜**,
> 而行业实际生成单位是 8–15 秒段落。正确量级是 **8–11 小时/集**,不是 3.7 小时。

---

## 六、任务系统(`services/generation.ts`)

**一张 `sys_task` 表同时装图片和视频任务**,字段:

```
type, provider, model, prompt, params, task_id, result_url, local_path,
status, error_msg, created_at/updated_at/completed_at
+ 五个可空外键: storyboard_id / drama_id / scene_id / character_id / prop_id
```

生命周期:`createTask(processing) → processTask(适配器构建请求) → 同步完成 or markPolling → pollTask → handleImageComplete/handleVideoComplete(下载落盘) → writeBackImageAssets(回写业务表)`。

**适配器层**按厂商一个文件:`openai-image` / `gemini-image` / `volcengine-image` /
`minimax-video` / `volcengine-video` / `aliyun-wan-video`,共用 `adapters/types.ts` 的接口。
—— 与 ai-ecommerce 的 `VideoBackend` + `Capability` 是同一个思路,可互相印证。

**一个实用细节**:本地文件要喂给云端 API 时,需要 `PUBLIC_BASE_URL` 拼成公网地址,
未配置时**抛出可操作的中文错误**写进 `error_msg` 供前端展示,而不是静默失败。

---

## 七、拼接(`services/ffmpeg-merge.ts`)

- 按 `storyboard_number` 排序拼接,**支持只拼选中的几镜**(部分拼接)
- **未生成的镜头跳过**,不阻塞
- 🔴 拼接前**先探测 ffmpeg 可用性**:二进制损坏时 fluent-ffmpeg 的同步 EFTYPE 会**崩掉整个进程**
- 🔴 拼接前**逐个校验视频文件真实存在**:DB 里的 `video_url` 可能指向已被清理的文件,
  直接拼会得到 ffmpeg 的 "No such file or directory" 晦涩报错。
  他们改成报 **「镜头 S3、S7 的视频文件已丢失,请重新生成或取消勾选」**

> 这两条都是 CLAUDE.md「fail-loud + 可操作的错误信息」的正面例子,直接受用。

---

## 八、风格预设(`db/sqlite-schema.ts`)

**风格是数据不是代码**,八个预设(3D漫剧 / 日漫赛璐璐 / 吉卜力 / 水彩 / 美式漫画 / 国风2.5D / 韩系网漫 / 黑白漫),
每个是一段**结构化英文 prompt 前缀**,注入每张图的提示词。写法按固定八维:

```
核心媒介与渲染 → 线条/造型 → 上色/材质 → 光影 → 色彩调性 → 背景处理 → 画质锚点 → avoid 禁忌项
```

每条都以 `consistent character design across shots` 收尾,并带一串 `avoid ...`(如
`avoid plastic waxy skin, avoid low-poly blurry look, avoid 2D flat cel shading`)。

**种子数据的幂等升级机制很干净**(值得抄这个思路):

- 插入用 `INSERT ... SELECT WHERE NOT EXISTS` —— 只补缺失行
- 升级用**内容寻址**:`UPDATE ... WHERE value = ? AND prompt = ?(旧种子原文)`
  —— **只有用户没改过的行才升级**,改过的视为用户数据保留
- 下架同理:`DELETE WHERE value = ? AND prompt = ?(下架种子原文)`

🔴 **被下架的那一条是 `live`(真人写实)**,注释原文:
> 「真人影像过不了平台真人内容审核,下架。」

---

## 九、对本项目的影响

**本次勘察未产生任何代码**(本仓仍无代码)。设计输入:

1. **三层主干 + 剧级资产 + 两张绑定表**的结构直接采用(独立实现)。
2. **生成单位定为 8–15 秒段落**,不是单镜。分镜规范里那五条可量化规则写成**代码断言**,
   不交给 LLM 自觉(对应 M-5:能确定性测的绝不交给 LLM)。
3. **三类资产的参考图形态**(三视图 / 无人空镜 / 白底单品)作为硬规范写进提示词模板。
4. **`first_frame_image` / `last_frame_image` 进 schema**,首尾帧衔接从第一版就留位。
5. **风格预设做成数据 + 内容寻址的幂等升级**,不硬编码。
6. **产能预期按 8–11 小时/集**做设计,不是 3.7 小时。这个量级决定了:
   队列必须能过夜跑、必须能断点续跑、必须只重跑受影响部分。
7. **不做真人写实风格**(平台审核过不了),第一版就别做。

---

## 十、局限

- ⚠️ **没有运行过该项目**,所有理解来自源码与 README,未经实跑验证。
- ⚠️ 前端(Nuxt3 + Vue3)只看了截图没读代码,「全选未生成项」这类增量补全的具体实现未核实。
- ⚠️ `agents/index.ts`(390 行,Mastra agent 编排)与 `extraction.ts` 未细读。
- ⚠️ 184 秒/16 段这个数据来自**一张截图**(样本量 1),不能当统计结论。
