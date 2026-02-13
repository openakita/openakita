#!/bin/bash
#
# OpenAkita 一键部署脚本 (Linux/macOS)
#
# 使用方式:
#   chmod +x scripts/deploy.sh
#   ./scripts/deploy.sh
#
# 支持系统:
#   - Ubuntu 20.04/22.04/24.04
#   - Debian 11/12
#   - CentOS 8/9
#   - macOS 12+
#

set -e  # 遇错退出

# =====================================================
# 配置区域
# =====================================================
PYTHON_MIN_VERSION="3.11"
PROJECT_NAME="openakita"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# =====================================================
# 辅助函数
# =====================================================

print_step() {
    echo ""
    echo -e "${CYAN}========================================"
    echo -e "  $1"
    echo -e "========================================${NC}"
}

print_success() {
    echo -e "${GREEN}[✓] $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}[!] $1${NC}"
}

print_error() {
    echo -e "${RED}[✗] $1${NC}"
}

print_info() {
    echo -e "${BLUE}[i] $1${NC}"
}

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 版本比较 (返回 0 表示 $1 >= $2)
version_gte() {
    [ "$(printf '%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

# 检测操作系统
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        PKG_MANAGER="brew"
    elif [ -f /etc/os-release ]; then
        . /etc/os-release
        case "$ID" in
            ubuntu|debian)
                OS="debian"
                PKG_MANAGER="apt"
                ;;
            centos|rhel|rocky|almalinux)
                OS="rhel"
                PKG_MANAGER="dnf"
                ;;
            fedora)
                OS="fedora"
                PKG_MANAGER="dnf"
                ;;
            arch|manjaro)
                OS="arch"
                PKG_MANAGER="pacman"
                ;;
            *)
                OS="unknown"
                PKG_MANAGER="unknown"
                ;;
        esac
    else
        OS="unknown"
        PKG_MANAGER="unknown"
    fi
    
    print_info "检测到操作系统: $OS (包管理器: $PKG_MANAGER)"
}

# =====================================================
# 主要函数
# =====================================================

# 查找 Python
find_python() {
    local python_cmds=("python3.12" "python3.11" "python3" "python")
    
    for cmd in "${python_cmds[@]}"; do
        if command_exists "$cmd"; then
            local version=$($cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
            if version_gte "$version" "$PYTHON_MIN_VERSION"; then
                print_success "找到 Python $version ($cmd)"
                PYTHON_CMD="$cmd"
                return 0
            fi
        fi
    done
    
    return 1
}

# 安装 Python
install_python() {
    print_step "安装 Python $PYTHON_MIN_VERSION"
    
    # 检查是否已安装
    if find_python; then
        print_success "Python 已安装且版本满足要求"
        return 0
    fi
    
    print_info "Python 未安装或版本过低，开始安装..."
    
    case "$OS" in
        debian)
            print_info "使用 apt 安装..."
            sudo apt update
            
            # 尝试安装 python3.11
            if apt-cache show python3.11 >/dev/null 2>&1; then
                sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip
            elif apt-cache show python3.12 >/dev/null 2>&1; then
                sudo apt install -y python3.12 python3.12-venv python3.12-dev python3-pip
            else
                # 添加 deadsnakes PPA (Ubuntu)
                print_info "添加 deadsnakes PPA..."
                sudo apt install -y software-properties-common
                sudo add-apt-repository -y ppa:deadsnakes/ppa
                sudo apt update
                sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip
            fi
            ;;
        rhel|fedora)
            print_info "使用 dnf 安装..."
            sudo dnf install -y python3.11 python3.11-pip python3.11-devel || \
            sudo dnf install -y python3.12 python3.12-pip python3.12-devel
            ;;
        arch)
            print_info "使用 pacman 安装..."
            sudo pacman -Sy --noconfirm python python-pip
            ;;
        macos)
            print_info "使用 Homebrew 安装..."
            if ! command_exists brew; then
                print_error "请先安装 Homebrew: https://brew.sh"
                exit 1
            fi
            brew install python@3.11
            ;;
        *)
            print_error "不支持的操作系统: $OS"
            print_info "请手动安装 Python 3.11+: https://www.python.org/downloads/"
            exit 1
            ;;
    esac
    
    # 验证安装
    if find_python; then
        print_success "Python 安装成功"
    else
        print_error "Python 安装失败"
        exit 1
    fi
}

