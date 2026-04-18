# WeCom Document & Collaboration MCP Server

The WeCom MCP service provides an all-in-one collaboration toolkit covering document creation/editing, smart tables, to-dos, calendars, meetings, messaging, and more. It connects via the standard MCP protocol, enabling AI Agents to directly operate WeCom documents and collaboration resources.

## Prerequisites

A WeCom MCP Server instance must be deployed. Recommended solution:

- **Crain99/wecom-mcp-server** (39 tools, covering 7 business domains)
  https://github.com/Crain99/wecom-mcp-server

Refer to the repository's README for deployment steps.

## Configuration

1. Deploy wecom-mcp-server and note the service URL (default: `http://localhost:8787/mcp`)
2. Set the environment variable in your `.env` file:
   ```
   WECOM_MCP_SERVER_URL=http://localhost:8787/mcp
   ```
3. After restarting, connect using `connect_mcp_server("wecom-doc")`

## Available Tools

Tools are auto-discovered upon connection. The following is a reference for the main tools:

### Documents

| Tool | Function |
|------|----------|
| create_doc | Create a document or smart table (doc_type=3 for document, 10 for smart table) |
| edit_doc_content | Edit document content (Markdown format) |

### Smart Tables

| Tool | Function |
|------|----------|
| smartsheet_get_sheet | Query basic worksheet information |
| smartsheet_add_sheet | Add a sub-sheet |
| smartsheet_get_fields | Get the list of fields |
| smartsheet_add_fields | Add fields |
| smartsheet_update_fields | Update fields |
| smartsheet_add_records | Add records |
| smartsheet_get_records | Query records |
| smartsheet_update_records | Update records |
| smartsheet_delete_records | Delete records |

### Messaging

| Tool | Function |
|------|----------|
| send_message | Send text/Markdown messages, supports @mentioning members |
| send_file | Send a file |
| send_image | Send an image |

### To-Dos

| Tool | Function |
|------|----------|
| create_todo | Create a to-do item |
| get_todo_list | Query the to-do list |
| update_todo | Update the status of a to-do item |

### Calendar

| Tool | Function |
|------|----------|
| create_schedule | Create a calendar event |
| get_schedule | Query a calendar event |

### Meetings

| Tool | Function |
|------|----------|
| create_meeting | Create a meeting |

### Directory

| Tool | Function |
|------|----------|
| get_user_info | Query member information |

## Notes

- Actual available tools are determined by what the MCP Server instance's `tools/list` returns
- Document operations require the bot to have the appropriate permissions granted in the WeCom admin console, along with member authorization (valid for 7 days)
- After creating a smart table, a default sub-sheet and fields are included — it is recommended to run `smartsheet_get_fields` first to retrieve the default fields before renaming them
