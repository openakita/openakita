#!/usr/bin/env python3
"""批量生成内置技能的 agents/openai.yaml i18n 翻译。

运行方式: python scripts/generate_skill_i18n.py

同时兼容旧的 .openakita-i18n.json（如果存在则迁移到 agents/openai.yaml）。
"""

from pathlib import Path

import yaml

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"

# {skill_name: {"name": 中文名, "description": 中文描述}}
TRANSLATIONS: dict[str, dict[str, str]] = {
    # ── 外部技能 ──
    "algorithmic-art": {
        "name": "算法艺术",
        "description": "使用 p5.js 创建算法艺术作品，支持种子随机数和交互式参数探索。",
    },
    "brand-guidelines": {
        "name": "品牌视觉指南",
        "description": "应用 Anthropic 官方品牌色和排版规范，让内容呈现统一的品牌视觉风格。",
    },
    "canvas-design": {
        "name": "画布设计",
        "description": "使用设计哲学创建精美的 PNG 和 PDF 视觉艺术作品，如海报、传单等。",
    },
    "changelog-generator": {
        "name": "变更日志生成器",
        "description": "从 Git 提交历史自动生成面向用户的变更日志，分类整理技术提交为可读的更新说明。",
    },
    "code-review": {
        "name": "代码审查",
        "description": "审查代码变更，支持本地修改（暂存区或工作区）和远程 Pull Request，关注正确性和质量。",
    },
    "content-research-writer": {
        "name": "内容研究写手",
        "description": "辅助撰写高质量内容，包括调研、添加引用、优化开头、迭代大纲和实时反馈。",
    },
    "datetime-tool": {
        "name": "日期时间工具",
        "description": "获取当前时间、格式化日期、计算日期差值、时区转换。",
    },
    "doc-coauthoring": {
        "name": "文档协作",
        "description": "引导用户完成结构化的文档协作流程，适用于撰写文档、提案、技术规格和决策文档。",
    },
    "docx": {
        "name": "Word 文档处理",
        "description": "创建、读取、编辑和处理 Word 文档（.docx 文件）。",
    },
    "file-manager": {
        "name": "文件管理器",
        "description": "文件和目录管理工具。创建、读取、写入、删除、移动、复制文件。",
    },
    "frontend-design": {
        "name": "前端界面设计",
        "description": "创建独特的、生产级的高质量前端界面，适用于构建网页组件、页面和应用。",
    },
    "github-automation": {
        "name": "GitHub 自动化",
        "description": "通过 MCP 自动化 GitHub 仓库、Issue、Pull Request、分支、CI/CD 和权限管理。",
    },
    "gmail-automation": {
        "name": "Gmail 自动化",
        "description": "通过 MCP 自动化 Gmail 任务：发送/回复邮件、搜索、标签、草稿和附件管理。",
    },
    "google-calendar-automation": {
        "name": "Google 日历自动化",
        "description": "通过 MCP 自动化 Google 日历事件管理：创建事件、查找空闲时段、管理参会者。",
    },
    "image-understander": {
        "name": "图像理解 (GPT-4V)",
        "description": "使用 GPT-4 Vision 分析图片，支持图片描述、OCR 文字提取、目标识别和视觉问答。",
    },
    "image-understanding": {
        "name": "图像理解 (通义千问)",
        "description": "使用通义千问视觉模型分析图片，支持详细描述、OCR 文字提取、目标识别和视觉问答。",
    },
    "internal-comms": {
        "name": "内部通讯模板",
        "description": "辅助撰写各类内部通讯，使用公司常用的格式和模板。",
    },
    "mcp-builder": {
        "name": "MCP 服务器构建",
        "description": "创建高质量 MCP（Model Context Protocol）服务器的指南，让大模型能与外部服务交互。",
    },
    "mcp-installer": {
        "name": "MCP 安装器",
        "description": "安装、配置和添加 MCP 服务器到 OpenAkita 系统，支持 npm/pip 包和远程服务。",
    },
    "moltbook": {
        "name": "Moltbook 社交",
        "description": "AI Agent 社交网络，支持发帖、评论、点赞和创建社区。",
    },
    "pdf": {
        "name": "PDF 文档处理",
        "description": "处理 PDF 文件，包括读取/提取文本和表格、合并、拆分、转换和填写表单。",
    },
    "pptx": {
        "name": "PPT 演示文稿",
        "description": "创建和处理 PowerPoint 演示文稿（.pptx），包括幻灯片设计、内容编辑和模板应用。",
    },
    "skill-creator": {
        "name": "技能创建器",
        "description": "创建和改进 OpenAkita 技能。为重复性任务创建新技能、改进现有技能、或将临时脚本封装为可复用技能。",
    },
    "slack-gif-creator": {
        "name": "Slack GIF 制作",
        "description": "创建适用于 Slack 的动画 GIF，提供约束条件、验证工具和动画概念。",
    },
    "theme-factory": {
        "name": "主题工厂",
        "description": "为各种内容（幻灯片、文档、报告、网页等）应用主题样式，内含 10 个预设主题。",
    },
    "video-downloader": {
        "name": "视频下载器",
        "description": "下载 YouTube 视频，支持自定义画质和格式选项。",
    },
    "web-artifacts-builder": {
        "name": "Web 组件构建器",
        "description": "使用 React、Tailwind CSS、shadcn/ui 等前端技术创建精美的多组件 HTML 页面。",
    },
    "webapp-testing": {
        "name": "Web 应用测试",
        "description": "使用 Playwright 与本地 Web 应用交互和测试，验证前端功能、调试 UI 行为。",
    },
    "xlsx": {
        "name": "Excel 表格处理",
        "description": "处理 Excel 表格文件（.xlsx），包括打开、读取、编辑、修复和创建电子表格。",
    },
    # ── 系统技能 ──
    "add-memory": {
        "name": "添加记忆",
        "description": "将重要信息记录到长期记忆中，用于学习用户偏好、保存成功模式和记录错误教训。",
    },
    "browser-click": {
        "name": "浏览器点击",
        "description": "通过 CSS 选择器或文本内容点击网页元素，如按钮、链接或选项。",
    },
    "browser-get-content": {
        "name": "获取网页内容",
        "description": "提取当前网页的内容和元素文本，用于读取页面信息、获取数据或验证内容。",
    },
    "browser-list-tabs": {
        "name": "列出标签页",
        "description": "列出所有打开的浏览器标签页，包括序号、URL 和标题。",
    },
    "browser-navigate": {
        "name": "浏览器导航",
        "description": "导航浏览器到指定 URL，打开网页或开始网页自动化操作。",
    },
    "browser-new-tab": {
        "name": "新建标签页",
        "description": "在新标签页中打开 URL，保持当前页面不变，支持多任务并行。",
    },
    "browser-open": {
        "name": "打开浏览器",
        "description": "启动浏览器或检查其状态，返回当前状态信息（是否打开、URL、标题、标签数）。",
    },
    "browser-screenshot": {
        "name": "网页截图",
        "description": "捕获浏览器页面截图（仅网页内容），用于记录页面状态或调试问题。",
    },
    "browser-status": {
        "name": "浏览器状态",
        "description": "检查浏览器当前状态，包括打开状态、当前 URL、页面标题和标签数量。",
    },
    "browser-switch-tab": {
        "name": "切换标签页",
        "description": "按索引切换到指定浏览器标签页，用于在不同页面间切换。",
    },
    "browser-task": {
        "name": "浏览器任务",
        "description": "智能浏览器任务——描述任务目标，自动完成操作（推荐优先使用）。",
    },
    "browser-type": {
        "name": "网页输入",
        "description": "在网页输入框中输入文本，用于填写表单、输入搜索词或录入数据。",
    },
    "call-mcp-tool": {
        "name": "调用 MCP 工具",
        "description": "调用 MCP 服务器工具以获取扩展功能，需查看系统提示中的可用服务器和工具列表。",
    },
    "cancel-scheduled-task": {
        "name": "取消定时任务",
        "description": "永久删除指定的定时任务。",
    },
    "complete-todo": {
        "name": "完成计划",
        "description": "标记计划为已完成并生成执行总结报告，在所有步骤完成后调用。",
    },
    "create-todo": {
        "name": "创建计划",
        "description": "为多步骤任务创建执行计划，在需要 2 个以上工具调用时必须首先创建计划。",
    },
    "deliver-artifacts": {
        "name": "发送文件",
        "description": "通过 IM 网关发送文件、图片或语音到当前聊天会话，返回发送回执。",
    },
    "desktop-click": {
        "name": "桌面点击",
        "description": "点击桌面元素或坐标，用于点击按钮/图标、选择菜单项或与桌面 UI 交互。",
    },
    "desktop-find-element": {
        "name": "查找桌面元素",
        "description": "使用 UI 自动化或视觉识别查找桌面 UI 元素，定位按钮、菜单和图标。",
    },
    "desktop-hotkey": {
        "name": "键盘快捷键",
        "description": "执行键盘快捷键，如复制粘贴、保存文件、关闭窗口、撤销重做等。",
    },
    "desktop-inspect": {
        "name": "桌面 UI 检查",
        "description": "检查窗口 UI 元素树结构，用于调试 UI 自动化问题和理解界面布局。",
    },
    "desktop-screenshot": {
        "name": "桌面截图",
        "description": "捕获 Windows 桌面截图并自动保存文件，用于记录桌面状态和操作结果。",
    },
    "desktop-scroll": {
        "name": "桌面滚动",
        "description": "滚动鼠标滚轮，用于翻页、浏览长列表或配合 Ctrl 键缩放。",
    },
    "desktop-type": {
        "name": "桌面输入",
        "description": "在桌面应用当前光标位置输入文本，用于填写对话框、输入框或文本编辑器。",
    },
    "desktop-wait": {
        "name": "等待元素",
        "description": "等待 UI 元素或窗口出现，用于等待对话框打开、加载完成或同步应用状态。",
    },
    "desktop-window": {
        "name": "窗口管理",
        "description": "管理桌面窗口操作：列出窗口、切换窗口、最小化/最大化/还原和关闭窗口。",
    },
    "enable-thinking": {
        "name": "深度思考开关",
        "description": "控制深度思考模式的开启和关闭，对简单任务可临时关闭以加速响应。",
    },
    "find-skills": {
        "name": "发现技能",
        "description": "帮助用户发现和安装适合的技能，当用户问「怎么做某事」或「有没有某个功能」时使用。",
    },
    "generate-image": {
        "name": "文生图",
        "description": "根据提示词生成图片并保存为本地 PNG 文件，需要配置 DASHSCOPE_API_KEY。",
    },
    "get-chat-history": {
        "name": "获取聊天记录",
        "description": "获取当前聊天历史，包括用户消息、回复和系统通知。",
    },
    "get-image-file": {
        "name": "获取图片文件",
        "description": "获取用户发送的图片的本地文件路径，用于处理或分析图片内容。",
    },
    "get-mcp-instructions": {
        "name": "MCP 使用说明",
        "description": "获取 MCP 服务器的详细使用说明（INSTRUCTIONS.md），了解服务器完整功能。",
    },
    "get-memory-stats": {
        "name": "记忆统计",
        "description": "获取记忆系统统计信息，包括记忆总数和按类型的分布。",
    },
    "get-todo-status": {
        "name": "查看计划进度",
        "description": "获取当前计划的执行状态，显示所有步骤及其完成情况。",
    },
    "get-session-logs": {
        "name": "会话日志",
        "description": "获取当前会话的系统日志，用于排查命令执行失败、错误或理解操作结果。",
    },
    "get-skill-info": {
        "name": "技能详情",
        "description": "获取技能的详细使用说明和指南，了解技能的功能和使用方法。",
    },
    "get-skill-reference": {
        "name": "技能参考文档",
        "description": "获取技能的参考文档，提供详细技术文档、示例和高级用法说明。",
    },
    "get-tool-info": {
        "name": "工具详情",
        "description": "获取系统工具的详细参数定义，了解工具的使用方法和参数说明。",
    },
    "get-user-profile": {
        "name": "获取用户画像",
        "description": "获取当前用户的画像摘要，了解用户偏好和上下文信息。",
    },
    "get-voice-file": {
        "name": "获取语音文件",
        "description": "获取用户发送的语音消息的本地文件路径，用于处理语音内容。",
    },
    "install-skill": {
        "name": "安装技能",
        "description": "从 URL 或 Git 仓库安装技能到本地 skills/ 目录，支持 GitHub 简写格式。",
    },
    "list-directory": {
        "name": "列出目录",
        "description": "列出目录内容，包括文件和子目录，用于浏览目录结构和查找文件。",
    },
    "list-mcp-servers": {
        "name": "MCP 服务器列表",
        "description": "列出所有已配置的 MCP 服务器及其连接状态。",
    },
    "list-scheduled-tasks": {
        "name": "定时任务列表",
        "description": "列出所有定时任务，包括 ID、名称、类型、状态和下次执行时间。",
    },
    "list-skills": {
        "name": "列出技能",
        "description": "列出所有已安装的技能，查看可用技能或查找适合任务的技能。",
    },
    "load-skill": {
        "name": "加载技能",
        "description": "从 skills/ 目录加载新创建的技能，使其立即可用。",
    },
    "news-search": {
        "name": "新闻搜索",
        "description": "使用 DuckDuckGo 搜索新闻，查找最新新闻、时事动态或突发事件。",
    },
    "read-file": {
        "name": "读取文件",
        "description": "读取文本文件内容，用于查看文件内容、分析代码或获取配置信息。",
    },
    "reload-skill": {
        "name": "重新加载技能",
        "description": "重新加载已修改的技能，使 SKILL.md 或脚本的更改生效。",
    },
    "run-shell": {
        "name": "执行命令",
        "description": "执行 Shell 命令，用于系统操作、创建目录、运行脚本和安装软件包。",
    },
    "run-skill-script": {
        "name": "运行技能脚本",
        "description": "执行技能的脚本文件，运行技能功能或处理数据。",
    },
    "schedule-task": {
        "name": "创建定时任务",
        "description": "创建定时任务或提醒，必须实际调用此工具才能创建任务。",
    },
    "search-memory": {
        "name": "搜索记忆",
        "description": "按关键词搜索相关记忆，回忆过往信息、查找用户偏好或检查已学习的模式。",
    },
    "send-sticker": {
        "name": "发送表情包",
        "description": "搜索并发送表情包图片，在闲聊、问候、鼓励等场景下让对话更生动有趣。",
    },
    "set-task-timeout": {
        "name": "设置任务超时",
        "description": "调整当前任务的超时策略，适用于预期耗时较长的任务。",
    },
    "skip-profile-question": {
        "name": "跳过画像问题",
        "description": "当用户明确拒绝回答画像问题时，跳过当前问题。",
    },
    "switch-persona": {
        "name": "切换人格",
        "description": "切换 Agent 人格预设角色，支持默认助手、商务助理、技术专家等多种预设。",
    },
    "toggle-proactive": {
        "name": "活人感模式",
        "description": "开关活人感模式，开启后 Agent 会主动发送问候、任务提醒和关键回顾。",
    },
    "trigger-scheduled-task": {
        "name": "立即触发任务",
        "description": "立即执行定时任务，无需等待预定时间，用于测试或提前运行。",
    },
    "update-todo-step": {
        "name": "更新计划步骤",
        "description": "更新计划中某个步骤的状态，每完成一步后必须调用以跟踪进度。",
    },
    "update-scheduled-task": {
        "name": "更新定时任务",
        "description": "修改定时任务设置（不删除），可修改通知开关和启用状态。",
    },
    "update-user-profile": {
        "name": "更新用户画像",
        "description": "当用户分享偏好、习惯或工作信息时，更新用户画像。",
    },
    "web-search": {
        "name": "网页搜索",
        "description": "使用 DuckDuckGo 搜索网页，查找最新信息、验证事实或查阅文档。",
    },
    "write-file": {
        "name": "写入文件",
        "description": "将内容写入文件，创建新文件或覆盖已有文件。",
    },
}