# 安装 Git
install_git() {
    print_step "检查 Git"
    
    if command_exists git; then
        print_success "Git 已安装: $(git --version)"
        return 0
    fi
    
    print_info "安装 Git..."
    
    case "$OS" in
        debian)
            sudo apt install -y git
            ;;
        rhel|fedora)
            sudo dnf install -y git
            ;;
        arch)
            sudo pacman -Sy --noconfirm git
            ;;
        macos)
            brew install git
            ;;
    esac
    
    print_success "Git 安装完成"
}

# 创建虚拟环境
create_venv() {
    print_step "创建虚拟环境"
    
    VENV_PATH="$(pwd)/venv"
    
    if [ -d "$VENV_PATH" ]; then
        print_info "虚拟环境已存在"
        read -p "是否重新创建? (y/N): " answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            print_info "删除旧虚拟环境..."
            rm -rf "$VENV_PATH"
        else
            print_info "使用现有虚拟环境"
            VENV_PYTHON="$VENV_PATH/bin/python"
            return 0
        fi
    fi
    
    print_info "创建虚拟环境..."
    $PYTHON_CMD -m venv venv
    
    VENV_PYTHON="$VENV_PATH/bin/python"
    VENV_PIP="$VENV_PATH/bin/pip"
    
    print_success "虚拟环境创建成功"
}

# 安装依赖
install_dependencies() {
    print_step "安装项目依赖"
    
    echo ""
    echo -e "${YELLOW}╔══════════════════════════════════════════════════════════╗"
    echo -e "║  ⏳ 此步骤需要下载并安装大量 Python 依赖包               ║"
    echo -e "║  根据网络状况，可能需要 5~15 分钟，请耐心等待...         ║"
    echo -e "║  如果安装失败，脚本会自动回退到清华镜像源重试            ║"
    echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    # 升级 pip
    print_info "升级 pip..."
    $VENV_PIP install --upgrade pip
    
    # 检查安装方式
    if [ -f "pyproject.toml" ]; then
        print_info "使用 pyproject.toml 安装..."
        $VENV_PIP install -e . || {
            print_warning "安装失败，尝试使用国内镜像..."
            $VENV_PIP install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
        }
    elif [ -f "requirements.txt" ]; then
        print_info "使用 requirements.txt 安装..."
        $VENV_PIP install -r requirements.txt || {
            print_warning "安装失败，尝试使用国内镜像..."
            $VENV_PIP install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        }
    else
        print_error "找不到依赖配置文件"
        exit 1
    fi
    
    print_success "依赖安装完成"
}

# 安装 Playwright
install_playwright() {
    print_step "安装 Playwright 浏览器"
    
    read -p "是否安装 Playwright 浏览器内核? (Y/n): " answer
    if [[ "$answer" =~ ^[Nn]$ ]]; then
        print_info "跳过 Playwright 安装"
        return 0
    fi
    
    print_info "安装 Chromium..."
    $VENV_PYTHON -m playwright install chromium
    
    # 安装系统依赖 (Linux)
    if [[ "$OS" != "macos" ]]; then
        print_info "安装 Playwright 系统依赖..."
        $VENV_PYTHON -m playwright install-deps chromium || {
            print_warning "系统依赖安装可能需要 sudo 权限"
            sudo $VENV_PYTHON -m playwright install-deps chromium || true
        }
    fi
    
    print_success "Playwright 安装完成"
}

