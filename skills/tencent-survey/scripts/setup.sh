#!/bin/bash
#
# Setup script for 腾讯问卷 MCP Skill (内部 OpenClaw 版本) 一体化配置与授权脚本
#
# 功能：
#   1. 检查 mcporter 是否已配置 tencent-survey（含 Authorization 可用）
#   2. 未配置或 Token 失效时，展示授权链接
#   3. 前台轮询等待用户完成授权，Token 获取后自动写入 mcporter 并继续
#   4. 对超时、过期、错误等场景给出友好提示
#
# 用法（供 AI Agent 调用）：
#   第一步：检查状态（立即返回，不阻塞）
#     bash ./setup.sh wj_check_and_start_auth
#     输出：
#       READY                  → 服务已就绪，直接执行用户任务，无需第二步
#       NONCE:<nonce>          → nonce 值（在 AUTH_REQUIRED 之前输出）
#       AUTH_REQUIRED:<url>    → 立即向用户展示授权链接，然后执行第二步
#       ERROR:*                → 告知用户对应错误
#
#   第二步：等待授权完成（仅 AUTH_REQUIRED 时执行，阻塞最多约 300s）
#     bash ./setup.sh wj_wait_auth
#     输出：
#       TOKEN_READY:ok         → 授权成功，Token 已写入配置，继续执行用户任务
#       AUTH_TIMEOUT           → 告知用户：授权超时，请重新发起请求
#       ERROR:*                → 告知用户对应错误
#
# 直接执行（排查问题）：
#   bash ./setup.sh
#

# ── 全局配置 ──────────────────────────────────────────────────────────────────
_WJ_API_BASE="${WJ_API_BASE_URL:-https://wj.qq.com}"
_WJ_AUTH_PAGE="${WJ_AUTH_PAGE_URL:-https://wj.qq.com/oauth/authorize}"

# 从 WJ_AUTH_PAGE_URL 中提取查询参数（如 _tde_id=2952），附加到 API 请求
_WJ_EXTRA_QUERY=""
if [[ "$_WJ_AUTH_PAGE" == *"?"* ]]; then
    _WJ_EXTRA_QUERY="${_WJ_AUTH_PAGE#*\?}"
fi

# 构造 API URL，如有额外参数则附加
_WJ_MCP_URL="${_WJ_API_BASE}/api/v2/mcp"
_WJ_TOKEN_POLL_URL="${_WJ_API_BASE}/api/v2/account/tokens/device-auth/poll"
if [[ -n "$_WJ_EXTRA_QUERY" ]]; then
    _WJ_MCP_URL="${_WJ_MCP_URL}?${_WJ_EXTRA_QUERY}"
    _WJ_TOKEN_POLL_URL="${_WJ_TOKEN_POLL_URL}?${_WJ_EXTRA_QUERY}"
fi
_WJ_SERVICE_NAME="tencent-survey"

# 轮询参数：每 2s 一次，最多 150 次（约 300s）
_WJ_POLL_INTERVAL=2
_WJ_POLL_MAX=150

# 临时文件：使用基于 UID 的私有目录，避免 symlink 攻击和多用户冲突
_WJ_TMP_DIR="${TMPDIR:-/tmp}/.wj_auth_$(id -u)"
mkdir -p "$_WJ_TMP_DIR" 2>/dev/null
chmod 700 "$_WJ_TMP_DIR"

_WJ_CODE_FILE="${_WJ_TMP_DIR}/code"
_WJ_TOKEN_FILE="${_WJ_TMP_DIR}/token"
_WJ_URL_FILE="${_WJ_TMP_DIR}/url"
_WJ_NONCE_FILE="${_WJ_TMP_DIR}/nonce"

# ── 安全写入函数（拒绝写入符号链接）────────────────────────────────────────
_wj_safe_write() {
    local file="$1" content="$2"
    if [[ -L "$file" ]]; then
        echo "ERROR:security - 检测到符号链接，拒绝写入: $file" >&2
        return 1
    fi
    echo "$content" > "$file"
    chmod 600 "$file"
}

