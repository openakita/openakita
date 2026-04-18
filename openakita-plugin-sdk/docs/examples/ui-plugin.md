# 全栈 UI 插件示例 / Full-Stack UI Plugin Example

带有独立前端页面的全栈插件 (Plugin 2.0)。

A full-stack plugin with a dedicated frontend page (Plugin 2.0).

**权限级别 / Permission Level:** Advanced（需要 `routes.register` 等 / requires `routes.register` etc.）

---

## 目录结构 / Directory Structure

```
task-dashboard/
  plugin.json
  plugin.py
  ui/
    dist/
      index.html
  README.md
```

## plugin.json

```json
{
  "id": "task-dashboard",
  "name": "Task Dashboard",
  "version": "1.0.0",
  "type": "python",
  "entry": "plugin.py",
  "description": "A simple task management dashboard with UI",
  "author": "OpenAkita Team",
  "license": "MIT",
  "permissions": [
    "tools.register",
    "routes.register",
    "config.read",
    "config.write",
    "data.own"
  ],
  "requires": {
    "openakita": ">=1.27.0",
    "plugin_api": "~1",
    "plugin_ui_api": "~1",
    "python": ">=3.11"
  },
  "provides": {
    "tools": ["create_task", "list_tasks"],
    "routes": true
  },
  "ui": {
    "entry": "ui/dist/index.html",
    "title": "Task Dashboard",
    "title_i18n": { "en": "Task Dashboard", "zh": "任务面板" },
    "sidebar_group": "apps",
    "permissions": ["notifications", "theme"]
  },
  "category": "productivity",
  "tags": ["tasks", "dashboard", "ui"]
}
```

## plugin.py

```python
import json
from pathlib import Path

from fastapi import APIRouter

from openakita.plugins.api import PluginAPI, PluginBase


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        self._tasks: list[dict] = []
        self._load_tasks()

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "create_task",
                        "description": "Create a new task",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "priority": {
                                    "type": "string",
                                    "enum": ["low", "medium", "high"],
                                },
                            },
                            "required": ["title"],
                        },
                    },
                },
            ],
            self._handle_tool,
        )

    def on_unload(self) -> None:
        self._save_tasks()

    def _data_file(self) -> Path:
        data_dir = self._api.get_data_dir()
        if data_dir is None:
            return Path("tasks.json")
        return data_dir / "tasks.json"

    def _load_tasks(self):
        path = self._data_file()
        if path.exists():
            self._tasks = json.loads(path.read_text())

    def _save_tasks(self):
        self._data_file().write_text(json.dumps(self._tasks, ensure_ascii=False))

    async def _handle_tool(self, tool_name: str, arguments: dict) -> str:
        if tool_name == "create_task":
            task = {
                "id": len(self._tasks) + 1,
                "title": arguments["title"],
                "priority": arguments.get("priority", "medium"),
                "done": False,
            }
            self._tasks.append(task)
            self._save_tasks()
            return f"Task #{task['id']} created: {task['title']}"
        return "Unknown tool"

    def _register_routes(self, router: APIRouter):
        @router.get("/tasks")
        async def list_tasks():
            return {"ok": True, "tasks": self._tasks}

        @router.post("/tasks")
        async def create_task(body: dict):
            task = {
                "id": len(self._tasks) + 1,
                "title": body.get("title", "Untitled"),
                "priority": body.get("priority", "medium"),
                "done": False,
            }
            self._tasks.append(task)
            self._save_tasks()
            return {"ok": True, "task": task}

        @router.put("/tasks/{task_id}/toggle")
        async def toggle_task(task_id: int):
            for t in self._tasks:
                if t["id"] == task_id:
                    t["done"] = not t["done"]
                    self._save_tasks()
                    return {"ok": True, "task": t}
            return {"ok": False, "error": "Not found"}
```