# 安装 Whisper 语音模型
install_whisper_model() {
    print_step "预下载 Whisper 语音模型"
    
    # 检查是否已安装 whisper
    if ! $VENV_PYTHON -c "import whisper" 2>/dev/null; then
        print_warning "Whisper 未安装，跳过模型下载"
        return 0
    fi
    
    # 模型选项
    print_info "Whisper 语音识别模型选项:"
    echo "  1. tiny   - 最小 (~39MB)  - 速度最快，准确度较低"
    echo "  2. base   - 基础 (~74MB)  - 推荐，平衡速度和准确度"
    echo "  3. small  - 小型 (~244MB) - 较高准确度"
    echo "  4. medium - 中型 (~769MB) - 高准确度"
    echo "  5. large  - 大型 (~1.5GB) - 最高准确度，需要较多资源"
    echo "  0. 跳过   - 不下载，首次使用时再下载"
    echo ""
    
    read -p "请选择模型 (默认 2-base): " choice
    choice=${choice:-2}
    
    local model_name
    case $choice in
        1) model_name="tiny" ;;
        2) model_name="base" ;;
        3) model_name="small" ;;
        4) model_name="medium" ;;
        5) model_name="large" ;;
        0) print_info "跳过 Whisper 模型下载"; return 0 ;;
        *) model_name="base" ;;
    esac
    
    # 询问语言（英语时自动使用 .en 模型，更小更快）
    echo ""
    print_info "语音识别语言选项:"
    echo "  1. zh   - 中文（使用多语言模型）"
    echo "  2. en   - 英文（自动切换为更小更快的 .en 专用模型）"
    echo "  3. auto - 自动检测语言"
    echo ""
    read -p "请选择语言 (默认 1-zh): " lang_choice
    lang_choice=${lang_choice:-1}

    local whisper_lang
    case $lang_choice in
        1) whisper_lang="zh" ;;
        2) whisper_lang="en" ;;
        3) whisper_lang="auto" ;;
        *) whisper_lang="zh" ;;
    esac

    # 英语且模型有 .en 变体时，切换到 .en 模型
    local actual_model="$model_name"
    if [[ "$whisper_lang" == "en" ]] && [[ "$model_name" != "large" ]]; then
        actual_model="${model_name}.en"
        print_info "英语模式 → 使用 $actual_model 专用模型（更小更快）"
    fi

    # 检查模型是否已存在
    local cache_dir="$HOME/.cache/whisper"
    local model_file="$cache_dir/${actual_model}.pt"
    
    if [[ -f "$model_file" ]] && [[ $(stat -f%z "$model_file" 2>/dev/null || stat -c%s "$model_file" 2>/dev/null) -gt 1000000 ]]; then
        print_info "Whisper $actual_model 模型已存在，跳过下载"
        return 0
    fi
    
    print_info "下载 Whisper $actual_model 模型..."
    
    $VENV_PYTHON -c "
import whisper
print('正在下载...')
whisper.load_model('$actual_model')
print('完成!')
" && print_success "Whisper $actual_model 模型下载成功" || print_warning "Whisper 模型下载失败，语音识别功能将在首次使用时下载"
}

# 初始化配置
init_config() {
    print_step "初始化配置"
    
    # 1. 基础环境配置 (.env)
    if [ -f ".env" ]; then
        print_info ".env 配置文件已存在"
        read -p "是否覆盖? (Y/n): " answer
        if [[ "$answer" =~ ^[Nn]$ ]]; then
            print_info "保留现有 .env 配置"
        else
            create_env_file
        fi
    else
        create_env_file
    fi
    
    # 2. LLM 端点配置 (data/llm_endpoints.json)
    init_llm_endpoints
    
    # 3. Identity 模板文件
    init_identity_templates
    
    print_warning "请编辑配置文件:"
    print_info "  - .env: 基础设置 (Telegram Token 等)"
    print_info "  - data/llm_endpoints.json: LLM 端点配置 (API Key, 模型等)"
    print_info "  - identity/SOUL.md: Agent 身份与核心特质"
}

init_identity_templates() {
    print_info "初始化 Identity 模板..."
    mkdir -p identity
    
    local templates=("SOUL" "AGENT" "USER" "MEMORY")
    for name in "${templates[@]}"; do
        local target="identity/${name}.md"
        local example="identity/${name}.md.example"
        if [ ! -f "$target" ] && [ -f "$example" ]; then
            cp "$example" "$target"
            print_success "已创建 identity/${name}.md (从 example 复制)"
        fi
    done
    
    # 如果 SOUL.md 仍不存在（没有 example），创建基础模板
    if [ ! -f "identity/SOUL.md" ]; then
        cat > "identity/SOUL.md" << 'SOULEOF'
# Agent Soul

你是 OpenAkita，一个忠诚可靠的 AI 助手。

## 核心特质
- 永不放弃，持续尝试直到成功
- 诚实可靠，不会隐瞒问题
- 主动学习，不断自我改进

## 行为准则
- 优先考虑用户的真实需求
- 遇到困难时寻找替代方案
- 保持简洁清晰的沟通方式
SOULEOF
        print_success "已创建 identity/SOUL.md (默认模板)"
    fi
}