# ── 清理函数 ──────────────────────────────────────────────────────────────────
_wj_cleanup() {
    [[ -d "$_WJ_TMP_DIR" ]] && rm -rf "$_WJ_TMP_DIR"
    # 重建私有目录，确保后续写入可用
    mkdir -p "$_WJ_TMP_DIR" 2>/dev/null
    chmod 700 "$_WJ_TMP_DIR"
}

# ── 检查 mcporter 是否已安装 ──────────────────────────────────────────────────
_wj_check_mcporter() {
    if ! command -v mcporter &> /dev/null; then
        echo "⚠️  未找到 mcporter，正在安装..."
        if command -v npm &>/dev/null; then
            npm install -g mcporter 2>&1 | tail -3
            echo "✅ mcporter 安装完成"
        else
            echo "ERROR:no_npm"
            return 1
        fi
    fi
    return 0
}

# ── 检查必要系统依赖 ──────────────────────────────────────────────────────────
_wj_check_dependencies() {
    local missing=()
    command -v curl &>/dev/null || missing+=("curl")
    command -v jq   &>/dev/null || missing+=("jq")

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "ERROR:missing_dependencies - 缺少必要依赖: ${missing[*]}"
        return 1
    fi
    return 0
}

# ── JSON 字段提取（使用 jq）──────────────────────────────────────────────────
# 提取 JSON 顶层字段
# 用法：_wj_json_get '{"code":"Ok","data":{...}}' "code"  → Ok
_wj_json_get() {
    local json="$1" key="$2"
    echo "$json" | jq -r --arg k "$key" '.[$k] // empty'
}

# 提取 JSON "data" 子对象中的字段
# 用法：_wj_json_get_data '{"code":"Ok","data":{"token":"xxx"}}' "token"  → xxx
_wj_json_get_data() {
    local json="$1" key="$2"
    echo "$json" | jq -r --arg k "$key" '.data[$k] // empty'
}

# 从 mcporter config get 读取当前 Authorization Token
# 输出：token 字符串（空则表示服务未注册或 Token 未配置）
_wj_get_token() {
    local output
    output=$(mcporter config get "$_WJ_SERVICE_NAME" 2>/dev/null) || return 1

    # 从输出中提取 Authorization 头的值
    local token
    token=$(echo "$output" | grep -i '^\s*Authorization:' | sed 's/.*Authorization:[[:space:]]*//' | tr -d '[:space:]')
    echo "$token"
}

# ── 将 Token 写入 mcporter 配置 ───────────────────────────────────────────────
# 用法：_wj_save_token <token>
_wj_save_token() {
    # 添加 MCP 配置
    echo "🔧 配置 mcporter..."

    local token="$1"
    [[ -z "$token" ]] && return 1

    # 使用传入的 token 写入 mcporter 配置（tencent-survey）
    mcporter config add "$_WJ_SERVICE_NAME" "$_WJ_MCP_URL" \
        --header "Authorization=Bearer $token" \
        --transport http \
        --scope home

    echo ""
    echo "✅ 配置完成！"
    echo ""

    echo "🧪 验证配置..."
    if mcporter list 2>&1 | grep -q "$_WJ_SERVICE_NAME"; then
        echo "✅ tencent-survey 配置验证成功！"
        echo ""
        mcporter list | grep -A 1 "$_WJ_SERVICE_NAME" || true
    else
        echo "⚠️  tencent-survey 配置验证失败，请检查网络或 Token 是否有效"
    fi

    echo ""
    echo "─────────────────────────────────────"
    echo "🎉 设置完成！"
    echo ""
    echo "📖 使用方法："
    echo "   mcporter call ${_WJ_SERVICE_NAME}.get_survey --args '{\"survey_id\": 12345}'"
    echo "   mcporter call ${_WJ_SERVICE_NAME}.create_survey --args '{\"text\": \"问卷标题\\n\\n1. 题目\"}'"
    echo ""
    echo "🏠 腾讯问卷主页：${_WJ_API_BASE}"
    echo ""
    echo "📖 更多信息请查看 SKILL.md"
    echo ""
    return 0
}

