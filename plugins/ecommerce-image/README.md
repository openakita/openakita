# 电商素材小助理插件 (ecommerce-image)

AI 电商内容创作 — 商品主图、详情页、活动海报、短视频，19 个功能覆盖电商全链路素材需求。

## 插件信息

| 字段 | 值 |
|------|-----|
| ID | `ecommerce-image` |
| 版本 | 0.1.0 |
| 类型 | Python 插件 |
| 入口 | `plugin.py` |
| 最低要求 | OpenAkita >= 1.27.0 |

## 目录结构

```
plugins/ecommerce-image/
├── plugin.json              # 插件元数据、权限、UI 配置
├── plugin.py                # 入口：路由注册、工具注册、任务轮询、API 端点
├── ecom_client.py           # DashScope 图像生成 HTTP 客户端
├── ecom_video_client.py     # Volcengine Ark 视频生成 HTTP 客户端
├── ecom_execution.py        # 执行策略 + Pipeline 步骤处理器
├── ecom_features_config.py  # 19 个功能的声明（提示词模板、参数、执行配置）
├── ecom_feature_registry.py # 功能注册中心，按模块分组
├── ecom_models.py           # 可用模型注册表（图像模型 + 视频模型）
├── ecom_prompt_optimizer.py # AI 提示词优化逻辑、系统提示词、视频模板
├── ecom_task_manager.py     # SQLite 任务持久化（创建、更新、查询、父子任务）
├── ecom_mock.py             # 演示模式（无 API Key 时的占位数据）
└── ui/
    └── dist/
        └── index.html       # 单文件 React 前端 UI
```

## 功能模块

插件包含 **4 个模块、19 个功能**：

### 视频生成 (video) — 4 个功能

| ID | 名称 | 执行模式 | 说明 |
|----|------|----------|------|
| `video_hot_replicate` | 爆款复刻 | agent | 参考爆款视频风格生成同类型短视频 |
| `video_storyboard` | 视频分镜生成器 | pipeline | 输入故事脚本，AI 拆解为多段分镜 |
| `video_ad_oneclick` | 商品广告一键成片 | agent | 上传商品图+卖点，一键生成广告视频 |
| `video_character_replace` | 角色替换 | agent | 替换视频中的角色形象 |

### 图像生成 (image) — 7 个功能

| ID | 名称 | 执行模式 | 说明 |
|----|------|----------|------|
| `image_main_replicate` | 主图复刻 | agent | 参考竞品主图风格生成同类型主图 |
| `image_batch_edit` | 批量改图 | batch | 批量修改多张图片 |
| `image_batch_replace` | 批量替换 | batch | 批量替换图片中的元素 |
| `image_batch_gen` | 批量生图 | batch | 批量生成多张图片 |
| `image_main_suite` | 主图套图 | agent | 生成一组风格统一的商品主图（父子任务） |
| `image_translate` | 图片翻译 | pipeline | 将图片中的文字翻译为目标语言 |
| `image_main_gen` | 主图生成 | prompt_template | 根据文字描述直接生成商品主图 |

### 电商详情图 (detail) — 4 个功能

| ID | 名称 | 执行模式 | 说明 |
|----|------|----------|------|
| `detail_replicate` | 详情图复刻 | agent | 参考竞品详情图风格生成 |
| `detail_suite` | 详情图套图 | agent | 生成一套完整详情页图（父子任务） |
| `detail_long` | 详情图长图 | pipeline | 生成竖版分段长图并自动拼接 |
| `detail_new_product` | 新品发布 | agent | 生成新品发布全套素材（父子任务） |

### 活动海报 (poster) — 4 个功能

| ID | 名称 | 执行模式 | 说明 |
|----|------|----------|------|
| `poster_private_domain` | 私域运营 | prompt_template | 微信群/朋友圈运营海报 |
| `poster_product` | 产品营销 | agent | 产品营销推广海报 |
| `poster_holiday` | 节日海报 | prompt_template | 节日主题海报 |
| `poster_campaign` | 活动宣传 | agent | 促销活动宣传海报 |

