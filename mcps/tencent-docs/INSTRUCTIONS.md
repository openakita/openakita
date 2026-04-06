# 腾讯文档 MCP Server

腾讯文档 MCP 提供了一套完整的在线文档操作工具，支持创建、查询、编辑多种类型的在线文档。

## 配置

1. 访问 https://docs.qq.com/open/auth/mcp.html 获取个人 Token
2. 在 `.env` 文件中设置环境变量：
   ```
   TENCENT_DOCS_TOKEN=你的Token值
   ```
3. 重启后使用 `connect_mcp_server("tencent-docs")` 连接

## 可用工具

连接后工具会自动发现，以下为主要工具参考：

### 文档创建

| 工具 | 功能 |
|------|------|
| create_smartcanvas_by_markdown | 创建智能文档（首选） |
| create_excel_by_markdown | 创建 Excel 表格 |
| create_slide_by_markdown | 创建幻灯片 |
| create_mind_by_markdown | 创建思维导图 |
| create_flowchart_by_mermaid | 创建流程图 |
| create_word_by_markdown | 创建 Word 文档 |

### 文档管理

| 工具 | 功能 |
|------|------|
| query_space_node | 查询空间节点 |
| create_space_node | 创建空间节点（文件夹） |
| delete_space_node | 删除空间节点 |
| search_space_file | 搜索空间文件 |
| get_content | 获取文档内容 |
| batch_update_sheet_range | 批量更新表格区域 |

### 智能文档操作 (smartcanvas.*)

对已有智能文档进行增删改查，包括页面、文本、标题、待办事项等元素操作。

### 智能表格操作 (smartsheet.*)

对智能表格进行工作表/视图/字段/记录操作，支持多视图、字段管理、看板等高级功能。

## 文档类型选择

- 通用文档内容 → `create_smartcanvas_by_markdown`（首选）
- 数据表格 → `create_excel_by_markdown`
- 演示文稿 → `create_slide_by_markdown`
- 知识图谱/大纲 → `create_mind_by_markdown`
- 流程图/架构图 → `create_flowchart_by_mermaid`
- 结构化数据管理 → `smartsheet.*` 系列工具

## 注意事项

- Header 的 key 必须使用 `Authorization`，不能使用其他名称
- 实际可用工具以调用 `tools/list` 接口返回结果为准
- 错误码 400006 表示 Token 鉴权失败，请检查 Token 配置
- 错误码 400007 表示 VIP 权限不足