# ── 检查 tencent-survey 服务状态 ────────────────────────────────────────────────
# 返回值：
#   0 = 服务正常可用（有 Token）
#   1 = 服务未注册（mcporter config get 失败）
#   2 = Token 为空或未配置
_wj_check_service() {
    if ! mcporter list 2>/dev/null | grep -q "$_WJ_SERVICE_NAME"; then
        return 1
    fi

    local token
    token=$(_wj_get_token)
    local rc=$?

    # mcporter config get 返回非 0 表示服务未注册
    if [[ $rc -ne 0 ]]; then
        return 1
    fi

    # Token 为空表示服务已注册但未配置 Authorization
    if [[ -z "$token" ]]; then
        return 2
    fi

    return 0
}

# ── 生成授权链接（调用后端 /init API 获取 code）─────────────────────────────
# 输出：auth_url 字符串
# code 由后端 crypto/rand 生成，保证密码学安全性
_wj_generate_auth_url() {
    # 构造 init API URL
    local init_url="${_WJ_API_BASE}/api/v2/account/tokens/device-auth/init"
    if [[ -n "$_WJ_EXTRA_QUERY" ]]; then
        init_url="${init_url}?${_WJ_EXTRA_QUERY}"
    fi

    # 调用后端 init 端点
    local response
    response=$(curl -s -f -X POST "$init_url" 2>/dev/null) || {
        echo "ERROR:init_request_failed - 无法连接到服务器 ${init_url}"
        return 1
    }

    # 解析响应中的 code
    local resp_code
    resp_code=$(_wj_json_get "$response" "code")
    local resp_upper
    resp_upper=$(echo "$resp_code" | tr '[:lower:]' '[:upper:]')
    if [[ "$resp_upper" != "OK" ]]; then
        echo "ERROR:init_failed - 服务端返回错误: $resp_code"
        return 1
    fi

    local code
    code=$(_wj_json_get_data "$response" "code")

    local nonce
    nonce=$(_wj_json_get_data "$response" "nonce")

    # 防御性校验
    if [[ ! "$code" =~ ^[0-9a-f]{16,64}$ ]]; then
        echo "ERROR:invalid_code - 服务端返回的 code 格式非法: $code"
        return 1
    fi
    if [[ -z "$nonce" ]]; then
        echo "ERROR:invalid_nonce - 服务端未返回 nonce"
        return 1
    fi

    _wj_safe_write "$_WJ_CODE_FILE" "$code" || return 1
    _wj_safe_write "$_WJ_NONCE_FILE" "$nonce" || return 1
    # 如果 AUTH_PAGE 已包含 ?，则用 & 拼接；否则用 ?
    local sep="?"
    [[ "$_WJ_AUTH_PAGE" == *"?"* ]] && sep="&"
    echo "${_WJ_AUTH_PAGE}${sep}code=${code}&nonce=${nonce}"
}

