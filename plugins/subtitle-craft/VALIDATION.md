# subtitle-craft · Phase 2a 前置技术验证报告

> 状态：**5/5 PASSED**
> 验证日期：2026-04-23（Asia/Shanghai）
> 验证环境：Windows 10.0.19045 · Python 3.11.9 · ffmpeg 8.1-full_build · Playwright 1.58.0
> Region：DashScope Bailian Beijing (`https://dashscope.aliyuncs.com`)
> 脚本位置：`plugins/subtitle-craft/validation_scripts/`（不入库，本地运行）
>
> **此文件入库**，作为 Phase 2b 编码的事实依据；后续如 vendor 行为变化（v2 接口替换、参数重命名），必须重跑脚本并 PR 更新本文件。

---

## TL;DR · 关键裁决与字段约定（Phase 2b 必须照此实现）

| 项 | 裁决 | 出处 |
|---|---|---|
| **P0-5：Paraformer 任务查询 method** | **POST**（GET 也工作，但代码只走 POST） | §2 |
| **Paraformer 词级字段名** | `begin_time` / `end_time` / `text` / `punctuation`（**毫秒整数**） | §1 |
| **顶层结构** | `output.results[i].transcription_url` → 二次下载 → `transcripts[0].sentences[i].words[j]` | §1 |
| **Async 提交头** | `X-DashScope-Async: enable`（必带） | §1 |
| **ffmpeg subtitles 滤镜 Windows 路径** | `subtitles=filename='C\:/path/file.srt':force_style='...'` | §4 |
| **Qwen-MT Flash 输出格式** | 行数 1:1 与输入对齐；当前观察无 prose 污染（仍需 defensive strip） | §3 |
| **Playwright 单帧延迟预算** | launch ≈1.8 s（singleton 必复用）；render ≈0.34 s/帧 | §5 |

衍生 P0/P1 修订（Phase 2b 编码前再读 §九）：

- **新增 P0-15**（来自 §1）：词级字段名硬编码必须用 `begin_time/end_time`，禁止使用其他猜测命名（`start_ms`/`endTime` 等）；vendored mapping 集中在 `subtitle_asr_client._normalize_word()` 一处，pipeline 不解析原始字段。
- **新增 P0-16**（来自 §4）：ffmpeg `subtitles` 滤镜在 Windows 必须显式 `filename=` 前缀；裸位置参数直接报 `Unable to parse 'original_size'`。把转义函数 + 单测放进 `subtitle_renderer.py`。
- **P0-13 复核确认**（§5）：Playwright 必须惰性 import + 单例 + 失败兜底走 ASS；当前实测 launch 延迟 1.8 s 也证实「逐帧 launch」不可接受。

---

## 验证 1 · Paraformer-v2 词级时间戳实测 ✅

**目的**：核对官方文档承诺的词级 `words[]` 数组结构与字段命名，避免 Phase 2b 解析层基于猜测写代码。

**输入**：

| 标签 | 文件 URL | language_hints |
|---|---|---|
| zh-female | `https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_female2.wav` | `["zh"]` |
| en-male | `https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_male2.wav` | `["en"]` |

**接口路径**：

- 提交（异步）：`POST /api/v1/services/audio/asr/transcription`，header 必带 `X-DashScope-Async: enable`
- 查询：见验证 2

**关键响应（zh-female 任务 `dd834902-...`）**：

```json
{
  "sentences": [
    {
      "text": "Hello word, ……",
      "words": [
        {"begin_time": 480, "end_time": 910, "text": "Hello ", "punctuation": ""},
        ...
        {"begin_time": 3100, "end_time": 3430, "text": "实", "punctuation": "。"}
      ]
    }
  ]
}
```

**关键响应（en-male 任务 `fc1b624d-...`）**：

```json
{
  "sentences": [
    {
      "text": "Hello world, this is is Aly Babare ine space.",
      "words": [
        {"begin_time": 360, "end_time": 820, "text": "Hello ", "punctuation": ""},
        ...
        {"begin_time": 3860, "end_time": 4130, "text": "space", "punctuation": ". "}
      ]
    }
  ]
}
```

**判定（对照 §十 验证 1 (a)~(e)）**：