## ui/dist/index.html

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Task Dashboard</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg); color: var(--text); padding: 24px;
    }
    :root, [data-theme="light"] {
      --bg: #fff; --text: #1a1a2e; --border: #e2e8f0;
      --primary: #3b82f6; --muted: #6c757d;
    }
    [data-theme="dark"] {
      --bg: #1a1b2e; --text: #e2e8f0; --border: #334155;
      --primary: #60a5fa; --muted: #94a3b8;
    }
    h1 { font-size: 20px; margin-bottom: 16px; }
    .task { display: flex; align-items: center; padding: 8px 0;
            border-bottom: 1px solid var(--border); }
    .task.done span { text-decoration: line-through; color: var(--muted); }
    .task input[type="checkbox"] { margin-right: 12px; }
    .add-form { display: flex; gap: 8px; margin-bottom: 20px; }
    .add-form input { flex: 1; padding: 8px; border: 1px solid var(--border);
                      border-radius: 6px; background: var(--bg); color: var(--text); }
    .add-form button { padding: 8px 16px; background: var(--primary);
                       color: #fff; border: none; border-radius: 6px; cursor: pointer; }
  </style>
</head>
<body>
  <div id="app"></div>

  <!-- Bridge SDK (inline, see plugin-ui.md for full template) -->
  <script>
    /* ... paste the full SDK template from plugin-ui.md here ... */
  </script>

  <script>
    var tasks = [];

    async function loadTasks() {
      var r = await pluginApi("GET", "/tasks");
      if (r.ok && r.body.tasks) { tasks = r.body.tasks; render(); }
    }

    async function addTask() {
      var input = document.getElementById("new-task");
      if (!input.value.trim()) return;
      var r = await pluginApi("POST", "/tasks", { title: input.value.trim() });
      if (r.ok) {
        showToast("Task created");
        input.value = "";
        loadTasks();
      }
    }

    async function toggleTask(id) {
      await pluginApi("PUT", "/tasks/" + id + "/toggle");
      loadTasks();
    }

    function render() {
      var html = '<h1>Task Dashboard</h1>';
      html += '<div class="add-form">';
      html += '<input id="new-task" placeholder="New task..." onkeydown="if(event.key===\'Enter\')addTask()">';
      html += '<button onclick="addTask()">Add</button></div>';
      tasks.forEach(function(t) {
        html += '<div class="task' + (t.done ? ' done' : '') + '">';
        html += '<input type="checkbox" ' + (t.done ? 'checked' : '') +
                ' onchange="toggleTask(' + t.id + ')">';
        html += '<span>' + t.title + '</span></div>';
      });
      document.getElementById("app").innerHTML = html;
    }

    loadTasks();
  </script>
</body>
</html>
```

## 测试 / Testing

```python
import pytest
from plugin import Plugin
from openakita_plugin_sdk.testing import MockPluginAPI, assert_plugin_loads


def test_loads():
    api = assert_plugin_loads(Plugin())
    assert "create_task" in api.registered_tools
    assert len(api.registered_routes) == 1


@pytest.mark.asyncio
async def test_create_task():
    plugin = Plugin()
    api = MockPluginAPI()
    plugin.on_load(api)
    result = await plugin._handle_tool("create_task", {"title": "Test"})
    assert "Test" in result
    assert len(plugin._tasks) == 1
```

---

## 要点 / Key Takeaways

1. `plugin.json` 中 `ui` 字段声明前端入口，宿主自动挂载静态资源到 `/api/plugins/<id>/ui/`
2. 前端通过 Bridge SDK 的 `pluginApi()` 调用后端路由，URL 前缀自动处理
3. `showToast()` 提供跨 iframe 的原生通知体验
4. 后端 `on_load` 同时注册 LLM 工具和 REST 路由，两个入口共享同一数据

---

## 相关文档 / Related

- [plugin-ui.md](../plugin-ui.md) — 完整 Bridge SDK 和协议参考 / Full Bridge SDK and protocol reference
- [api-reference.md](../api-reference.md) — PluginAPI 方法 / PluginAPI methods
- [rest-api.md](../rest-api.md) — 管理 API / Management REST API