# ── 前台轮询 Token（通用函数）─────────────────────────────────────────────────
# 用法：_wj_poll_token [log_prefix]
#   log_prefix: 日志前缀（默认 "⏳"），用于区分调用场景的输出
#
# 前置条件：$_WJ_CODE_FILE 已存在且包含有效的 code
#
# 输出（最后一行为结构化结果）：
#   TOKEN_READY:<token>    授权成功
#   AUTH_TIMEOUT           超时
#   ERROR:empty_token      Token 为空异常
#   ERROR:no_code          未找到授权码
#   ERROR:empty_code       授权码为空
_wj_poll_token() {
    local prefix="${1:-⏳}"

    # 检查 code 文件
    if [[ ! -f "$_WJ_CODE_FILE" ]]; then
        echo "ERROR:no_code - 未找到授权码，请先执行 wj_check_and_start_auth"
        return 1
    fi

    local code
    code=$(cat "$_WJ_CODE_FILE")
    if [[ -z "$code" ]]; then
        echo "ERROR:empty_code - 授权码为空"
        return 1
    fi

    local poll_sep="?"
    [[ "$_WJ_TOKEN_POLL_URL" == *"?"* ]] && poll_sep="&"
    local url="${_WJ_TOKEN_POLL_URL}${poll_sep}code=${code}"

    local i
    for ((i=1; i<=_WJ_POLL_MAX; i++)); do
        sleep "$_WJ_POLL_INTERVAL"

        local response
        response=$(curl -s -f -L "$url" 2>/dev/null) || {
            echo "  ${prefix} [$i/$_WJ_POLL_MAX] curl 请求失败"
            continue
        }

        local resp_code
        resp_code=$(_wj_json_get "$response" "code")

        # 兼容 "Ok" 和 "OK"
        local resp_upper
        resp_upper=$(echo "$resp_code" | tr '[:lower:]' '[:upper:]')
        if [[ "$resp_upper" != "OK" && -n "$resp_upper" ]]; then
            echo "  ${prefix} [$i/$_WJ_POLL_MAX] resp_code=$resp_code (非Ok)"
            continue
        fi

        local status
        status=$(_wj_json_get_data "$response" "status")

        local token
        token=$(_wj_json_get_data "$response" "token")

        case "$status" in
            "completed")
                echo "  ${prefix} [$i/$_WJ_POLL_MAX] status=completed ✅"
                if [[ -n "$token" ]]; then
                    echo "TOKEN_READY:$token"
                    return 0
                fi
                echo "  ${prefix} [$i/$_WJ_POLL_MAX] ⚠️  status=completed 但 token 为空"
                echo "ERROR:empty_token"
                return 1
                ;;
            "pending")
                echo "  ${prefix} [$i/$_WJ_POLL_MAX] status=pending"
                continue
                ;;
            *)
                echo "  ${prefix} [$i/$_WJ_POLL_MAX] status=$status (未知状态)"
                continue
                ;;
        esac
    done

    echo "AUTH_TIMEOUT"
    return 2
}

# ── 执行授权流程（第一阶段）：生成链接（立即返回，不阻塞）─────────────────────
# 输出：
#   AUTH_REQUIRED:<url>   立即输出到 stdout，同时写入 $_WJ_URL_FILE
_wj_do_auth_start() {
    _wj_cleanup

    # 生成授权链接（同时写入 code 和 nonce 文件）
    local auth_url
    auth_url=$(_wj_generate_auth_url)
    local rc=$?

    # 检查生成是否成功
    if [[ $rc -ne 0 ]]; then
        echo "$auth_url"   # 透传 ERROR:xxx 消息
        return 1
    fi

    # 将 URL 写入文件，供后续阶段读取
    _wj_safe_write "$_WJ_URL_FILE" "$auth_url" || return 1

    # 读取 nonce 并显示
    local nonce=""
    [[ -f "$_WJ_NONCE_FILE" ]] && nonce=$(cat "$_WJ_NONCE_FILE")
    if [[ -n "$nonce" ]]; then
        echo "NONCE:$nonce"
    fi

    # ★ 立即输出授权链接（调用方可立即展示给用户）
    echo "AUTH_REQUIRED:$auth_url"
    return 0
}

# ── 主入口函数 A：检查状态 / 生成授权链接（立即返回，不阻塞）────────────────
#
# AI Agent 第一步调用此函数，命令执行完毕后立即拿到输出：
#   READY                  服务已就绪，直接执行用户任务，无需第二步
#   NONCE:<nonce>          nonce 值（在 AUTH_REQUIRED 之前输出）
#   AUTH_REQUIRED:<url>    需要授权：立即展示链接给用户，然后执行第二步
#   ERROR:*                错误信息
#
wj_check_and_start_auth() {
    _wj_check_mcporter || {
        echo "ERROR:mcporter_not_found - 请先安装 Node.js 和 npm 后重试"
        return 1
    }

    _wj_check_dependencies || return 1

    # ★ 如果设置了 TENCENT_SURVEY_TOKEN 环境变量，直接写入配置
    if [[ -n "$TENCENT_SURVEY_TOKEN" ]]; then
        if [[ ! "$TENCENT_SURVEY_TOKEN" =~ ^wjpt_ ]]; then
            echo "ERROR:invalid_token_prefix - TENCENT_SURVEY_TOKEN 必须以 wjpt_ 开头"
            return 1
        fi
        if _wj_save_token "$TENCENT_SURVEY_TOKEN" >/dev/null 2>&1; then
            echo "READY"
            return 0
        else
            echo "ERROR:save_token_failed - Token 写入配置失败"
            return 1
        fi
    fi

    _wj_check_service
    local status=$?

    case $status in
        0)
            echo "READY"
            return 0
            ;;
        1|2)
            _wj_do_auth_start || return 1
            return 0
            ;;
    esac
}

