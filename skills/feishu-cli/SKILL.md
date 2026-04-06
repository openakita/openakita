---
name: openakita/skills@feishu-cli
description: "Feishu/Lark CLI - official open-source CLI tool from Feishu for AI Agents. Provides 200+ commands across 12 business domains: IM, Docs, Sheets, Base (Bitable), Calendar, Video Meeting, Mail, Tasks, Wiki, Drive, Contacts, Search. Supports both user identity and bot identity authentication. Use when user wants to operate Feishu/Lark resources."
license: MIT
metadata:
  author: larksuite
  version: "1.0.0"
---

# 飞书 CLI (lark-cli)

飞书官方开源的命令行工具，为 AI Agent 提供连接飞书业务系统的标准化执行入口。安装后 Agent 可以直接读消息、查日历、写文档、建多维表格、发邮件，把任务真正落到飞书里完成。

> 官方 GitHub: https://github.com/larksuite/cli
> 官方介绍: https://www.feishu.cn/content/article/7623291503305083853

## 安装

```bash
# 第一步：安装 lark-cli
npm install -g @larksuite/cli

# 第二步：安装相关 Skills
npx skills add https://github.com/larksuite/cli -y -g

# 第三步：初始化应用配置（默认创建新应用，也可选已有应用）
lark-cli config init --new
```

安装完成后需重启 AI Agent 工具，确保 skills 完整加载。

## 认证

飞书 CLI 支持两种工作模式：

### 应用身份（Bot）
不需要用户授权即可使用。AI 可执行发消息、创建文档等操作，但无法访问用户个人数据（如日程、私信、收件箱）。只需在飞书开发者后台开通对应 scope。

### 用户身份（User）
AI 可以访问用户的个人日历、消息、文档，并以用户名义执行操作。需要完成一次用户授权：

```bash
lark-cli auth login
```

执行后打开链接在飞书中确认即可。后续 AI 在需要访问个人数据时也会自动发起授权提示。

### 身份选择原则

- Bot 看不到用户资源（日历、云空间文档、邮箱等）
- Bot 无法代表用户操作
- 涉及个人数据的操作必须使用 User 身份

### 权限不足处理

- Bot 身份：将 console_url 提供给用户，去后台开通 scope
- User 身份：`lark-cli auth login --scope "missing_scope"`

## 核心业务域

| 业务域 | 核心能力 |
|--------|---------|
| 消息与群组 | 搜索消息和群聊、发送消息、回复话题 |
| 云文档 | 创建文档、读取内容、更新正文、评论协作 |
| 云空间 | 上传下载文件、管理权限、处理评论 |
| 电子表格 | 创建表格、读写单元格、批量更新 |
| 多维表格 | 管理数据表、字段、记录、视图、仪表盘、自动化 |
| 日历 | 查询日程、创建会议、查询忙闲、推荐时间 |
| 视频会议 | 搜索会议、获取纪要和逐字稿、关联日程文档 |
| 邮箱 | 搜索、读取、起草、发送、回复、归档邮件 |
| 任务 | 创建任务、更新状态、管理清单和子任务 |
| 知识库 | 查询空间、管理节点和文档层级 |
| 通讯录 | 查询用户、搜索同事、查看部门 |
| 搜索 | 搜索群聊、消息、文档等 |

## 典型使用场景

### 会议待办自动执行
读取妙记逐字稿，提取待办事项，自动帮用户创建文档、发送消息、预约会议。

### 人与 AI 共创文档
AI 在飞书文档里直接创建初稿，用户用评论提修改意见，AI 读取评论修改正文，持续迭代。也可反过来让 AI 当审稿人用评论提意见。支持 Markdown 与飞书文档双向转换。

### 跨时区多人智能约会
AI 自动拉群成员、查每个人的日历空闲、考虑所有人时区，推荐合适的会议时间。

### 日历审计到多维表格仪表盘
拉取日历数据，给会议打标签分类，写入多维表格生成仪表盘，可视化时间分配。

### 未读邮件智能分类
AI 定期扫描未读邮件，按优先级分类，重要邮件摘要推送到群聊，低优先级自动归档。

## 验证安装

```bash
lark-cli help          # 查看命令总览
lark-cli auth status   # 查看当前登录状态
```

## 安全规则

- 禁止输出密钥（appSecret、accessToken）到终端明文
- 写入/删除操作前必须确认用户意图
- 用 `--dry-run` 预览危险请求

## 更新

lark-cli 命令执行后如检测到新版本，输出中会包含 `_notice.update` 字段。更新命令：

```bash
npm update -g @larksuite/cli && npx skills add larksuite/cli -g -y
```

## 支持国际版 Lark

通过 `lark-cli config init` 并配置国际版 Lark 的应用即可使用。

## 预置脚本

### scripts/setup.py
飞书 lark-cli 安装配置脚本。

```bash
python3 scripts/setup.py
```

### scripts/feishu_quick.py
飞书常用操作快捷脚本。

```bash
python3 scripts/feishu_quick.py send-msg --receive-id xxx --content "Hello"
python3 scripts/feishu_quick.py list-chats
python3 scripts/feishu_quick.py create-doc --folder-token xxx --title "新文档"
python3 scripts/feishu_quick.py list-events --calendar-id xxx
python3 scripts/feishu_quick.py create-task --summary "待办事项"
```