def _write_i18n_to_yaml(skill_dir: Path, i18n_data: dict) -> None:
    """将 i18n 数据写入 agents/openai.yaml，合并已有内容。"""
    yaml_file = skill_dir / "agents" / "openai.yaml"
    existing: dict = {}
    if yaml_file.exists():
        try:
            existing = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        except Exception:
            existing = {}

    existing["i18n"] = i18n_data

    yaml_file.parent.mkdir(parents=True, exist_ok=True)
    yaml_file.write_text(
        yaml.dump(existing, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def main():
    created = 0
    skipped = 0
    migrated = 0

    for skill_md in sorted(SKILLS_ROOT.rglob("SKILL.md")):
        skill_dir = skill_md.parent
        skill_name = skill_dir.name

        if skill_name not in TRANSLATIONS:
            print(f"  SKIP (no translation): {skill_name}")
            skipped += 1
            continue

        i18n_data = {"zh": TRANSLATIONS[skill_name]}
        _write_i18n_to_yaml(skill_dir, i18n_data)
        created += 1

        # 清理旧的 .openakita-i18n.json
        legacy = skill_dir / ".openakita-i18n.json"
        if legacy.exists():
            legacy.unlink()
            migrated += 1

    print(f"\nDone: {created} created/updated, {migrated} migrated from JSON, {skipped} skipped")


if __name__ == "__main__":
    main()