create_env_file() {
    if [ -f "examples/.env.example" ]; then
        cp examples/.env.example .env
        print_success "配置文件已创建: .env"
    else
        cat > .env << 'EOF'
# =====================================================
# OpenAkita 基础配置
# =====================================================

# LLM API（推荐使用 data/llm_endpoints.json 管理多端点）
ANTHROPIC_API_KEY=
ANTHROPIC_BASE_URL=https://api.anthropic.com
DEFAULT_MODEL=claude-opus-4-5-20251101-thinking
MAX_TOKENS=8192

# Agent 配置
AGENT_NAME=OpenAkita
MAX_ITERATIONS=100
AUTO_CONFIRM=false

# Thinking 模式（auto/always/never）
# THINKING_MODE=auto

# 数据库 & 日志
DATABASE_PATH=data/agent.db
LOG_LEVEL=INFO

# =====================================================
# IM 通道（启用后填写对应密钥）
# =====================================================
TELEGRAM_ENABLED=false
# TELEGRAM_BOT_TOKEN=
# TELEGRAM_PROXY=

FEISHU_ENABLED=false
# FEISHU_APP_ID=
# FEISHU_APP_SECRET=

WEWORK_ENABLED=false
# WEWORK_CORP_ID=
# WEWORK_AGENT_ID=
# WEWORK_SECRET=

DINGTALK_ENABLED=false
# DINGTALK_CLIENT_ID=
# DINGTALK_CLIENT_SECRET=

ONEBOT_ENABLED=false
# ONEBOT_WS_URL=ws://127.0.0.1:8080

QQBOT_ENABLED=false
# QQBOT_APP_ID=
# QQBOT_APP_SECRET=

# =====================================================
# 功能开关（可选）
# =====================================================
PERSONA_NAME=default
STICKER_ENABLED=true
PROACTIVE_ENABLED=false
SCHEDULER_ENABLED=true
# SCHEDULER_TIMEZONE=Asia/Shanghai
ORCHESTRATION_ENABLED=false

# 记忆
EMBEDDING_MODEL=shibing624/text2vec-base-chinese
EMBEDDING_DEVICE=cpu
# 模型下载源: auto | huggingface | hf-mirror | modelscope
MODEL_DOWNLOAD_SOURCE=auto

# 会话
# SESSION_TIMEOUT_MINUTES=30
# SESSION_MAX_HISTORY=50

# 网络代理（可选）
# HTTP_PROXY=http://127.0.0.1:7890
# HTTPS_PROXY=http://127.0.0.1:7890

# 语音
WHISPER_MODEL=base
# 语音识别语言: zh(中文) | en(英文,自动使用.en模型) | auto(自动检测)
WHISPER_LANGUAGE=zh

# GitHub
# GITHUB_TOKEN=

# =====================================================
# LLM 端点配置
# =====================================================
# 注意: LLM 相关配置已迁移到 data/llm_endpoints.json
# 支持多端点、自动故障切换、能力路由
# 运行 openakita llm-config 进行交互式配置
EOF
        print_success "配置文件已创建: .env"
    fi
}

init_llm_endpoints() {
    local llm_config="data/llm_endpoints.json"
    local llm_example="data/llm_endpoints.json.example"
    
    if [ -f "$llm_config" ]; then
        print_info "LLM 端点配置已存在: $llm_config"
        return 0
    fi
    
    print_info "创建 LLM 端点配置..."
    mkdir -p data
    
    # 如果 example 文件存在，则复制它
    if [ -f "$llm_example" ]; then
        cp "$llm_example" "$llm_config"
        print_success "LLM 端点配置已创建: $llm_config (从 example 复制)"
    else
        # 生成空端点配置（由用户通过 Setup Center 或 llm-config 添加端点）
        cat > "$llm_config" << 'EOF'
{
  "endpoints": [],
  "compiler_endpoints": [],
  "settings": {
    "retry_count": 2,
    "retry_delay_seconds": 2,
    "health_check_interval": 60,
    "fallback_on_error": true
  }
}
EOF
        print_success "LLM 端点配置已创建: $llm_config"
    fi
    print_info "提示: 通过 Setup Center 或 openakita llm-config 添加 LLM 端点"
    print_info "提示: 可添加多个端点实现自动故障切换"
}

# 初始化数据目录
init_data_dirs() {
    print_step "初始化数据目录"
    
    local dirs=(
        "data"
        "data/sessions"
        "data/media"
        "data/scheduler"
        "data/temp"
        "data/telegram/pairing"
        "data/sticker"
        "identity"
        "skills"
        "plugins"
        "logs"
    )
    
    for dir in "${dirs[@]}"; do
        if [ ! -d "$dir" ]; then
            mkdir -p "$dir"
            print_info "创建目录: $dir"
        fi
    done
    
    print_success "数据目录初始化完成"
}

