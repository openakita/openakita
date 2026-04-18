---
name: openakita/skills@yuque-skills
description: Manage Yuque (语雀) knowledge bases, documents, and team collaboration through API integration. Supports personal search, weekly reports, knowledge base management, document CRUD, and group collaboration workflows. Based on yuque/yuque-skills.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Yuque Skills — 语雀知识manage

## When to Use

- When the user needssearch语雀中的文档和知识
- 需要create、编辑、发布语雀文档
- 需要生成周报/月报并发布到语雀
- 需要manage知识库结构（目录、分类）
- 需要在团队知识库中进行协作
- 需要从语雀导出文档或同步内容
- 需要统计知识库的使用数据

---

## Prerequisites

### 必需配置

| 配置项 | 说明 |
|--------|------|
| `YUQUE_TOKEN` | 语雀 API Token |
| `YUQUE_HOST` | 语雀 API 地址（默认 `https://www.yuque.com/api/v2`） |

**get Token：**

1. 登录语雀 → 点击头像 → 设置 → Token
2. 或访问：https://www.yuque.com/settings/tokens
3. create新 Token，勾选所需权限

在 `.env` 中配置：

```
YUQUE_TOKEN=your_yuque_token_here
YUQUE_HOST=https://www.yuque.com/api/v2
```

> 企业版语雀的 Host 格式为 `https://your-company.yuque.com/api/v2`

### 必需依赖

| 依赖 | 用途 | install方式 |
|------|------|---------|
| `httpx` | HTTP API 调用 | `pip install httpx` |

### 可选依赖

| 依赖 | 用途 | install方式 |
|------|------|---------|
| `markdownify` | HTML → Markdown 转换 | `pip install markdownify` |
| `beautifulsoup4` | HTML 解析 | `pip install beautifulsoup4` |

### 验证配置

```bash
curl -s -H "X-Auth-Token: $YUQUE_TOKEN" "https://www.yuque.com/api/v2/user" | python -m json.tool
```

---

## Instructions

### 语雀核心概念

| 概念 | 英文 | 说明 |
|------|------|------|
| 用户 | User | 个人账号 |
| 团队 | Group | 组织/团队空间 |
| 知识库 | Book/Repo | 文档集合，类似文件夹 |
| 文档 | Doc | 具体的内容页面 |
| 目录 | TOC | 知识库内的文档组织结构 |
| 协作者 | Collaborator | 知识库的共同维护者 |

### API 基础调用

所有 API 请求需携带 Token：

```python
import httpx

YUQUE_HOST = os.environ.get("YUQUE_HOST", "https://www.yuque.com/api/v2")
YUQUE_TOKEN = os.environ["YUQUE_TOKEN"]

headers = {
    "X-Auth-Token": YUQUE_TOKEN,
    "Content-Type": "application/json",
    "User-Agent": "OpenAkita-Agent/1.0"
}

async def yuque_api(method, path, data=None):
    async with httpx.AsyncClient() as client:
        url = f"{YUQUE_HOST}{path}"
        response = await client.request(method, url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()["data"]
```

### 权限说明

| 权限 | 可执行操作 |
|------|-----------|
| 只读 | search、查看文档、导出 |
| 读写 | create、编辑、delete文档 |
| manage | 知识库设置、成员manage |

---

## Workflows

### Workflow 1: 个人文档search

**步骤 1 — get用户信息**

```python
user = await yuque_api("GET", "/user")
user_login = user["login"]
print(f"当前用户: {user['name']} ({user_login})")
```

**步骤 2 — search文档**

```python
async def search_docs(query, scope="user"):
    """search语雀文档"""
    params = {
        "q": query,
        "type": "doc",
        "scope": scope,
    }
    result = await yuque_api("GET", f"/search?q={query}&type=doc")
    return result
```

**步骤 3 — get文档内容**

```python
async def get_doc(repo_slug, doc_slug):
    """get文档详细内容"""
    doc = await yuque_api("GET", f"/repos/{repo_slug}/docs/{doc_slug}")
    return {
        "title": doc["title"],
        "body": doc["body"],        # Markdown 格式
        "body_html": doc["body_html"],  # HTML 格式
        "word_count": doc["word_count"],
        "updated_at": doc["updated_at"],
    }
```

**步骤 4 — 返回search结果摘要**

---

### Workflow 2: 周报/月报生成

**步骤 1 — 收集周报内容**

向用户询问或从其他来源汇总：

| 模块 | 内容 |
|------|------|
| 本周完成 | 已完成的工作项列表 |
| 进行中 | 正在推进的工作 |
| 下周计划 | 下周的工作安排 |
| 风险与阻塞 | 需要协助的问题 |
| 数据指标 | 关键 KPI 变化 |

