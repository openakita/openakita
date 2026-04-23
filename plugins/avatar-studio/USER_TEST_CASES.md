# Avatar Studio 用户测试用例

> 版本: 1.1.0 | 适用于 OpenAkita + DashScope (阿里云百炼) / RunningHub / 本地 ComfyUI
> 预估总耗费: **¥5–15**（全部用例跑完，DashScope 路径；RunningHub/本地 ComfyUI 路径单独计）
> 预估总耗时: **约 60–90 分钟**（含模型排队等待）
>
> 本插件 5 个 Tab（创建 / 任务 / 音色库 / 形象库 / 设置）+ 5 种生成模式（照片说话 / 视频换嘴 / 视频换人 / 数字人合成 / 图生动作）+ 3 种后端（阿里云 / RunningHub / 本地 ComfyUI）+ 双 TTS 引擎（CosyVoice / Edge-TTS），本文按 Tab 顺序穷举。

---

## 〇 · 前置准备

### 0.1 DashScope API Key（必需，国内首选）

1. 访问 [阿里云百炼控制台](https://bailian.console.aliyun.com/)，登录后进入「API-KEY 管理」。
2. 创建或复制一个 API Key（`sk-` 开头），账户余额 ≥ ¥20（全部用例 ≤ ¥15，留一些 buffer）。
3. 在百炼控制台 → 「模型广场」中**逐一开通**（点 "开通" 即可，无需付费）：
   - `wan2.2-s2v-detect`（人脸预检 — 必装，照片说话/数字人合成会用）
   - `wan2.2-s2v`（数字人对口型 — 必装）
   - `videoretalk`（视频换嘴 — 视频换嘴模式必装）
   - `wan2.2-animate-mix`（视频换人 — 视频换人模式必装）
   - `wan2.2-animate-move`（图生动作 — 图生动作模式必装）
   - `wan2.7-image`（多图融合 — 数字人合成可选，否则用 `wan2.5-i2i-preview`）
   - `cosyvoice-v2`（TTS — 用百炼音色时必装）
   - `qwen-vl-max`（多模态融合 prompt 自动生成 — 数字人合成可选）

### 0.2 阿里云 OSS 配置（DashScope 后端必需）

> DashScope 不能直接 fetch 本地 `localhost` URL，所有上传素材都必须通过 OSS 转中转给云端。

1. 访问 [阿里云 OSS 控制台](https://oss.console.aliyun.com/)。
2. 创建 Bucket（**与 DashScope 同区域**，推荐杭州 `oss-cn-hangzhou` 或北京 `oss-cn-beijing`）。
3. 创建 RAM 子用户 → 授予「`AliyunOSSFullAccess`」权限 → 生成 AccessKey ID / Secret。
4. 设置 → 阿里云 DashScope → 展开「OSS 对象存储」配置：
   - Endpoint：`oss-cn-hangzhou.aliyuncs.com`（不要带 `https://` 和 bucket 名称前缀）
   - Bucket：你的 bucket 名（如 `my-avatar-studio`）
   - AccessKey ID / Secret：上一步生成的
5. 点「测试连接」→ 显示「OSS 配置可用」即成功。

### 0.3 RunningHub Key（可选，DashScope 不可用时备选）

1. 访问 [RunningHub 控制台](https://www.runninghub.cn/console/apikey)。
2. 注册 → 创建 API Key（首次注册赠送试用额度）。
3. 设置 → RunningHub → 填 API Key → 选择 Instance type（轻度跑 `Basic`，复杂跑 `Plus`/`Pro`）。
4. 「Workflow 预设」可先留空，创建任务时再选「自定义填写」。

### 0.4 本地 ComfyUI（可选，离线场景）

1. 本机安装并启动 ComfyUI（默认 `http://127.0.0.1:8188`）。
2. 加载 wan2.2-s2v / videoretalk 等对应 workflow。
3. 设置 → 本地 ComfyUI → URL 填 `http://127.0.0.1:8188` → 测试连接。

### 0.5 Edge-TTS（默认免费，无需任何配置）

- 微软 Edge 浏览器内置的 TTS，**完全免费、无需 API Key**。
- 在国内可直连 `speech.platform.bing.com`（部分网络可能需要镜像，详见 [edge-tts GitHub README](https://github.com/rany2/edge-tts)）。
- 设置 → TTS 引擎 → 切换到「Edge-TTS（免费）」即可使用。

### 0.6 测试素材表（国内**直连可下载**为主）

| 编号 | 素材 | 来源 / 获取方式 | 用途 |
|---|---|---|---|
| **IMG-01** | 正面单人女像 PNG | DashScope 官方示例：<br>`https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20240829/lyumdf/female_2.png` | TC-C01 / TC-F01 / TC-C04 主流程 |
| **IMG-02** | 正面单人男像 PNG | DashScope 官方示例：<br>`https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20240828/jbfmnp/male_3.png` | TC-C03 视频换人 / TC-C05 图生动作 |
| **IMG-03** | 第二张参考人像 | 可用 IMG-01 或 IMG-02，或自己拍一张正脸照 | TC-C04 数字人合成（多图融合） |
| **AUD-01** | 中文示例 MP3（≈ 5 s） | DashScope 官方：<br>`https://dashscope.oss-cn-beijing.aliyuncs.com/audios/welcome.mp3` | TC-C01 / TC-C02 用「上传音频」分支 |
| **AUD-02** | 长一些的中文 WAV（≈ 16 kHz, ~10 s） | DashScope 官方：<br>`https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_male2.wav` | TC-C02 备用，验证 wav 转码 |
| **VID-01** | 30 s 中文短视频（mp4，单人讲话） | **方案 A（推荐，5 秒搞定）**：用 ffmpeg 把 IMG-01 + AUD-01 合成静态视频：<br>`ffmpeg -loop 1 -i female_2.png -i welcome.mp3 -c:v libx264 -tune stillimage -c:a aac -shortest -t 30 -pix_fmt yuv420p talker.mp4`<br>**方案 B（实拍）**：[Pexels 中文搜索](https://www.pexels.com/zh-cn/search/videos/中文%20讲话/) → 选 ≤30 s 单人正面讲话 → 下载 720p<br>**方案 C（B 站投稿）**：[bbdown](https://github.com/nilaoda/BBDown) 下载 CC0 / 公开演讲，自行裁切到 30 s | TC-C02 视频换嘴 / TC-C03 视频换人主流程 |
| **VID-02** | 5–10 s 动作短视频（跳舞 / 走路 / 打招呼） | **方案 A（推荐）**：[Pexels 动作分类](https://www.pexels.com/zh-cn/search/videos/dance/) → 找 5 s 单人跳舞 / 走路 → 下载 720p<br>**方案 B**：[Mixkit free dance](https://mixkit.co/free-stock-video/dance/) → 同上<br>**方案 C**：[Pixabay 中文](https://pixabay.com/zh/videos/search/dance/) → CC0 免费下载 | TC-C05 图生动作 |
| **TEXT-01** | 简短中文文案（≤ 30 字） | 直接用 `"你好，欢迎来到数字人工作室。今天天气不错。"` | TC-C01 / TC-C02 / TC-C04 文本输入 |
| **TEXT-02** | 中等中文文案（30–80 字） | 直接用 `"OpenAkita 数字人工作室让你 5 分钟生成专属数字人视频，支持照片说话、视频换嘴、视频换人、多图融合、图生动作五大模式，欢迎体验。"` | TC-C04 数字人合成 |

> 全部素材建议放在 `D:\OpenAkita\samples\avatar-studio\` 下，跑测试时拖入 UI 即可。
> **DashScope OSS 与阿里云帮助 CDN 在国内均无需任何代理**，是首选稳定来源。
> Pexels / Mixkit 国内访问偶尔慢，可换 [Pixabay 中文](https://pixabay.com/zh/videos/) 或 [Coverr 国内 CDN](https://coverr.co/)。

### 0.7 一键素材脚本（Windows PowerShell）

```powershell
# 1. 创建目录
mkdir D:\OpenAkita\samples\avatar-studio -Force | Out-Null
cd D:\OpenAkita\samples\avatar-studio

# 2. 下载基础人像 + 音频（国内直连，< 5 秒）
Invoke-WebRequest "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20240829/lyumdf/female_2.png" -OutFile female.png
Invoke-WebRequest "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20240828/jbfmnp/male_3.png" -OutFile male.png
Invoke-WebRequest "https://dashscope.oss-cn-beijing.aliyuncs.com/audios/welcome.mp3" -OutFile welcome.mp3
Invoke-WebRequest "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_male2.wav" -OutFile zh-male.wav

# 3.（可选）用 ffmpeg 合成 30 s 单人讲话视频（用于视频换嘴 / 换人测试）
ffmpeg -y -loop 1 -i female.png -i welcome.mp3 -c:v libx264 -tune stillimage -c:a aac -shortest -t 30 -pix_fmt yuv420p talker.mp4
```

> 跑完即可覆盖 80% 的用例。VID-02（动作视频）需要从 Pexels / Pixabay 手动下载，因为 ffmpeg 合成的静态视频无法用于图生动作模式。

---

## 一 · Settings Tab（设置）— 最先测试

### TC-S01：首次配置 DashScope API Key

**前提**：插件刚加载，尚未配置 API Key

**操作**：
1. 打开「设置」Tab，**阿里云 DashScope** 区块。
2. API Key 输入框粘贴你的 `sk-xxx`。
3. 点输入框右侧「测试连接」按钮。

**预期**：
- 顶部出现 toast「DashScope 连接成功」（绿色）。
- 顶部 banner「未配置 API Key」消失（红色 → 隐藏）。
- 输入框失焦后自动保存（无需点保存按钮）。

**失败现象**：
- toast「Auth failed」→ Key 错误或区域不匹配（国内 Key 用 `dashscope.aliyuncs.com`，国际版用 `-intl`）。
- toast「Network」→ 检查代理/防火墙。

---

### TC-S02：配置阿里云 OSS

**前提**：TC-S01 已完成

**操作**：
1. 设置 → 阿里云 DashScope → 展开「OSS 对象存储」折叠面板。
2. 4 个字段依次填入（**所有字段都不要带前缀 `https://` 或后缀 `/`**）：
   - Endpoint：`oss-cn-hangzhou.aliyuncs.com`
   - Bucket：`my-avatar-studio-test`（你创建的）
   - AccessKey ID：`LTAIxxx`
   - AccessKey Secret：完整 secret
3. 失焦后等 2 秒，观察输入框边框颜色。

**预期**：
- 4 个字段边框正常（非红色）。
- 顶部 banner「阿里云 OSS 未配置」消失。
- 「测试连接」按钮变为可用状态。

**失败现象**：
- Endpoint 字段红框 + 警告图标 → 鼠标悬停看 tooltip 提示，常见错误：填了完整 URL（应该只填域名）。
- Bucket 字段红框 → 名称含非法字符或长度超限。

---

### TC-S03：TTS 引擎切换

**操作**：
1. 设置 → 滚动到 **TTS 引擎** 区块。
2. 点「Edge-TTS（免费）」radio。
3. 观察音色选择器是否出现。
4. 切回「CosyVoice（百炼付费）」。

**预期**：
- 切换 radio 后**不弹 toast**（这是 v1.1.1 修复点：之前会误弹「保存成功」）。
- 切到 Edge-TTS：下方出现 12 个微软中文音色（云希、晓晓、晓伊...）。
- 切回 CosyVoice：音色选择器隐藏（统一在「创建」Tab 选）。

---

### TC-S04：默认数据目录显示

**操作**：
1. 设置 → 滚动到 **存储与清理** 区块。
2. 数据目录输入框留空，观察下方提示。

**预期**：
- 输入框 placeholder 显示「留空 = 使用默认目录（由 OpenAkita 管理）」。
- 输入框下方一行显示「当前生效：`D:\OpenAkita\data\plugin_data\avatar-studio\data`」（具体路径因系统不同）。
- 点「浏览」按钮 → 弹出 FolderPickerModal（宽度 ≈ 960px，左侧快捷入口、右侧文件列表）。

---

### TC-S05：RunningHub 配置（可选）

**操作**：
1. 设置 → **RunningHub** 区块。
2. 填 API Key → 选 Instance type（默认 `Plus` 即可）。
3. 展开「Workflow 预设」折叠面板，给「照片说话」填一个 workflow_id（可在 RunningHub 模板广场复制现成的）。
4. 点「测试连接」。

**预期**：
- 测试连接成功 → toast「RunningHub 连接成功」。
- 「创建」Tab 切换到 RunningHub 后端时，照片说话的 Workflow ID 选择器默认选中你预设的值。

---

## 二 · Voices Tab（音色库）

### TC-V01：浏览 12 个 CosyVoice 系统音色

**操作**：
1. 切到「音色库」Tab。
2. 顶部「系统音色」区列出 12 张卡片（龙小淳 / 龙小白 / 龙小诚 等）。
3. 点任一卡片的「试听」按钮。

**预期**：
- 试听播放约 5 秒中文示例。
- 卡片显示 voice_id（如 `longxiaochun_v2`）+ 性别 + 风格标签。

**失败现象**：
- 试听 401 → API Key 未配置或没开通 cosyvoice-v2。

---

### TC-V02：克隆自定义音色（可选）

**前提**：你有一段 5–30 秒的清晰人声 mp3/wav

**操作**：
1. 「音色库」Tab → 点顶部「克隆新音色」按钮。
2. 输入名称（如「我的声音」）→ 上传音频 → 提交。
3. 等待 60 s 左右，刷新音色列表。

**预期**：
- 自定义音色出现在「我的音色」区。
- 「试听」可正常播放（用上传音频复刻你的声线）。

---

## 三 · Figures Tab（形象库）

### TC-F01：上传形象 + 自动预检

**操作**：
1. 「形象库」Tab → 点「上传新形象」。
2. 名称输入「测试女」→ 上传 IMG-01（DashScope 示例女像）→ 提交。

**预期**：
- 卡片立即出现，状态先为「预检中…」（spinner）。
- ≈ 3–10 秒后变为「✓ 已通过」（绿色）。
- 卡片底部显示 `humanoid=true`、`face_count=1`。

**失败现象**：
- 「✗ 检测失败：humanoid=false」→ 上传图不是真人正脸（卡通/动物/侧脸）。
- 「✗ skipped」→ API Key 未配置。

---

### TC-F02：在创建 Tab 复用形象

**操作**：
1. 切到「创建」Tab → 选「照片说话」模式。
2. 「人像」上传区下方有「从形象库选」按钮 → 点击。
3. 弹出弹窗，选 TC-F01 上传的「测试女」→ 确认。

**预期**：
- 上传区显示形象缩略图 + 名称。
- 表单 figure_id 字段被填充（提交时直接复用 OSS URL，无需重新上传）。

---

## 四 · Create Tab（创建）— 5 种模式核心

### TC-C01：照片说话（photo_speak）— 最便宜入门

**前提**：TC-S01 / TC-S02 完成；准备 IMG-01

**操作**：
1. 「创建」Tab → 顶部模式选「照片说话」（默认即是）。
2. **选择后端** = 阿里云 DashScope（默认）。
3. 「人像 / 主体图」拖入 IMG-01 或贴 URL。
4. 「文本+声音」**文本分支**：粘贴 TEXT-01。
5. 音色选默认 `longxiaochun_v2`（龙小淳）。
6. 分辨率 480P，时长保持自动（由 TTS 音频长度决定）。
7. 点「提交任务」。

**预期**：
- 提交前弹出「成本明细」弹窗，显示 face_detect ¥0.004 + s2v 480P × 7s ≈ ¥3.50 + tts 18 字 ≈ ¥0.0036 = **总计 ≈ ¥3.51**。
- 总价 < 阈值 ¥5 → 直接提交，不再二次确认。
- 提交成功后**右侧预览区**保留预览状态（v1.1.1 修复：之前点「继续创建」预览会消失）。
- 跳转「任务」Tab，新行 status：pending → running → succeeded。
- 进度条每 8 秒自动刷新（v1.1.1 修复）。
- succeeded 后右侧详情面板自动播放视频。

**成本**：约 ¥3.50（480P × 7s）。

---

### TC-C02：视频换嘴（video_relip）

**前提**：准备 VID-01（30 s 单人讲话）+ AUD-01

**操作**：
1. 「创建」Tab → 选「视频换嘴」模式。
2. 后端 = 阿里云 DashScope。
3. 「源视频」拖入 VID-01。
4. 「文本+声音」**音频分支**：拖入 AUD-01（5 s 中文音频）。
5. 提交。

**预期**：
- 估价 ≈ videoretalk × 5s × ¥0.20/s = **¥1.00**（按音频时长计算，不是视频时长）。
- 完成后视频时长被截短到音频长度（5 s），原视频画面保留，嘴型改为新音频。
- 任务详情显示 `videoretalk` 模型 + 5 s 用量。

**成本**：约 ¥1.00。

---

### TC-C03：视频换人（video_reface）— 最贵，谨慎跑

**前提**：准备 VID-01（30 s 单人讲话）+ IMG-02（男像）

⚠️ **价格警告**：`wan-pro` 单价 ¥1.20/s，30 s 视频 = ¥36！**务必用 `wan-std` + 短视频测试**。

**操作**：
1. 「创建」Tab → 选「视频换人」模式。
2. 后端 = 阿里云 DashScope。
3. 「人像 / 主体图」拖入 IMG-02（要换成的新人像）。
4. 「源视频」拖入 VID-01（被换的原视频，**先裁剪到 5 s** 再上传）。
5. ⚠️ 「质量等级」**不要勾选 wan-pro**，保持默认 wan-std。
6. 提交 → 估价弹窗显示 ≈ wan-std × 5s × ¥0.40/s = **¥2.00**。

**预期**：
- 估价对得上：¥0.40/s × 5s = ¥2.00（std）。
- 完成后视频中原人物被替换成 IMG-02 的人像，动作和场景保留。
- 任务详情显示 `wan2.2-animate-mix (wan-std)` 模型。

**失败现象**：
- 「dependency: humanoid=false」→ IMG-02 不是清晰真人正脸，换图。
- 「moderation」→ 视频含敏感内容，换 VID-01。

**成本**：约 ¥2.00（5 s × wan-std）。

> **不要**为了"看效果"提交 720P + 15s + wan-pro 的视频换人 —— 那一次就是 ¥18 的真金白银。

---

### TC-C04：数字人合成（avatar_compose）— 多图融合

**前提**：准备 IMG-01 + IMG-02（两张不同人像）+ TEXT-02

**操作**：
1. 「创建」Tab → 选「数字人合成」模式。
2. 后端 = 阿里云 DashScope。
3. 「参考图」上传 IMG-01 + IMG-02（最多 3 张）。
4. 「融合 prompt」输入：`一位介于两张参考图之间风格的虚拟主播，正面、清晰、专业造型`
   - **或**点「✨ 自动生成 Prompt」（调用 qwen-vl-max 多模态自动起草，约 ¥0.005）。
5. 「文本+声音」文本分支：粘贴 TEXT-02。
6. 音色 `longxiaobai`（龙小白，活泼风格适合介绍场景）。
7. 提交。

**预期**：
- 估价 ≈ wan2.7-image × 2 张 × ¥0.20 + face_detect + s2v 480P × 8s = **¥4.40 左右**（详情依音频时长）。
- 流程：先合成新形象（约 30 s）→ 再做 s2v（约 60 s）→ 总耗时 2–4 分钟。
- 完成后视频中数字人是融合两张参考图特征的全新形象，开口讲 TEXT-02。

**成本**：约 ¥4.40。

---

### TC-C05：图生动作（pose_drive）— v1.1 新增

**前提**：准备 IMG-02（男像）+ VID-02（5 s 跳舞 / 走路视频）

**操作**：
1. 「创建」Tab → 顶部模式条最右侧「图生动作」（v1.1 新增）。
2. 后端 = 阿里云 DashScope。
3. 「人像 / 主体图」拖入 IMG-02。
4. 「动作参考视频」拖入 VID-02（**短一点，5 s 即可**，按时长计费）。
5. 质量等级保持默认 wan-std。
6. 提交 → 估价 ≈ wan2.2-animate-move × 5s × ¥0.40/s = **¥2.00**。

**预期**：
- 估价对得上。
- 完成后视频中 IMG-02 的人像复刻 VID-02 的动作（跳舞/走路），背景为静态。
- 任务详情显示 `wan2.2-animate-move (wan-std)` 模型。

**失败现象**：
- 「animate-mix not opened」→ DashScope 模型未开通，回 0.1 步开通 `wan2.2-animate-move`。

**成本**：约 ¥2.00。

---

### TC-C06：后端切换 + 配置校验

**操作**：
1. 「创建」Tab → 选「照片说话」。
2. 后端切到「RunningHub」（如果未配置 Key，按钮显示半透明）。
3. 不配置 RH，直接点提交 → 应弹 toast「请先配置 RunningHub API Key」。
4. 切回「阿里云 DashScope」→ 再次提交。

**预期**：
- 切换后端时，**右侧 ModelInfoCard 立即跟随更新**（DashScope 显示模型 + 单价；RH 显示 workflow_id；本地 ComfyUI 显示「本地推理免费」）。
- ✓ 标记跟随当前选中的后端（v1.1.1 修复：之前 ✓ 是「configured」标记，与选中无关）。
- 切回 DashScope 后提交不会再弹 RH 错误（v1.1.1 修复闭包陷阱）。

---

### TC-C07：估价失败排查

**操作**：
1. 「创建」Tab → 选任一模式。
2. 上传必要素材，但**不要**填 voice_id 或文本（让某些字段为空）。
3. 提交。

**预期**：
- 估价应仍然成功返回（CostPreviewBody 接受所有字段为可选）。
- 提交时如果有缺项，后端会返「VALIDATION」错误，UI 显示具体哪个字段缺失。

> **历史 bug**：v1.1.0 曾因 `CostPreviewBody` 严格拒绝未声明字段（`extra="forbid"`），导致 backend / workflow_id 等新字段一来就 422 → 估价失败。v1.1.1 已修复。

---

### TC-C08：使用 Edge-TTS 免费跑通照片说话

**前提**：TC-S03 已切换到 Edge-TTS

**操作**：
1. 「创建」Tab → 选「照片说话」→ 后端 DashScope。
2. 拖入 IMG-01。
3. 文本输入 TEXT-01。
4. 音色选「云希」（默认）—— 此时是 Edge-TTS 的微软云希音色，**完全免费**。
5. 提交。

**预期**：
- 估价明细中**没有 cosyvoice TTS 这一行**（Edge 免费），只有 face_detect + s2v 两项 ≈ ¥3.50。
- 完成后视频中数字人用云希音色讲话（不是龙小淳）。

**适用场景**：练手、跑大量短视频时省 TTS 钱。

---

### TC-C09：表单恢复 + 继续创建

**操作**：
1. 「创建」Tab → 填好任意一种模式的所有字段。
2. 提交 → 跳到任务页 → 切回「创建」Tab。

**预期**：
- 表单内容**完整保留**（草稿持久化到 localStorage）。
- 点击「TaskStartedModal」弹窗的「继续创建」按钮 → modal 关闭，表单**清空**，但右侧预览仍然显示上一次任务的进度（v1.1.1 修复：之前 modal 关闭后预览会一起消失）。

---

## 五 · Tasks Tab（任务）

### TC-T01：自动轮询刷新

**前提**：TC-C01 提交了一个任务，状态还是 running

**操作**：
1. 切到「任务」Tab。
2. 不要手动点刷新，等待 8 秒。

**预期**：
- 列表自动刷新（v1.1.1 修复：之前要手动切 Tab 才更新）。
- 状态由 `running` → `succeeded` 后，自动停止轮询（不再无谓请求）。

---

### TC-T02：右侧详情视频预览

**操作**：
1. 「任务」Tab → 左侧任务列表点一个 succeeded 任务。
2. 观察右侧详情面板。

**预期**：
- **顶部标题行**：「任务详情 task_xxx」+ 右侧操作按钮（复制 ID / 下载 / 删除）—— **下载按钮在标题行**，不在视频下方独立成行（v1.1.1 修复）。
- **下方**：黑色 16:9 比例视频播放器，自动循环播放生成视频。
- 视频源**优先用本地 `/tasks/{id}/video`**（v1.1.1 新增）—— DashScope CDN 24 小时过期后仍能播放。
- 视频下方依次：基础信息（ID/模式/模型/创建时间）→ 费用明细卡片 → 参数 JSON → 元数据 JSON。

---

### TC-T03：未输出文件 + 重新查询

**前提**：你有一个 status=succeeded 但 `output_url` 为 `null` 的任务（可能是 v1.1.0 之前 `_extract_output_url` bug 留下的）

**操作**：
1. 「任务」Tab → 点该任务 → 右侧应显示「任务已完成但未返回输出文件」+ 橘黄色「重新查询」按钮（v1.1.2 修复样式）。
2. 点「重新查询」。

**预期**：
- 后端调用 DashScope `query_task` 重新拉 output_url。
- 如果 CDN 还没过 24 h 有效期 → toast「已找回视频并下载到本地」+ 视频开始播放。
- 如果已过期 → toast「DashScope 未返回输出，链接可能已过期」。

> **24 h 后无救**：DashScope 不长期保留生成结果，超时后没有任何方式找回。下次跑任务务必让 finalize 步骤完成本地下载。

---

### TC-T04：取消运行中的任务

**前提**：TC-C03 提交了一个 30s 视频换人，正在 running

**操作**：
1. 「任务」Tab → 点该任务 → 右侧详情。
2. 视频区域显示 spinner + 进度条 + 「取消任务」按钮（黑底容器内）。
3. 点「取消任务」。

**预期**：
- 后端调用 DashScope `cancel_task`。
- 状态变为 `cancelled`。
- 视频区域显示「任务已取消」提示。

---

### TC-T05：失败任务重试

**操作**：
1. 故意触发一个失败任务（如上传一张卡通图做照片说话 → humanoid=false）。
2. 「任务」Tab → 点该任务 → 右侧详情显示 ErrorPanel（红框 + 错误码 + 中英 hints）。
3. 点标题行「重试」按钮（仅 failed 状态显示）。

**预期**：
- 用相同 params 创建一个新任务。
- 旧的 failed 任务保留，新任务 status=pending。

---

### TC-T06：筛选 + 搜索

**操作**：
1. 「任务」Tab → 顶部筛选区。
2. 「状态」点「已完成」→ 列表只显示 succeeded 任务（v1.1.1 修复：之前过滤值是 `done`，但后端实际用 `succeeded`，导致永远过滤不出）。
3. 「模式」点「视频换人」→ 列表只显示 video_reface 任务。

**预期**：
- 两个筛选可叠加。
- 任务列表实时更新。

---

## 六 · 通用回归

### TC-R01：API Key 热更新

**操作**：
1. 设置 → 改 API Key 为另一个 → 失焦保存。
2. 立即去「创建」Tab 提交一个任务。

**预期**：
- 新任务用新 Key 调用 DashScope（不必重启插件 / 主进程）。
- 这是 Pixelle A10 的核心改进。

---

### TC-R02：5 个模式的 chip 尺寸统一

**操作**：
1. 「创建」Tab → 顶部模式选择条。
2. 观察 5 个 chip 的尺寸是否一致。

**预期**：
- 所有 chip 等宽等高（v1.1.1 修复：之前 grid 固定 4 列，第 5 个换行尺寸不一）。

---

### TC-R03：视频缩略图 + 自适应

**操作**：
1. 「任务」Tab → 左侧任务列表。
2. 观察 succeeded 任务的缩略图。

**预期**：
- 视频元素以 `objectFit: contain` 适配 16:9 容器（不拉伸变形）。
- 视频源用本地 `/tasks/{id}/video` 代理（v1.1.1 新增）。

---

## 七 · 工具调用（OpenAkita 主对话）

avatar-studio 注册了 9 个 tool，可在主对话直接调用：

```text
@avatar_cost_preview mode=photo_speak audio_duration_sec=3 resolution=480P
@avatar_photo_speak image_url=https://... text="你好" voice_id=longxiaochun_v2
@avatar_video_relip video_url=https://... audio_url=https://...
@avatar_video_reface image_url=https://... video_url=https://... mode_pro=false
@avatar_compose ref_images_url=[https://...] prompt="..." text="..."
@avatar_pose_drive image_url=https://... video_url=https://...
```

每个 mode 工具返回 `任务已创建：{id}（mode=...）`；后续状态在 Tasks Tab 查看。

---

## 八 · 用例汇总表

| 编号 | 模式 | 后端 | TTS | 预估成本 | 预估耗时 | 必须 |
|---|---|---|---|---:|---:|:-:|
| TC-S01 | 设置 API | — | — | ¥0 | 1 min | ✓ |
| TC-S02 | 设置 OSS | — | — | ¥0 | 5 min | ✓ |
| TC-S03 | TTS 切换 | — | 双 | ¥0 | 1 min | ✓ |
| TC-V01 | 浏览音色 | — | CosyVoice | ¥0 | 2 min | — |
| TC-F01 | 形象预检 | DashScope | — | ¥0.004 | 1 min | — |
| TC-C01 | 照片说话 | DashScope | CosyVoice | ¥3.50 | 2 min | ✓ |
| TC-C02 | 视频换嘴 | DashScope | — | ¥1.00 | 2 min | ✓ |
| TC-C03 | 视频换人 | DashScope | — | ¥2.00 | 3 min | ✓ |
| TC-C04 | 数字人合成 | DashScope | CosyVoice | ¥4.40 | 4 min | — |
| TC-C05 | 图生动作 | DashScope | — | ¥2.00 | 3 min | — |
| TC-C06 | 后端切换 | 全 | — | ¥0 | 2 min | ✓ |
| TC-C08 | Edge-TTS 免费 | DashScope | Edge | ¥3.50 | 2 min | — |
| TC-T01–T06 | 任务页全套 | — | — | ¥0 | 5 min | ✓ |
| **合计** | — | — | — | **≈ ¥16.5** | **≈ 35 min** | — |

> **建议跑通 ✓ 标记的核心 8 条 = ¥10 / 15 min**，其余按需。
>
> 跑完所有 ✓ 任务后，回到「任务」Tab 应该看到 8+ 条已完成记录，每条都能正常播放视频 + 显示费用明细。**任意一条卡 pending > 5 min 或 failed → 抓 task_id + metadata.json 反馈**。

---

## 九 · 已知限制 / 边界情况

- **DashScope 异步任务并发上限 = 1 / API Key**。同一 Key 同一时刻只能跑一个任务。
- **任务 24h 后过期**。CDN URL 失效，本地未归档的视频无法找回。
- **wan-pro 价格陷阱**：单价 ¥1.20/s，请用 cost-preview 弹窗确认。
- **OSS 必须配置**：所有 DashScope 任务都需要素材通过 OSS 中转，没有 OSS 任务直接 422。
- **RunningHub / 本地 ComfyUI 不需要 OSS**：直接传本地文件路径给 ComfyKit 即可。
- **图生动作（pose_drive）需要单独开通 `wan2.2-animate-move` 模型**，国内某些子账号默认未开通。

---

## 十 · 反馈模板

如发现 bug，请按以下格式反馈到项目 issue：

```markdown
**用例编号**: TC-Cxx
**模式 / 后端 / TTS**: photo_speak / DashScope / Edge-TTS
**复现步骤**:
1. ...
2. ...
**预期**: ...
**实际**: ...
**task_id**: task_xxx (从「任务」Tab 复制)
**metadata.json**: 见 D:\OpenAkita\data\plugin_data\avatar-studio\data\tasks\<task_id>\metadata.json
**截图**: 附图
**插件日志**: D:\OpenAkita\data\plugin_data\avatar-studio\logs\avatar-studio.log 末尾 50 行
```
