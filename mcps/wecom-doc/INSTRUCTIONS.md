# 企业微信文档与协作 MCP Server

企业微信 MCP 服务提供文档创建/编辑、智能表格、待办、日程、会议、消息等一站式协作工具，
通过标准 MCP 协议接入，支持 AI Agent 直接操作企业微信中的文档和协作资源。

## 前置条件

需要部署一个企业微信 MCP Server 实例。推荐方案：

- **Crain99/wecom-mcp-server** (39 个工具，覆盖 7 大业务域)
  https://github.com/Crain99/wecom-mcp-server

部署步骤请参考对应仓库的 README。

## 配置

1. 部署 wecom-mcp-server 并记录服务地址（默认 `http://localhost:8787/mcp`）
2. 在 `.env` 文件中设置环境变量：
   ```
   WECOM_MCP_SERVER_URL=http://localhost:8787/mcp
   ```
3. 重启后使用 `connect_mcp_server("wecom-doc")` 连接

## 可用工具

连接后工具会自动发现，以下为主要工具参考：

### 文档

| 工具 | 功能 |
|------|------|
| create_doc | 新建文档或智能表格（doc_type=3 文档，10 智能表格） |
| edit_doc_content | 编辑文档内容（Markdown 格式） |

### 智能表格

| 工具 | 功能 |
|------|------|
| smartsheet_get_sheet | 查询工作表基本信息 |
| smartsheet_add_sheet | 添加子表 |
| smartsheet_get_fields | 获取字段列表 |
| smartsheet_add_fields | 添加字段 |
| smartsheet_update_fields | 更新字段 |
| smartsheet_add_records | 添加记录 |
| smartsheet_get_records | 查询记录 |
| smartsheet_update_records | 更新记录 |
| smartsheet_delete_records | 删除记录 |

### 消息

| 工具 | 功能 |
|------|------|
| send_message | 发送文本/Markdown 消息，支持 @成员 |
| send_file | 发送文件 |
| send_image | 发送图片 |

### 待办

| 工具 | 功能 |
|------|------|
| create_todo | 创建待办 |
| get_todo_list | 查询待办列表 |
| update_todo | 更新待办状态 |

### 日程

| 工具 | 功能 |
|------|------|
| create_schedule | 创建日程 |
| get_schedule | 查询日程 |

### 会议

| 工具 | 功能 |
|------|------|
| create_meeting | 创建会议 |

### 通讯录

| 工具 | 功能 |
|------|------|
| get_user_info | 查询成员信息 |

## 注意事项

- 实际可用工具以 MCP Server 实例的 `tools/list` 返回为准
- 文档操作需要机器人在企业微信后台获得相应权限并由成员授权（有效期 7 天）
- 创建智能表格后会自带默认子表和字段，建议先 `smartsheet_get_fields` 获取默认字段再重命名
