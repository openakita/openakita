# Tencent Docs MCP Server

The Tencent Docs MCP provides a comprehensive set of online document operation tools, supporting the creation, querying, and editing of various types of online documents.

## Configuration

1. Visit https://docs.qq.com/open/auth/mcp.html to obtain your personal Token
2. Set the environment variable in your `.env` file:
   ```
   TENCENT_DOCS_TOKEN=your_token_value
   ```
3. After restarting, connect using `connect_mcp_server("tencent-docs")`

## Available Tools

Tools are auto-discovered upon connection. The following is a reference for the main tools:

### Document Creation

| Tool | Function |
|------|----------|
| create_smartcanvas_by_markdown | Create a smart document (preferred) |
| create_excel_by_markdown | Create an Excel spreadsheet |
| create_slide_by_markdown | Create a presentation |
| create_mind_by_markdown | Create a mind map |
| create_flowchart_by_mermaid | Create a flowchart |
| create_word_by_markdown | Create a Word document |

### Document Management

| Tool | Function |
|------|----------|
| query_space_node | Query space nodes |
| create_space_node | Create a space node (folder) |
| delete_space_node | Delete a space node |
| search_space_file | Search for files in a space |
| get_content | Retrieve document content |
| batch_update_sheet_range | Batch update spreadsheet ranges |

### Smart Document Operations (smartcanvas.*)

Perform CRUD operations on existing smart documents, including pages, text, headings, to-do items, and other elements.

### Smart Spreadsheet Operations (smartsheet.*)

Perform worksheet/view/field/record operations on smart spreadsheets, with support for multi-view, field management, kanban boards, and other advanced features.

## Choosing a Document Type

- General document content → `create_smartcanvas_by_markdown` (preferred)
- Data tables → `create_excel_by_markdown`
- Presentations → `create_slide_by_markdown`
- Knowledge graphs / outlines → `create_mind_by_markdown`
- Flowcharts / architecture diagrams → `create_flowchart_by_mermaid`
- Structured data management → `smartsheet.*` tool series

## Notes

- The header key must be `Authorization`; other names are not accepted
- Actual available tools are determined by what the `tools/list` endpoint returns
- Error code 400006 indicates Token authentication failure — check your Token configuration
- Error code 400007 indicates insufficient VIP permissions
