# SKILL: bgm-suggester

## What

把"场景 + 情绪 + 时长"翻译成结构化 BGM 简报。**它不生成音频**——它告诉你应该用什么风格、bpm、关键词去找/生成音乐，外加 4 套桥接搜索词。

## When

- 用户说"想要一段 BGM 但不知道怎么描述"
- 已经有分镜（storyboard），需要给每段配音乐
- 准备喂 Suno AI / Udio 但不会写 prompt
- 准备去 YouTube/Spotify/Epidemic Sound 找现成 BGM 但不知道搜什么词

## Tools

| Tool | 调用时机 |
|---|---|
| `bgm_create` | 拿到场景描述时，立刻创建任务（参数最少：`scene`） |
| `bgm_status` | 等任务完成（异步走 brain） |
| `bgm_list` | 用户问"最近做过哪些 BGM 简报" |
| `bgm_cancel` | 用户改主意 / 任务卡死 |

## Routes

| Method | Path | 用途 |
|---|---|---|
| POST | `/tasks` | 创建任务（quality gate：`scene` 非空） |
| GET  | `/tasks/{id}` | 取完整 brief + self_check |
| GET  | `/tasks/{id}/export.csv` | 单行 CSV |
| GET  | `/tasks/{id}/export-suno.json` | Suno style + description |
| GET  | `/tasks/{id}/export-search.json` | YouTube/Spotify/Epidemic/Artlist |
| GET  | `/tasks/{id}/export-all.json` | bundle |
| POST | `/tasks/{id}/cancel` | 取消 |

## Pipeline

```
scene + mood + duration
  ↓
build_user_prompt → brain.think_lightweight (走 SDK ErrorCoach 包错)
  ↓
parse_bgm_llm_output (5 级 fallback：JSON / fenced / 内嵌 / k:v / 编号 / stub)
  ↓
self_check (4 类告警：missing_style / few_keywords / bpm_label_mismatch / arc_curve_length)
  ↓
持久化 (BaseTaskManager → SQLite, brief_json + self_check_json 两栏)
  ↓
桥接导出 (CSV / Suno / search-queries / all)
```

## Quality Gates

| Gate | 触发 | 行为 |
|---|---|---|
| **G1 输入完整性** | `scene` 为空 | `400 Bad Request`，由 `QualityGates.check_input_integrity` 渲染建议 |
| **G2 解析兜底** | LLM 输出无法 JSON 解析 | 5 级回退；最终用 stub，**永不抛错** |
| **G3 自检告警** | bpm 与 label 不一致、关键词 < 3、style 缺失等 | 写入 `self_check.issues`，UI 黄色提示，**不阻断**用户 |

## Trust Hooks

- **bpm 硬钳制**：`30..220` —— LLM 幻觉 999 bpm 时不会污染数据库
- **deterministic stub**：无 brain 时仍能完成任务（走 `stub_brief_text`），便于离线/调试
- **csv 字段安全**：所有导出字段都过 `_csv_safe`（双引号转义、换行剔除）
- **Suno style ≤120 字符**：超长会被 Suno UI 截断或拒绝

## Known Pitfalls

1. **没有 brain provider** → 简报会用 stub，能落库但内容固定。**对话提示用户**：装一个 LLM provider 才能产出有意义的 BGM 描述
2. **LLM 把 keywords 写成中文逗号串**："lofi，chill，calm" 也会被切开（`_coerce_str_list` 兼容 `,`/`，`/`、`）
3. **mood_arc 与 energy_curve 长度不一致** → self_check 会标 `arc_curve_length` info，不阻断
4. **Suno style 字段过长**会被截到 120 字符 —— 优先保留 `style + 前 5 个 keyword + 前 3 个 instrument`

## Hardening Notes (Sprint 5)

- 子类化 `BaseTaskManager` —— 直接继承 SDK 的 SQL 白名单 / cancel_task 行为
- 沿用 `ErrorCoach` / `QualityGates` / `UIEventEmitter` —— UI 事件契约与 storyboard 一致
- `parse_llm_json_object` 来自 SDK `contrib.llm_json_parser` —— 与 `storyboard` / `video-translator` 共享 5 级回退实现
- 新插件**零碰**任何现有代码 —— 只新增 `plugins/bgm-suggester/` 目录