**步骤 2 — 生成 Markdown 内容**

```python
def generate_weekly_report(data):
    """生成周报 Markdown"""
    report = f"""# 周报 | {data['date_range']}

## ✅ 本周完成

{format_task_list(data['completed'])}

## 🔄 进行中

{format_task_list(data['in_progress'])}

## 📋 下周计划

{format_task_list(data['next_week'])}

## ⚠️ 风险与阻塞

{format_risk_list(data['risks'])}

## 📊 关键指标

{format_metrics_table(data['metrics'])}
"""
    return report
```

**步骤 3 — 发布到语雀**

```python
async def publish_report(repo_slug, title, content):
    """发布周报到指定知识库"""
    doc_data = {
        "title": title,
        "slug": generate_slug(title),
        "body": content,
        "format": "markdown",
        "status": 1,  # 0=草稿, 1=发布
    }
    result = await yuque_api("POST", f"/repos/{repo_slug}/docs", data=doc_data)
    return result
```

**步骤 4 — 返回文档链接**

---

### Workflow 3: 知识库manage

**list所有知识库**

```python
async def list_repos(user_login=None, group_login=None):
    """list知识库"""
    if group_login:
        repos = await yuque_api("GET", f"/groups/{group_login}/repos")
    else:
        repos = await yuque_api("GET", f"/users/{user_login}/repos")

    return [{
        "id": r["id"],
        "name": r["name"],
        "slug": r["slug"],
        "description": r["description"],
        "docs_count": r["items_count"],
        "namespace": r["namespace"],
        "public": r["public"],
        "updated_at": r["updated_at"],
    } for r in repos]
```

**get知识库目录**

```python
async def get_toc(repo_namespace):
    """get知识库的目录结构"""
    toc = await yuque_api("GET", f"/repos/{repo_namespace}/toc")
    return toc
```

**create知识库**

```python
async def create_repo(user_or_group_login, name, description="", public=0):
    """create新知识库"""
    data = {
        "name": name,
        "slug": slugify(name),
        "description": description,
        "public": public,  # 0=私有, 1=公开
        "type": "Book",
    }
    result = await yuque_api("POST", f"/users/{user_or_group_login}/repos", data=data)
    return result
```

---

### Workflow 4: 文档 CRUD 操作

**create文档**

```python
async def create_doc(repo_namespace, title, body, format="markdown"):
    data = {
        "title": title,
        "slug": generate_slug(title),
        "body": body,
        "format": format,
    }
    return await yuque_api("POST", f"/repos/{repo_namespace}/docs", data=data)
```

**update文档**

```python
async def update_doc(repo_namespace, doc_id, title=None, body=None):
    data = {}
    if title:
        data["title"] = title
    if body:
        data["body"] = body
    return await yuque_api("PUT", f"/repos/{repo_namespace}/docs/{doc_id}", data=data)
```

**delete文档**

```python
async def delete_doc(repo_namespace, doc_id):
    return await yuque_api("DELETE", f"/repos/{repo_namespace}/docs/{doc_id}")
```

**导出文档**

```python
async def export_doc(repo_namespace, doc_slug, format="markdown"):
    """导出文档为指定格式"""
    doc = await get_doc(repo_namespace, doc_slug)
    if format == "markdown":
        return doc["body"]
    elif format == "html":
        return doc["body_html"]
    elif format == "text":
        from bs4 import BeautifulSoup
        return BeautifulSoup(doc["body_html"], "html.parser").get_text()
```

---

### Workflow 5: 团队协作

**list团队**

```python
async def list_groups():
    """list用户加入的所有团队"""
    groups = await yuque_api("GET", "/users/groups")
    return [{
        "id": g["id"],
        "name": g["name"],
        "login": g["login"],
        "description": g["description"],
        "members_count": g["members_count"],
    } for g in groups]
```

**团队知识库报告**

```python
async def generate_team_report(group_login):
    """生成团队知识库使用报告"""
    repos = await list_repos(group_login=group_login)

    report = {
        "total_repos": len(repos),
        "total_docs": sum(r["docs_count"] for r in repos),
        "repos_detail": [],
    }

    for repo in repos:
        docs = await yuque_api("GET", f"/repos/{repo['namespace']}/docs")
        recent_docs = sorted(docs, key=lambda d: d["updated_at"], reverse=True)[:5]
        report["repos_detail"].append({
            "name": repo["name"],
            "doc_count": repo["docs_count"],
            "recent_updates": [d["title"] for d in recent_docs],
        })

    return report
```

**知识库协作者manage**

