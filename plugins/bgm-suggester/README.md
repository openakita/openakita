# bgm-suggester

把一段**场景描述 + 情绪 + 时长**变成结构化 BGM 简报，并桥接到主流 AI 音乐生成器（Suno）和素材库搜索词（YouTube / Spotify / Epidemic Sound / Artlist）。

## 给新用户

- 你不会写音乐 —— 没关系，告诉它"海边日落 vlog，平静温暖，30 秒"，它给你 BGM 风格、bpm、情绪曲线和 4 套现成搜索词
- 不需要 ffmpeg，不需要下载模型，不需要 GPU
- 走 LLM（任意 brain provider 即可）
- 输出可以直接喂 Suno / YouTube 搜索

## 三大核心能力

1. **结构化 BGM 简报**：风格、bpm、tempo_label、情绪曲线、关键词、避免词、推荐乐器
2. **5 级 LLM 输出兜底解析**：JSON / fenced 代码块 / 文本中嵌入 JSON / `key: value` 列表 / 编号列表 / 纯文本兜底 stub —— 无论 LLM 多脏都能落库
3. **4 种导出桥接**
   - `export.csv` —— 单行 CSV，Excel/Google Sheets 直开
   - `export-suno.json` —— `style` + `description` 两栏，Suno Custom 模式直接粘贴
   - `export-search.json` —— YouTube/Spotify/Epidemic/Artlist 4 套搜索词
   - `export-all.json` —— 上面所有内容的 bundle

## 配置

| 键 | 默认 | 说明 |
|---|---|---|
| `default_duration_sec` | `30` | 创建任务时未传时长则用此值 |
| `default_language` | `auto` | `zh` / `en` / `auto` |
| `default_tempo_hint` | `""` | 全局节拍偏好（每个任务可覆写） |

## API 速查

```bash
# 创建简报
POST /api/plugins/bgm-suggester/tasks
{
  "scene": "海边日落 vlog",
  "mood": "calm, nostalgic",
  "target_duration_sec": 30,
  "tempo_hint": "midtempo"
}
# → { "task_id": "t_xxx", "status": "queued" }

# 查任务（包含完整 brief + self_check）
GET  /api/plugins/bgm-suggester/tasks/{task_id}

# 导出
GET  /api/plugins/bgm-suggester/tasks/{task_id}/export.csv
GET  /api/plugins/bgm-suggester/tasks/{task_id}/export-suno.json
GET  /api/plugins/bgm-suggester/tasks/{task_id}/export-search.json
GET  /api/plugins/bgm-suggester/tasks/{task_id}/export-all.json

# 取消
POST /api/plugins/bgm-suggester/tasks/{task_id}/cancel
```

## 工具调用（Agent / brain）

| Tool | 输入 | 用途 |
|---|---|---|
| `bgm_create` | `{scene, mood?, target_duration_sec?, tempo_hint?}` | 创建简报任务 |
| `bgm_status` | `{task_id}` | 查状态 |
| `bgm_list`   | `{}` | 列出最近 20 条 |
| `bgm_cancel` | `{task_id}` | 取消任务 |

## 测试

```bash
py -3.11 -m pytest plugins/bgm-suggester/tests -v
```

29 个纯函数测试覆盖：5 级解析回退 / bpm 钳制 / tempo_label 校准 / self_check 4 类告警 / CSV/Suno/搜索词导出。

## 相关插件

- `storyboard` —— 把脚本拆成分镜表（每个分镜的 sound 提示可 → bgm-suggester）
- `tongyi-image` / `seedance-video` —— 出画面，本插件出配乐，搭起来就是完整短视频