## 执行模式

| 模式 | 说明 |
|------|------|
| `prompt_template` | 模板填充 → 单次 API 调用。最简单的模式 |
| `agent` | 用户提示词（或 AI 优化后的 JSON）→ API 调用。支持父子任务（套图/批量） |
| `pipeline` | 多步骤顺序执行。如：分镜解析 → 视频生成，或翻译 → 图像生成 → 拼接 |
| `batch` | 对多张上传图片执行相同操作，每张图创建一个子任务 |

## API 提供商

| 提供商 | 用途 | 模型示例 |
|--------|------|----------|
| DashScope (阿里通义) | 图像生成/编辑 | wan2.7-image-pro, wan2.7-image, wan2.6-image, qwen-image-2.0-pro |
| Volcengine Ark (豆包) | 视频生成 | seedance-2-0, doubao-seedance-1-0-lite-i2v |

## 安装与配置

### 启用插件

插件位于 `plugins/ecommerce-image/`，OpenAkita 启动时自动加载。确保在插件设置中授予以下权限：
- `routes.register` — 注册 API 路由（功能列表、任务管理等）
- `brain.access` — 调用 LLM 进行提示词优化和分镜解析

### 配置 API Key

在插件 UI 的「设置」页面中配置：

- **DashScope API Key** — 用于图像生成。[获取地址](https://dashscope.console.aliyun.com/)
- **Ark API Key** — 用于视频生成。[获取地址](https://console.volcengine.com/ark)

未配置 Key 时，插件会进入演示模式，使用占位图/示例视频。

### 设置参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 默认图像模型 | 功能未指定时使用的图像模型 | 功能预设 |
| 默认视频模型 | 功能未指定时使用的视频模型 | 功能预设 |
| 图片输出目录 | 生成图片的本地保存路径 | `data/plugin_data/ecommerce-image/data/images` |
| 视频输出目录 | 生成视频的本地保存路径 | `data/plugin_data/ecommerce-image/data/videos` |
| 自动下载 | 生成完成后自动下载到本地 | 开启 |
| 轮询间隔 | 图像/视频任务状态检查间隔 | 图像 10s / 视频 15s |

## 工具调用

插件注册 4 个工具，可通过 Agent 对话或 API 调用：

### ecom_image_create

创建图像生成任务。

```json
{
  "feature_id": "image_main_gen",
  "product_name": "智能手表",
  "prompt": "现代简约风格，白色背景，产品居中",
  "style": "realistic"
}
```

### ecom_video_create

创建视频生成任务。

```json
{
  "feature_id": "video_ad_oneclick",
  "product_name": "无线耳机",
  "selling_points": "主动降噪、30小时续航",
  "prompt": "科技感产品展示",
  "duration": 5,
  "ratio": "9:16"
}
```

### ecom_task_status

查询任务状态。参数：`task_id`。

### ecom_task_list

列出最近任务。可选参数：`module`（video/image/detail/poster）、`limit`。

## 提示词优化

每个功能的输入框下方有「AI 优化」按钮：
1. 用户输入简单描述
2. 点击「AI 优化」
3. AI 根据功能类型（主图/套图/海报/视频）自动补充专业约束
4. 用户确认后点击「开始生成」，直接使用优化后的提示词

优化不会在提交时再次执行，点击生成就是最终提示词。

## 开发说明

- 前端为单文件内联 React（`ui/dist/index.html`），使用 Babel standalone 运行时编译 JSX
- 后端所有模块通过 `plugin.py` 加载，使用相对导入
- 任务数据存储在 `data/plugin_data/ecommerce-image/data/ecommerce.db`（SQLite）
- 添加新功能只需在 `ecom_features_config.py` 中声明，注册表自动识别