# ── 主入口函数 B：等待授权完成（阻塞，最多约 300s）────────────────────────────
#
# AI Agent 在展示授权链接后调用此函数，等待用户完成授权：
#   TOKEN_READY:ok         授权成功，Token 已写入配置，直接执行用户任务
#   AUTH_TIMEOUT           超时，告知用户重新发起请求
#   ERROR:*                错误信息
#
wj_wait_auth() {
    # 前台轮询 API 等待授权完成
    local result
    result=$(_wj_poll_token "⏳")
    local rc=$?

    # 提取最后一行作为结构化结果
    local last_line
    last_line=$(echo "$result" | tail -1)

    # 输出过程日志（去掉最后一行结果）
    echo "$result" | sed '$d'

    case "$last_line" in
        TOKEN_READY:*)
            local token="${last_line#TOKEN_READY:}"
            if _wj_save_token "$token"; then
                _wj_cleanup
                echo "TOKEN_READY:ok"
                return 0
            else
                _wj_cleanup
                echo "ERROR:save_token_failed"
                return 1
            fi
            ;;
        AUTH_TIMEOUT)
            _wj_cleanup
            echo "AUTH_TIMEOUT"
            return 2
            ;;
        ERROR:empty_token*)
            _wj_cleanup
            echo "ERROR:empty_token - 授权异常，Token 为空"
            return 1
            ;;
        ERROR:*)
            _wj_cleanup
            echo "$last_line"
            return 1
            ;;
    esac
}