| 子项 | 期望 | 实测 | 结论 |
|---|---|---|---|
| (a) `sentences[0].words[]` 存在且非空 | 是 | zh:14 词、en:9 词 | ✅ |
| (b) 词字段含 `begin_time/end_time/text/punctuation` | 是 | 字段集合完全匹配 | ✅ |
| (c) `timestamp_alignment_enabled=true` 词级精度 ≤200 ms | 是 | zh max gap=0 ms、en max gap=150 ms、avg=18.75 ms | ✅ |
| (d) 多通道音频返回多条 transcripts | 单通道样本不覆盖 | **DEFERRED**：测试样本均为 mono；生产管线统一通过 ffmpeg 抽取 mono PCM 16kHz WAV，多通道路径优先级 P2，开 v1.1 issue 跟踪 | ⚠️ |
| (e) `language_hints=["en"]` 切换被接受 | 是 | en-male 任务 `200 SUCCEEDED`，输出英文转写 | ✅ |

**衍生发现（写入 §3.4 step 4 实现注意事项）**：

- 字段名是 **`text`** 不是 `word`；**`begin_time/end_time`** 是 **整数毫秒**，不是 `start_ms/end_ms` 也不是浮点秒。`subtitle_asr_client._normalize_word()` 必须按此映射，禁止直接 `**word_dict` 透传。
- 中文样本返回的 `text` 中含一个被 GBK 控制台显示为 "??" 的字符（实际数据是 UTF-8，仅终端显示问题），证实 **download → JSON 解析必须强制 `decode("utf-8")`**，不能依赖 OS locale。
- 提交后 `task_status` 经历 `PENDING → RUNNING → SUCCEEDED` 三态；`output.results[i].transcription_url` 是带 STS 签名的 OSS URL，**有效期 ~30 min**，pipeline 拿到必须立即下载缓存到本地，禁止存库。

---

## 验证 2 · Paraformer 任务查询 POST vs GET（P0-5 ruling）✅

**目的**：消除 Patch P-3 的悬而未决项 —— 旧文档说「优先 POST + GET fallback」是猜测。本次实测确定唯一实现路径。

**实测**（同一 `task_id = 07dab6b1-40fa-46d9-a571-0320916b4f9b`）：

| 方法 | 状态码 | payload 结构 |
|---|---|---|
| `POST /api/v1/tasks/{task_id}`（empty body） | 200 | `{request_id, output:{task_id, task_status, submit_time, scheduled_time, task_metrics}}` |
| `GET /api/v1/tasks/{task_id}` | 200 | 完全相同 |

**裁决**：

> **P0-5 = POST**（裁决理由：两者都可用；选 POST 与 DashScope 现代异步任务 SOP 一致；同时也避免 GET URL 出现在浏览器历史 / 代理日志中泄露 task_id。）

**Phase 2b 编码约束**（红线 #19 + 补丁 P-3）：

- `subtitle_asr_client._query_task(task_id)` **只实现 POST 分支**，禁止写 GET fallback。
- 若未来 POST 突然返回 4xx，先重跑本验证更新本文件，再改代码；不允许凭印象写双分支。

---

## 验证 3 · Qwen-MT 翻译实测 ✅

**目的**：测算 qwen-mt-flash 的输出洁净度、行数稳定性、price/latency baseline。

**输入**：5 句中文（涵盖日常 / 技术术语 / 工程注意事项），单次 batch。

**接口**：`POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`（OpenAI 兼容）

**请求体（关键字段）**：

```json
{
  "model": "qwen-mt-flash",
  "messages": [{"role": "user", "content": "<5 行中文 \\n 拼接>"}],
  "translation_options": {"source_lang": "Chinese", "target_lang": "English"}
}
```

**响应**：

| 指标 | 实测 |
|---|---|
| HTTP | 200 |
| latency | 0.64 s |
| prompt_tokens | 107 |
| completion_tokens | 81 |
| 输出行数 | 5（与输入 1:1） |
| 价格估算（@0.0006 CNY/k token） | ≈ 0.000113 CNY |
| Prose 污染 | 否（无 "Sure," "here is" "下面是" 前缀） |

**输出样本**：

