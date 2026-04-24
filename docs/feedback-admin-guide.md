# 用户反馈管理指南

## 基本信息

- **Worker 地址**: `https://feedback.openakita.ai`（备用: `https://bug-report-worker.zacon365.workers.dev`）
- **Admin API Key**: `4d8c535c9d5aa05e4238ce3f9e2692fb91319e0d955a703f96b3eceed17ecbfd`
- **认证方式**: HTTP Header `Authorization: Bearer <ADMIN_API_KEY>`

## API 接口

### 1. 查看所有反馈

```bash
curl -s -H "Authorization: Bearer <ADMIN_API_KEY>" \
  "https://feedback.openakita.ai/admin/reports" | python -m json.tool
```

可选参数：
- `?type=bug` — 只看错误报告
- `?type=feature` — 只看需求建议
- `?limit=50` — 限制返回条数（默认 50，最大 100）

### 2. 查看单条反馈详情

```bash
curl -s -H "Authorization: Bearer <ADMIN_API_KEY>" \
  "https://feedback.openakita.ai/admin/reports/<REPORT_ID>" | python -m json.tool
```

### 3. 下载反馈 zip 包

```bash
curl -s -H "Authorization: Bearer <ADMIN_API_KEY>" \
  "https://feedback.openakita.ai/admin/reports/<REPORT_ID>/download" \
  -o <REPORT_ID>.zip
```

### 4. 删除反馈

```bash
curl -s -X DELETE -H "Authorization: Bearer <ADMIN_API_KEY>" \
  "https://feedback.openakita.ai/admin/reports/<REPORT_ID>"
```

## Zip 包内容结构

每个下载的 zip 包解压后包含：

```
├── metadata.json          # 报告元信息（标题、描述、系统信息、时间戳）
├── images/                # 用户上传的截图
│   ├── 00_screenshot.png
│   └── ...
├── logs/                  # 应用日志（最近 1MB）
│   ├── openakita.log
│   └── error.log
└── llm_debug/             # 最近 50 条 LLM 调试文件
    ├── 2026-02-25_xxx.json
    └── ...
```

### metadata.json 示例

```json
{
  "report_id": "938a21595b31",
  "type": "bug",
  "title": "技能市场安装技能报错",
  "description": "在技能浏览市场搜索了一个技能，点击安装的时候报错...",
  "steps": "1. 打开技能市场\n2. 搜索技能\n3. 点击安装",
  "system_info": {
    "os": "Windows 10 AMD64",
    "python": "3.11.9",
    "openakita_version": "1.24.2+2fb7fb5",
    "git_version": "git version 2.43.0.windows.1",
    "packages": { "fastapi": "0.115.0", "...": "..." },
    "memory_total_gb": 16.0,
    "disk_free_gb": 120.5,
    "path_env": "C:\\Windows\\system32;..."
  },
  "created_at": "2026-02-25T03:57:34"
}
```

## 批量下载脚本

一键下载所有未处理的反馈到本地 `bug-reports/` 目录：

```bash
# 设置 API Key
API_KEY="<ADMIN_API_KEY>"
WORKER="https://feedback.openakita.ai"
OUTDIR="bug-reports"
mkdir -p $OUTDIR

# 获取列表并逐个下载
curl -s -H "Authorization: Bearer $API_KEY" "$WORKER/admin/reports" | \
  python -c "
import json, sys, subprocess, os
data = json.load(sys.stdin)
outdir = '$OUTDIR'
for r in data['reports']:
    rid = r['id']
    title = r.get('title', 'untitled').replace('/', '_')[:30]
    fname = f'{outdir}/{rid}_{title}.zip'
    if os.path.exists(fname):
        print(f'  SKIP {fname}')
        continue
    print(f'  DOWN {fname}')
    subprocess.run([
        'curl', '-s',
        '-H', 'Authorization: Bearer $API_KEY',
        f'$WORKER/admin/reports/{rid}/download',
        '-o', fname
    ])
print(f'Done. Total: {data[\"total\"]}')
"
```

## 注意事项

- Admin API Key 请妥善保管，不要泄露到公开渠道
- 反馈数据可能包含用户日志和 LLM 对话内容，注意隐私保护
- R2 存储有 lifecycle 策略，过期数据会自动清理

