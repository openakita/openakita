---
name: openakita/skills@yuque-skills
description: Manage Yuque () knowledge bases, documents, and team collaboration through API integration. Supports personal search, weekly reports, knowledge base management, document CRUD, and group collaboration workflows. Based on yuque/yuque-skills.
license: MIT
metadata:
 author: openakita
 version: "1.0.0"
---

# Yuque Skills — manage

## When to Use

- When the user needssearch and
- needcreate, Edit, 
- needGeneration/
- needmanage (, ) 
- needin
- needor
- need Use

---

## Prerequisites

###

| | Description |
|--------|------|
| `YUQUE_TOKEN` | API Token |
| `YUQUE_HOST` | API (Default `https://www.yuque.com/api/v2`) |

**get Token: **

1. → Click → Set → Token
2. or: https://www.yuque.com/settings/tokens
3. create Token, 

in `.env`: 

```
YUQUE_TOKEN=your_yuque_token_here
YUQUE_HOST=https://www.yuque.com/api/v2
```

> Host `https://your-company.yuque.com/api/v2`

###

| | | install |
|------|------|---------|
| `httpx` | HTTP API Call | `pip install httpx` |

### Optional

| | | install |
|------|------|---------|
| `markdownify` | HTML → Markdown | `pip install markdownify` |
| `beautifulsoup4` | HTML | `pip install beautifulsoup4` |

### Validate

```bash
curl -s -H "X-Auth-Token: $YUQUE_TOKEN" "https://www.yuque.com/api/v2/user" | python -m json.tool
```

---

## Instructions

###

| | | Description |
|------|------|------|
| | User | |
| | Group | / |
| | Book/Repo |, |
| | Doc | |
| | TOC | |
| | Collaborator | |

### API Call

have API Token: 

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

###

| | Execute |
|------|-----------|
| | search, View, |
| | create, Edit, delete |
| manage | Set, manage |

---

## Workflows

### Workflow 1: search

** 1 — get**

```python
user = await yuque_api("GET", "/user")
user_login = user["login"]
print(f"current user: {user['name']} ({user_login})")
```

** 2 — search**

```python
async def search_docs(query, scope="user"):
"""search"""
 params = {
 "q": query,
 "type": "doc",
 "scope": scope,
 }
 result = await yuque_api("GET", f"/search?q={query}&type=doc")
 return result
```

** 3 — get**

```python
async def get_doc(repo_slug, doc_slug):
"""get"""
 doc = await yuque_api("GET", f"/repos/{repo_slug}/docs/{doc_slug}")
 return {
 "title": doc["title"],
"body": doc["body"], # Markdown
"body_html": doc["body_html"], # HTML
 "word_count": doc["word_count"],
 "updated_at": doc["updated_at"],
 }
```

** 4 — Returnssearchneed**

---

### Workflow 2: /Generation

** 1 — **

or: 

| | |
|------|------|
| | |
| | in |
| | |
| and | need |
| | KPI |

** 2 — Generation Markdown **

```python
def generate_weekly_report(data):
"""Generation Markdown"""
report = f"""# | {data['date_range']}

## ✅

{format_task_list(data['completed'])}

## 🔄

{format_task_list(data['in_progress'])}

## 📋

{format_task_list(data['next_week'])}

## ⚠️ and

{format_risk_list(data['risks'])}

## 📊

{format_metrics_table(data['metrics'])}
"""
 return report
```

** 3 — **

```python
async def publish_report(repo_slug, title, content):
""""""
 doc_data = {
 "title": title,
 "slug": generate_slug(title),
 "body": content,
 "format": "markdown",
"status": 1, # 0=, 1=
 }
 result = await yuque_api("POST", f"/repos/{repo_slug}/docs", data=doc_data)
 return result
```

** 4 — Returns**

---

### Workflow 3: manage

**listhave**

```python
async def list_repos(user_login=None, group_login=None):
"""list"""
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

**get**

```python
async def get_toc(repo_namespace):
"""get """
 toc = await yuque_api("GET", f"/repos/{repo_namespace}/toc")
 return toc