# ── 直接执行时的交互式安装流程 ───────────────────────────────────────────────
_wj_interactive_setup() {
    echo ""
    echo "╔══════════════════════════════════════════════╗"
    echo "║     腾讯问卷 MCP Skill 配置向导              ║"
    echo "╚══════════════════════════════════════════════╝"
    echo ""

    # 检查 mcporter
    echo "🔍 检查 mcporter..."
    if ! _wj_check_mcporter; then
        echo "❌ mcporter 安装失败，请先安装 Node.js (https://nodejs.org) 后重试"
        exit 1
    fi
    echo "✅ mcporter 已就绪"
    echo ""

    # 检查系统依赖
    echo "🔍 检查系统依赖..."
    if ! _wj_check_dependencies; then
        echo "❌ 缺少必要依赖，请先安装后重试"
        exit 1
    fi
    echo "✅ 系统依赖已就绪"
    echo ""

    # 检查服务状态
    echo "🔍 检查 tencent-survey 服务配置..."
    _wj_check_service
    local status=$?

    case $status in
        0)
            echo "✅ tencent-survey 服务已配置且运行正常！"
            echo ""
            echo "🎉 无需重新配置，您可以直接使用腾讯问卷功能。"
            echo ""
            echo "📖 使用示例："
            echo "   mcporter call tencent-survey.get_survey --args '{\"survey_id\": 12345}'"
            return 0
            ;;
        1|2)
            echo "⚠️  Token 未配置，需要授权..."
            ;;
    esac

    echo ""
    echo "🔐 需要完成腾讯问卷授权"
    echo ""

    # 清理旧状态
    _wj_cleanup

    # 生成授权链接（同时写入 code 和 nonce 文件）
    local auth_url
    auth_url=$(_wj_generate_auth_url)
    if [[ $? -ne 0 ]]; then
        echo "❌ 生成授权链接失败：$auth_url"
        exit 1
    fi

    # 读取 nonce
    local nonce=""
    [[ -f "$_WJ_NONCE_FILE" ]] && nonce=$(cat "$_WJ_NONCE_FILE")

    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│  请在浏览器中打开以下链接完成授权：                      │"
    echo "│                                                         │"
    printf "│  %s\n" "$auth_url"
    echo "│                                                         │"
    if [[ -n "$nonce" ]]; then
    printf "│  🔑 nonce: %s\n" "$nonce"
    echo "│                                                         │"
    fi
    echo "│  ⚠️  请使用 QQ 或微信 扫码 / 登录授权                   │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo ""
    echo "⏳ 正在等待您完成授权，无需任何额外操作..."
    echo "   （最多等待 $((_WJ_POLL_MAX * _WJ_POLL_INTERVAL)) 秒）"
    echo ""

    # ★ 前台轮询等待授权完成
    local result
    result=$(_wj_poll_token "🔄")
    local rc=$?

    # 提取最后一行作为结构化结果
    local last_line
    last_line=$(echo "$result" | tail -1)

    # 输出过程日志（去掉最后一行结果）
    echo "$result" | sed '$d'

    case "$last_line" in
        TOKEN_READY:*)
            local token="${last_line#TOKEN_READY:}"
            echo ""
            echo "✅ 授权成功！正在保存配置..."
            if _wj_save_token "$token"; then
                _wj_cleanup
                echo "✅ Token 已写入 mcporter 配置"
                echo ""
                echo "🎉 配置完成！现在可以直接使用腾讯问卷功能了。"
                echo ""
                echo "📖 使用示例："
                echo "   mcporter call ${_WJ_SERVICE_NAME}.get_survey --args '{\"survey_id\": 12345}'"
                echo ""
                echo "🏠 腾讯问卷主页：${_WJ_API_BASE}"
            else
                # Token 写入临时文件，避免在终端明文打印
                local token_file="${_WJ_TMP_DIR}/token_backup"
                _wj_safe_write "$token_file" "$token"
                echo "⚠️  Token 写入配置失败"
                echo "   Token 已保存到临时文件: $token_file"
                echo "   请手动运行："
                echo "   mcporter config add ${_WJ_SERVICE_NAME} ${_WJ_MCP_URL} --header \"Authorization=Bearer \$(cat $token_file)\" --transport http --scope home"
            fi
            ;;
        AUTH_TIMEOUT)
            echo ""
            echo "⏳ 授权超时（未在时限内完成授权）"
            echo "   请重新运行：bash ./setup.sh"
            exit 1
            ;;
        ERROR:*)
            echo ""
            echo "❌ 授权失败：$last_line"
            echo "   如问题持续，请联系腾讯问卷支持"
            exit 1
            ;;
    esac

    return 0
}

# ── 脚本入口 ──────────────────────────────────────────────────────────────────
# 直接执行时：
#   bash ./setup.sh wj_check_and_start_auth  → 第一步：检查状态 / 生成授权链接
#   bash ./setup.sh wj_wait_auth             → 第二步：等待授权完成
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ -n "$1" ]]; then
        # 参数分发：将第一个参数作为函数名执行
        case "$1" in
            wj_check_and_start_auth|wj_wait_auth)
                "$1"
                exit $?
                ;;
            setup)
                echo "🚀 腾讯问卷 MCP Skill 人工配置向导"
                echo ""
                _wj_interactive_setup
                ;;
            *)
                echo "ERROR:unknown_command - 未知命令: $1"
                echo "可用命令: wj_check_and_start_auth, wj_wait_auth, setup"
                exit 1
                ;;
        esac
    else
        echo "用法："
        echo "  bash ./setup.sh wj_check_and_start_auth   # 第一步：检查状态 / 生成授权链接"
        echo "  bash ./setup.sh wj_wait_auth              # 第二步：等待授权完成"
    fi
fi
