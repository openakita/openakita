# SQLite Memory Plugin

基于 Python 标准库 `sqlite3` 的轻量级记忆后端，零外部依赖。

## 功能

- 存储 / 搜索 / 删除记忆
- 基于 LIKE 的全文搜索
- 会话记录（record_turn）
- 上下文注入（get_injection_context）

## 配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| db_path | SQLite 数据库文件路径 | 插件数据目录下 `memory.db` |