# 验证安装
verify_installation() {
    print_step "验证安装"
    
    print_info "检查模块导入..."
    
    $VENV_PYTHON << 'EOF'
import sys
try:
    import anthropic
    import rich
    import typer
    import httpx
    import pydantic
    print('SUCCESS: 所有核心模块导入成功')
    sys.exit(0)
except ImportError as e:
    print(f'FAILED: {e}')
    sys.exit(1)
EOF
    
    if [ $? -eq 0 ]; then
        print_success "安装验证通过"
    else
        print_warning "部分模块可能未正确安装"
    fi
}

# 创建 systemd 服务文件
create_systemd_service() {
    print_step "创建 systemd 服务 (可选)"
    
    if [[ "$OS" == "macos" ]]; then
        print_info "macOS 不使用 systemd，跳过"
        return 0
    fi
    
    read -p "是否创建 systemd 服务? (y/N): " answer
    if [[ ! "$answer" =~ ^[Yy]$ ]]; then
        print_info "跳过 systemd 服务创建"
        return 0
    fi
    
    local service_content="[Unit]
Description=OpenAkita Telegram Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment=\"PATH=$(pwd)/venv/bin\"
ExecStart=$(pwd)/venv/bin/python scripts/run_telegram_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target"
    
    local service_file="openakita.service"
    echo "$service_content" > "$service_file"
    print_success "服务文件已创建: $service_file"
    
    print_info "安装服务的命令:"
    echo "  sudo cp $service_file /etc/systemd/system/"
    echo "  sudo systemctl daemon-reload"
    echo "  sudo systemctl enable openakita"
    echo "  sudo systemctl start openakita"
}

# 显示完成信息
show_completion() {
    echo ""
    echo -e "${GREEN}========================================"
    echo -e "        部署完成!"
    echo -e "========================================${NC}"
    echo ""
    echo -e "${YELLOW}后续步骤:${NC}"
    echo ""
    echo -e "  1. 配置 LLM 端点 (二选一):"
    echo -e "     ${CYAN}openakita llm-config${NC}  # 交互式配置向导"
    echo -e "     ${CYAN}nano data/llm_endpoints.json${NC}  # 直接编辑"
    echo ""
    echo -e "  2. (可选) 配置 Telegram:"
    echo -e "     ${CYAN}nano .env${NC}  # 填入 TELEGRAM_BOT_TOKEN"
    echo ""
    echo -e "  3. 激活虚拟环境:"
    echo -e "     ${CYAN}source venv/bin/activate${NC}"
    echo ""
    echo -e "  4. 启动 Agent:"
    echo -e "     ${CYAN}openakita${NC}        # 交互模式"
    echo -e "     ${CYAN}openakita serve${NC}  # 服务模式 (Telegram/IM)"
    echo ""
    echo -e "${BLUE}新特性:${NC}"
    echo -e "  - 多 LLM 端点支持，自动故障切换"
    echo -e "  - 端点 3 分钟冷静期机制"
    echo -e "  - 能力路由 (text/vision/video/tools)"
    echo ""
    echo -e "${GREEN}========================================${NC}"
}

# =====================================================
# 主流程
# =====================================================

main() {
    echo ""
    echo -e "${MAGENTA}╔════════════════════════════════════════╗"
    echo -e "║   OpenAkita 一键部署脚本 (Linux/macOS)   ║"
    echo -e "╚════════════════════════════════════════╝${NC}"
    echo ""
    
    # 检查是否在项目目录
    if [ ! -f "pyproject.toml" ]; then
        print_error "请在项目根目录运行此脚本"
        print_info "当前目录: $(pwd)"
        exit 1
    fi
    
    print_info "项目目录: $(pwd)"
    print_info "开始部署..."
    
    # 检测操作系统
    detect_os
    
    # 步骤 1: 安装 Python
    install_python
    
    # 步骤 2: 安装 Git
    install_git
    
    # 步骤 3: 创建虚拟环境
    create_venv
    
    # 步骤 4: 安装依赖
    install_dependencies
    
    # 步骤 5: 安装 Playwright (可选)
    install_playwright
    
    # 步骤 6: 下载 Whisper 语音模型 (可选)
    install_whisper_model
    
    # 步骤 7: 初始化配置
    init_config
    
    # 步骤 8: 初始化数据目录
    init_data_dirs
    
    # 步骤 9: 验证安装
    verify_installation
    
    # 步骤 10: 创建 systemd 服务 (可选)
    create_systemd_service
    
    # 完成
    show_completion
}

# 运行主函数
main "$@"