```python
async def add_collaborator(repo_namespace, user_login, role="writer"):
    """添加知识库协作者"""
    data = {
        "login": user_login,
        "role": role,  # reader, writer, admin
    }
    return await yuque_api("POST", f"/repos/{repo_namespace}/collaborators", data=data)
```

---

### Workflow 6: 知识库内容同步

将外部内容同步到语雀，或从语雀同步到本地：

**从 Markdown 文件同步到语雀**

```python
async def sync_markdown_to_yuque(md_dir, repo_namespace):
    """将本地 Markdown 文件目录同步到语雀知识库"""
    import glob

    md_files = glob.glob(f"{md_dir}/**/*.md", recursive=True)

    for md_file in md_files:
        with open(md_file, "r", encoding="utf-8") as f:
            content = f.read()

        title = os.path.splitext(os.path.basename(md_file))[0]

        existing = await search_doc_by_title(repo_namespace, title)
        if existing:
            await update_doc(repo_namespace, existing["id"], body=content)
            print(f"update: {title}")
        else:
            await create_doc(repo_namespace, title, content)
            print(f"create: {title}")
```

**从语雀导出到本地**

```python
async def export_repo_to_local(repo_namespace, output_dir):
    """将语雀知识库导出为本地 Markdown 文件"""
    docs = await yuque_api("GET", f"/repos/{repo_namespace}/docs")

    os.makedirs(output_dir, exist_ok=True)

    for doc_info in docs:
        doc = await get_doc(repo_namespace, doc_info["slug"])
        file_path = os.path.join(output_dir, f"{doc_info['slug']}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# {doc['title']}\n\n{doc['body']}")
        print(f"导出: {doc['title']} -> {file_path}")
```

---

## Output Format

### search结果

```
🔍 search "项目规范" 共找到 5 条结果：

1. 📄 代码规范 v2.0
   - 知识库: 工程团队/开发规范
   - update时间: 2025-02-28
   - 链接: https://www.yuque.com/team/repo/doc-slug

2. 📄 项目manage规范
   - 知识库: PMO/流程文档
   - update时间: 2025-02-25
   - 链接: https://www.yuque.com/team/repo/doc-slug2

...
```

### 周报输出

生成后返回发布链接和摘要。

### 知识库统计

```
📊 团队知识库统计
- 总知识库数: 12
- 总文档数: 456
- 本月新增: 28 篇
- 最活跃知识库: "产品设计" (本月 12 篇update)
- 最近update:
  1. API 接口文档 v3 (2 小时前)
  2. Q1 OKR 复盘 (昨天)
  3. 新员工入职手册 (3 天前)
```

---

## Common Pitfalls

### 1. Token 权限不足

**症状**：API 返回 401 或 403
**解决**：
- 确认 Token 有读写权限
- 企业版语雀可能需要manage员授权
- 检查 Token 是否过期

### 2. namespace 格式错误

语雀的 namespace 格式为 `{user_or_group_login}/{repo_slug}`，例如 `myteam/dev-docs`。

**错误**：使用 repo 名称代替 slug
**正确**：使用 URL 中的 slug

### 3. Markdown 与 HTML 格式混淆

语雀文档有两种内容格式：
- `body`：Markdown 格式（create时使用 `format: "markdown"`）
- `body_html`：HTML 格式

create文档时需明确指定 `format` 参数。

### 4. API 频率限制

语雀 API 有请求频率限制：
- 个人版：约 100 次/分钟
- 企业版：通常更高

**解决**：批量操作时添加延迟：

```python
import asyncio
for doc in docs:
    await process_doc(doc)
    await asyncio.sleep(0.5)
```

### 5. 文档 slug 冲突

同一知识库中 slug 必须唯一。create文档前先检查是否已存在：

```python
async def safe_create_doc(repo_namespace, title, body):
    slug = generate_slug(title)
    existing = await find_doc_by_slug(repo_namespace, slug)
    if existing:
        slug = f"{slug}-{int(time.time())}"
    return await create_doc(repo_namespace, title, body, slug=slug)
```

### 6. 企业版与个人版 API 差异

企业版语雀的部分 API 路径和参数可能有差异：
- Host 不同（`your-company.yuque.com`）
- 部分接口需要额外权限
- 团队manage接口更丰富

### 7. 大文档性能问题

超过 5 万字的文档在create/update时可能超时。建议：
- 拆分为多篇文档
- 使用分页上传
- 图片先上传到 CDN 再引用

---

## EXTEND.md 扩展

用户可在技能同目录下create `EXTEND.md` 添加：
- 默认知识库 namespace
- 团队的语雀 Host 地址
- 周报/月报模板定制
- 常用知识库列表和别名
- 自定义文档模板