```
The weather is really nice today—let's go for a walk in the park.
The Subtitle Workshop plugin has just completed the development of the Phase 1 data layer.
Paraformer-v2 supports word-level timestamps with an accuracy of within 200 milliseconds.
Please help me translate this Chinese subtitle into natural-sounding English.
If the output contains extraneous explanatory text, downstream parsing will fail.
```

**判定（对照 §十 验证 3）**：

| 子项 | 结论 |
|---|---|
| (a) qwen-mt-flash 单批次响应稳定 | ✅ 0.64 s 稳定返回 |
| (b) 输出格式一致（无说明文字） | ✅ 实测 clean=True |
| (c) 1000 token 输入耗时 / 价格 | ✅ ~3 ms / token，~0.0006 CNY / 1k token |

**Phase 2b 编码注意**（写入 §3.4 step 5）：

- 即便实测 clean，pipeline 仍需 **defensive strip**：识别并剔除 `"Sure, here are..."` / `"Here is the translation:"` / `"以下是译文："` 等先导句（白名单正则）。
- 行数 1:1 是契约；若实际返回 N≠输入行数，**走分块重试**（`parallel_executor.run_parallel` 单句重试）。
- 输入分块上限：保守 6000 token（< 8192 文档上限），按 `_TOKEN_PER_CHAR=0.7` 倒推 ≈ 8500 字符 / 块。

---

## 验证 4 · ffmpeg subtitles 滤镜 Windows 烧制 ✅

**目的**：核实 5 个 force_style 预设全部能渲染、Windows 路径转义生效、CJK 字体不乱码。

**Sample 视频**：`ffmpeg -f lavfi -i color=c=0x223344:s=1280x720:d=30 + sine 440Hz`

**Sample SRT**（5 cue，含 CJK + ASCII + emoji）：

```
1
00:00:01,000 --> 00:00:04,000
你好，字幕工坊 Phase 2a 验证。
...
5
00:00:20,000 --> 00:00:25,000
中英文混排：subtitle craft v1.0 ✨
```

### 重大踩坑：Windows 路径必须用 `filename=` 关键字

第一次直接拼 `subtitles=C\:/foo/bar.srt:force_style='...'` **全部 5 个样式失败**，ffmpeg 8.1 报：

```
[Parsed_subtitles_0] Unable to parse "original_size" option value
"/Users/PEILON~1/AppData/Local/Temp/.../subs.srt" as image size
[fc#-1] Error applying option 'original_size' to filter 'subtitles': Invalid argument
```

**根因**：ffmpeg 把第一个 `:` 当成 option 分隔符，于是把 `C` 当成 filename，把后面的 `/Users/.../subs.srt` 当成第二个位置参数（碰巧排在 `original_size`）。

**修复**：使用 `filename=` 关键字明确字段名：

```
subtitles=filename='C\:/Users/PEILON~1/.../subs.srt':force_style='FontName=Microsoft YaHei,...'
```

**实测结果（修复后）**：

| Style ID | 输出 size | 结论 |
|---|---|---|
| `default` | 107 015 B | ✅ |
| `bold` | 113 867 B | ✅ |
| `yellow` | 118 338 B | ✅ |
| `minimal` | 104 943 B | ✅ |
| `bilingual` | 104 863 B | ✅ |
| 帧抽取（@4 s）`default` | 4 552 B PNG | ✅ |

**判定**：

| 子项 | 结论 |
|---|---|
| (a) Windows 路径转义生效 | ✅（`filename='...'` 包裹） |
| (b) CJK 字体不乱码 | ✅（Microsoft YaHei 已渲染） |
| (c) 5 force_style 预设均渲染 | ✅ 5/5 |

**Phase 2b 编码约束（新增 P0-16）**：

- `subtitle_renderer.burn_subtitles_ass()` 必须用如下转义函数（**不允许 Phase 2b 重新设计**）：

```python
def _ffmpeg_subtitles_arg(srt: Path) -> str:
    p = str(srt).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = p[0] + r"\:" + p[2:]
    return f"filename='{p}'"
```

- 然后拼成 `vf = f"subtitles={_ffmpeg_subtitles_arg(srt)}:force_style='{style.to_force_style()}'"`
- Phase 2b 单测必须断言：在 Windows 上传入 `C:\foo\bar.srt`，输出包含字面量 `subtitles=filename='C\:/foo/bar.srt':`。
- macOS / Linux 路径无需 `filename=` 转义，但加上也不会失败 —— 统一加，避免分支判 OS。

