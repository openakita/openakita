# shorts-batch

批量生成短视频。一次提交多条 brief，每条先扩成分镜计划、过 `slideshow_risk` 启发式打分，再交给可插拔的下游渲染器。

## 设计原则

- **风险先行（D2.1）**：`evaluate_slideshow_risk` 在花配额前就告诉用户"这一批有 N 条会像静态幻灯片"。
- **可插拔规划器/渲染器**：默认是确定性 stub；生产环境通过 `Plugin.set_planner` / `Plugin.set_renderer` 注入真实 LLM 规划器和 seedance-video / ppt-to-video 渲染器。
- **D2.10 校验包络**：失败数 > 0、高风险占多数、`bytes==0` 都会变成黄旗，前端可直接渲染绿/黄/红徽章。
- **零外部依赖**：仅依赖 SDK 与 FastAPI，无 ffmpeg / 模型 / API key。

## 安装

无额外依赖。需要 OpenAkita >= 1.27.0 / SDK >= 0.4.0。

## HTTP 用法

```bash
# 1) 风险预览（不渲染、不花钱）
curl -X POST http://localhost:8000/plugins/shorts-batch/preview-risk \
  -H "Content-Type: application/json" \
  -d '{"briefs":[{"topic":"秋季穿搭","duration_sec":15.0}]}'

# 2) 提交批次（risk_block_threshold=high 表示高风险跳过不渲染）
curl -X POST http://localhost:8000/plugins/shorts-batch/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "briefs":[
      {"topic":"秋季穿搭","duration_sec":15.0},
      {"topic":"咖啡冲煮技巧","duration_sec":20.0}
    ],
    "risk_block_threshold":"high"
  }'

# 3) 查询任务
curl http://localhost:8000/plugins/shorts-batch/tasks/<task_id>
```

## Brain Tools

```text
shorts_batch_preview_risk → 仅打分，不渲染
shorts_batch_create        → 提交批次（异步）
shorts_batch_status        → 查询状态
shorts_batch_list          → 列出最近 20 个
shorts_batch_cancel        → 取消运行中的批次
```

## 配置项

| key | 默认 |
|-----|------|
| `default_aspect` | `9:16` |
| `default_duration_sec` | `15.0` |
| `default_style` | `vlog` |
| `default_language` | `zh-CN` |
| `default_min_shots` | `3` |
| `default_max_shots` | `12` |
| `default_risk_block_threshold` | `""`（不阻塞） |

## 测试

```bash
.\.venv\Scripts\python.exe -m pytest plugins/shorts-batch/tests -q
```

51 个测试覆盖：brief 校验、`slideshow_risk` 接入、stub planner、render dispatch、风险阻塞、批量聚合、D2.10 校验、HTTP 路由、brain tool。
