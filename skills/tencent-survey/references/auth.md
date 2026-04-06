# 腾讯问卷鉴权检查

## 何时需要鉴权

- **会话首次调用工具前**：执行一次鉴权检查
- **鉴权通过后**：同一会话内后续调用**无需重复检查**，直接调用工具即可
- **按需重试**：仅当工具调用返回以下鉴权错误时，才需重新执行鉴权流程：
  - `invalid_token` / `invalid token`
  - `token expired`
  - `missing_token`
  - `invalid_token_prefix`

腾讯问卷授权流程，**必须按以下步骤执行**：

## 快速配置：通过环境变量传入 Token

如果已有 Token，可通过 `TENCENT_SURVEY_TOKEN` 环境变量直接完成配置，跳过 OAuth 授权：

```bash
TENCENT_SURVEY_TOKEN=xxx bash "${SKILL_DIR}/setup.sh" wj_check_and_start_auth
```

| 输出 | 处理方式 |
|------|---------|
| `READY` | ✅ Token 已写入配置，直接执行用户任务 |
| `ERROR:invalid_token_prefix` | Token 格式错误，必须以 `wjpt_` 开头 |
| `ERROR:save_token_failed` | Token 写入配置失败 |

> 设置了 `TENCENT_SURVEY_TOKEN` 时，脚本会优先使用该 Token，不再发起 OAuth 流程。

## 第一步：检查状态（立即返回）

未设置 `TENCENT_SURVEY_TOKEN` 时，进入标准 OAuth 设备授权流程：

```bash
bash "${SKILL_DIR}/setup.sh" wj_check_and_start_auth
```

> `${SKILL_DIR}` 为当前 skill 所在目录路径（即 `setup.sh` 所在目录）。

| 输出 | 处理方式 |
|------|---------|
| `READY` | ✅ 直接执行用户任务，**无需第二步** |
| `NONCE:<nonce>` | 记录 nonce 值，用于展示给用户（在 `AUTH_REQUIRED` 之前输出） |
| `AUTH_REQUIRED:<url>` | **立即**向用户展示授权链接（见下方模板），**然后执行第二步** |
| `ERROR:*` | 告知用户对应错误 |

## 第二步：等待授权完成（仅 AUTH_REQUIRED 时执行）

**展示授权链接后**，立即执行：

```bash
bash "${SKILL_DIR}/setup.sh" wj_wait_auth
```

| 输出 | 处理方式 |
|------|---------|
| `TOKEN_READY:ok` | ✅ 授权成功，Token 已写入配置，继续执行用户任务 |
| `AUTH_TIMEOUT` | 告知用户：「授权超时，请重新发起请求。」 |
| `ERROR:*` | 告知用户对应错误，请重新发起请求 |

## 第三步：人工兜底（前两步都失败的情况）

🔑 **手动获取 Token**：访问 [https://wj.qq.com/claw](https://wj.qq.com/claw) 登录后创建 Token，再通过环境变量配置：

```bash
TENCENT_SURVEY_TOKEN=<your_token> bash "${SKILL_DIR}/setup.sh" wj_check_and_start_auth
```

或手动执行 mcporter 命令：

```bash
mcporter config add tencent-survey "https://wj.qq.com/api/v2/mcp" \
    --header "Authorization=Bearer <your_token>" \
    --transport http \
    --scope home
```

> Token 以 `wjpt_` 开头，可在 [https://wj.qq.com/oauth/authorize](https://wj.qq.com/oauth/authorize) 登录后创建。

## 授权链接展示模板

当第一步输出 `AUTH_REQUIRED:<url>` 时，**立即**向用户展示：

> 🔑 **需要先完成腾讯问卷授权**
>
> 请确保在**浏览器**中打开以下链接完成授权：**[点击授权腾讯问卷]({url})**
>
> 🔑 授权码（nonce）：`{nonce}`
>
> ⚠️ 请使用 **QQ 或微信** 扫码 / 登录授权
>
> _(授权后将自动继续，无需回复)_

## 错误说明

| 错误 | 含义 |
|------|------|
| `ERROR:mcporter_not_found` | 缺少依赖，请先安装 Node.js |
| `ERROR:invalid_token_prefix` | `TENCENT_SURVEY_TOKEN` 格式错误，必须以 `wjpt_` 开头 |
| `ERROR:empty_token` | 授权异常，Token 为空 |
| `ERROR:save_token_failed` | Token 写入配置失败 |
| `AUTH_TIMEOUT` | 用户未在时限内完成授权 |