```

**create**

```python
async def create_repo(user_or_group_login, name, description="", public=0):
"""create"""
 data = {
 "name": name,
 "slug": slugify(name),
 "description": description,
"public": public, # 0=have, 1=
 "type": "Book",
 }
 result = await yuque_api("POST", f"/users/{user_or_group_login}/repos", data=data)
 return result
```

---

### Workflow 4: CRUD

**create**

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

**update**

```python
async def update_doc(repo_namespace, doc_id, title=None, body=None):
 data = {}
 if title:
 data["title"] = title
 if body:
 data["body"] = body
 return await yuque_api("PUT", f"/repos/{repo_namespace}/docs/{doc_id}", data=data)
```

**delete**

```python
async def delete_doc(repo_namespace, doc_id):
 return await yuque_api("DELETE", f"/repos/{repo_namespace}/docs/{doc_id}")
```

****

```python
async def export_doc(repo_namespace, doc_slug, format="markdown"):
""""""
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

### Workflow 5:

**list**

```python
async def list_groups():
"""list have"""
 groups = await yuque_api("GET", "/users/groups")
 return [{
 "id": g["id"],
 "name": g["name"],
 "login": g["login"],
 "description": g["description"],
 "members_count": g["members_count"],
 } for g in groups]
```

****

```python
async def generate_team_report(group_login):
"""GenerationUse"""
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

**manage**

```python
async def add_collaborator(repo_namespace, user_login, role="writer"):
""""""
 data = {
 "login": user_login,
 "role": role, # reader, writer, admin
 }
 return await yuque_api("POST", f"/repos/{repo_namespace}/collaborators", data=data)
```

---

### Workflow 6:

, or: 

** Markdown **

```python
async def sync_markdown_to_yuque(md_dir, repo_namespace):
""" Markdown """
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

****

```python
async def export_repo_to_local(repo_namespace, output_dir):
""" Markdown """
 docs = await yuque_api("GET", f"/repos/{repo_namespace}/docs")

 os.makedirs(output_dir, exist_ok=True)

 for doc_info in docs:
 doc = await get_doc(repo_namespace, doc_info["slug"])
 file_path = os.path.join(output_dir, f"{doc_info['slug']}.md")
 with open(file_path, "w", encoding="utf-8") as f:
 f.write(f"# {doc['title']}\n\n{doc['body']}")
print(f": {doc['title']} -> {file_path}")
```

---

## Output Format

### search

```
🔍 search "" 5: 

1. 📄 v2.0
-: /
- update: 2025-02-28
-: https://www.yuque.com/team/repo/doc-slug

2. 📄 manage
-: PMO/
- update: 2025-02-25
-: https://www.yuque.com/team/repo/doc-slug2

...
```

###

GenerationReturnsandneed. 

###

```
📊
-: 12
-: 456
-: 28
-: "" ( 12 update)
- update:
1. API v3 (2 )
2. Q1 OKR ()
3. (3 )
```

---

## Common Pitfalls

### 1. Token not

****: API Returns 401 or 403
****: 
- Token have
- needmanage
- Token YesNo

### 2. namespace

namespace `{user_or_group_login}/{repo_slug}`, `myteam/dev-docs`. 

****: Use repo slug
****: Use URL slug

### 3. Markdown and HTML

have: 
- `body`: Markdown (createUse `format: "markdown"`) 
- `body_html`: HTML

create `format`. 

### 4. API

API have: 
-: 100 /
-: 

****:: 

```python
import asyncio
for doc in docs:
 await process_doc(doc)
 await asyncio.sleep(0.5)
```

### 5. slug

slug. createYesNoin: 

```python
async def safe_create_doc(repo_namespace, title, body):
 slug = generate_slug(title)
 existing = await find_doc_by_slug(repo_namespace, slug)
 if existing:
 slug = f"{slug}-{int(time.time())}"
 return await create_doc(repo_namespace, title, body, slug=slug)
```

### 6. and API

Partial API andhave: 
- Host not (`your-company.yuque.com`) 
- Partialneed
- manage

### 7.

5 increate/update.: 
-
- UseUpload
- Upload CDN

---

## EXTEND.md

increate `EXTEND.md`: 
- Default namespace
- Host
- /
- and
-