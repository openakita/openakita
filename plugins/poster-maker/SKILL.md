---
name: poster-maker
description: 用模板 + 文案 + 一张配图，生成 PNG 海报。可选调用 image-edit AI 润色背景。
env_any: []
---

# Poster Maker

## 是什么 / What

零设计基础也能用的海报生成器：选模板 → 填文案 → 上传配图 → 出图。所有渲染本地完成（Pillow），不依赖外部服务。

## 何时用 / When

- 用户要做朋友圈 / 小红书 / 公众号海报
- 用户要做活动 / 课程 / 产品宣传图
- 用户要快速做一张视频封面
- **不要用于**: 需要复杂图层 / 自定义字体 / 复杂版式（建议接专业工具）

## 工具 / Tools

- `poster_maker_templates()` — 列出所有模板
- `poster_maker_create(template_id, text_values, background_image_path?, ai_enhance_prompt?)`
- `poster_maker_status(task_id)` / `poster_maker_list()` / `poster_maker_cancel(task_id)`

## 模板

| id              | 尺寸      | 用途              |
|-----------------|-----------|-------------------|
| social-square   | 1080×1080 | 朋友圈 / 小红书   |
| vertical-poster | 900×1200  | 活动海报          |
| banner-wide     | 1920×1080 | 网页 banner / 封面 |

## 复用关系

- 文字渲染：本插件自管（Pillow）
- AI 背景润色：可选调用 `image-edit` 的 provider（设了 OPENAI_API_KEY 才生效）

## Quality Gates

| Gate | 检查内容              | 通过条件                                                |
|------|-----------------------|---------------------------------------------------------|
| G1   | template_id 存在     | `template_id in TEMPLATES`                              |
| G2   | output_path 存在      | `Path(result["output_path"]).is_file()`                 |
| G3   | 错误用 ErrorCoach     | `RenderedError.pattern_id != "_fallback"`               |

## 已知坑 / Known Pitfalls

- **没装中文字体 → 字符变方块**：Linux 容器需安装 NotoSansCJK 或 wqy-microhei
- **AI 润色费钱**：`ai_enhance_prompt` 非空才调用，留空则纯本地渲染（0 成本）
- **配图比例不对会被裁切**：使用 cover 拉伸，超出部分裁掉，不会变形