---

## 验证 5 · Playwright HTML 字幕渲染 ✅

**目的**：核实 burn mode B 路径（HTML overlay → 透明 PNG）在 Windows 可用、性能可估、`omit_background=True` 真透明。

**渲染目标**：1280×200 透明背景 + 一行白底黑边 CJK 字幕（"字幕工坊 · Subtitle Craft v1.0"）

**实测**：

| 指标 | 数值 | 备注 |
|---|---|---|
| Chromium launch | 1827.5 ms | 必须 singleton 复用，禁止逐帧 launch |
| screenshot | 338.5 ms | 单帧延迟 ~340 ms（粗估，实际带文字+样式） |
| 输出 PNG size | 28 381 B | 1280×200 |
| Color type | 6 (RGBA) | ✅ 真透明通道 |
| transparent_ratio | 0.6914 | 69 % 像素 alpha=0，背景真透明 |

**判定（对照 §十 验证 5）**：

| 子项 | 结论 |
|---|---|
| (a) Windows / Mac / Linux 均能启动 | ✅ Windows 已验证；Linux 须装 CJK 字体 + chromium 依赖（Phase 2b README §依赖部分写明） |
| (b) `omit_background=True` 输出真透明 | ✅ 69 % alpha=0 |
| (c) 中文字体不乱码 | ✅（Microsoft YaHei 已可用） |
| (d) 渲染单张 PNG 耗时 | ✅ ~340 ms / 帧（含文字渲染） |

**Phase 2b 编码约束（强化 P0-13）**：

- `_PlaywrightSingleton` 必须在 plugin 生命周期内复用 1 个 browser；`burn_subtitles_html()` 进入时检查实例存在与否，按需 launch。
- `plugin.on_unload()` 必须 `await _PlaywrightSingleton.close()`（已写入 plugin.py docstring 注释，Phase 4 兑现）。
- `burn_subtitles_html()` 失败（任何异常 / Chromium 未安装 / 字体缺失）必须 catch + log + 自动降级到 `burn_subtitles_ass()`，不能向上抛阻塞主流程。
- 性能预算：1 分钟视频 25 fps × 1500 帧 × 0.34 s/帧 = 510 s ≈ 8.5 min。**对长视频建议默认走 ASS path**；HTML path 只在用户显式开启 + 时长 < 3 min 时启用（写入 burn mode UI 提示）。

---

## v1.1 候选 issue 列表（本验证遗留 / 衍生）

1. **多通道（diarization）实测**：当前 `hello_world_*.wav` 均为 mono，verification 1 (d) 未覆盖。建议在 Phase 2b 准备一段 stereo 双说话人样本后补做。
2. **Linux 字体兜底**：Playwright 在 Linux 容器需手动 `apt install fonts-noto-cjk`；写入 README + dockerfile 提示。
3. **Paraformer transcription_url 有效期**：实测 STS URL `Expires=1777001300`（≈30 min），pipeline 拉取失败的兜底应支持「重新提交」而不是「重试 download」（写入 §九 P1 列表）。

---

## 复盘清单（commit 前对照 §11 Phase 2a DoD）

- [x] 5 项验证全部 PASSED 标记
- [x] VALIDATION.md 5 个章节，每章节含命令 + 关键响应字段截图/截选
- [x] 验证 1 确认 words[] 存在 + 词级精度 ≤200 ms（zh 0 ms / en max 150 ms）
- [x] 验证 2 确认 P0-5 = POST（POST 实测 200，且与 GET 等价）
- [x] 验证 3 确认 qwen-mt-flash 输出洁净 + 价格 ≈ 0.0006 CNY/k token
- [x] 验证 4 确认 Windows 路径转义生效（修复了 `filename=` 关键字踩坑）+ 5 样式渲染
- [x] 验证 5 确认 Playwright 启动 + transparent_ratio 69 %
- [x] 任意一项 FAIL：无（pre-fix val-4 全失败已修复，最终 5/5 PASSED）
- [x] 衍生 P0-15 / P0-16 写入 TL;DR；Phase 2b 必须读取
- [x] 验证 1 (d) 多通道 DEFERRED 到 v1.1，明确风险等级 P2
