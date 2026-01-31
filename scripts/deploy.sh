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

# 初始化配置
init_config() {
    print_step "初始化配置"
    
    if [ -f ".env" ]; then
        print_info ".env 配置文件已存在"
        read -p "是否覆盖? (y/N): " answer
        if [[ ! "$answer" =~ ^[Yy]$ ]]; then
            print_info "保留现有配置"
            return 0
        fi
    fi
    
    if [ -f ".env.example" ]; then
        cp .env.example .env
        print_success "配置文件已创建: .env"
    else
        # 创建基础配置
        cat > .env << 'EOF'
# Anthropic API Key (必需)
ANTHROPIC_API_KEY=sk-your-api-key-here

# API Base URL
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 模型配置
DEFAULT_MODEL=claude-opus-4-5-20251101-thinking
MAX_TOKENS=8192

# Agent配置
AGENT_NAME=OpenAkita
MAX_ITERATIONS=100
AUTO_CONFIRM=false

# 数据库路径
DATABASE_PATH=data/agent.db

# 日志级别
LOG_LEVEL=INFO

# Telegram (可选)
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=your-bot-token
EOF
        print_success "配置文件已创建: .env"
    fi
    
    print_warning "请编辑 .env 文件，填入你的 API Key"
}

# 初始化数据目录
init_data_dirs() {
    print_step "初始化数据目录"
    
    local dirs=("data" "data/sessions" "data/media" "skills" "plugins")
    
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
    echo -e "  1. 编辑配置文件，填入 API Key:"
    echo -e "     ${CYAN}nano .env${NC}  或  ${CYAN}vim .env${NC}"
    echo ""
    echo -e "  2. 激活虚拟环境:"
    echo -e "     ${CYAN}source venv/bin/activate${NC}"
    echo ""
    echo -e "  3. 启动 Agent:"
    echo -e "     ${CYAN}openakita${NC}"
    echo ""
    echo -e "  4. 启动 Telegram Bot (可选):"
    echo -e "     ${CYAN}python scripts/run_telegram_bot.py${NC}"
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
    
    # 步骤 6: 初始化配置
    init_config
    
    # 步骤 7: 初始化数据目录
    init_data_dirs
    
    # 步骤 8: 验证安装
    verify_installation
    
    # 步骤 9: 创建 systemd 服务 (可选)
    create_systemd_service
    
    # 完成
    show_completion
}

# 运行主函数
main "$@"
