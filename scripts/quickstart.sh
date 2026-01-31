#!/bin/bash
#
# OpenAkita Quick Start Script
# 
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.sh | bash
#
# Or download and run:
#   chmod +x quickstart.sh && ./quickstart.sh
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
cat << "EOF"
   ____                      _    _    _ _        
  / __ \                    / \  | | _(_) |_ __ _ 
 | |  | |_ __   ___ _ __   / _ \ | |/ / | __/ _` |
 | |  | | '_ \ / _ \ '_ \ / ___ \|   <| | || (_| |
 | |__| | |_) |  __/ | | /_/   \_\_|\_\_|\__\__,_|
  \____/| .__/ \___|_| |_|                        
        |_|    Your Loyal AI Companion 🐕
EOF
echo -e "${NC}"

echo -e "${CYAN}=== OpenAkita Quick Start ===${NC}\n"

# Check Python version
echo -e "${YELLOW}Checking Python version...${NC}"
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}Error: Python is not installed.${NC}"
    echo "Please install Python 3.11 or later from https://www.python.org"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.minor)")

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
    echo -e "${RED}Error: Python 3.11+ is required. Found: $PYTHON_VERSION${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python $PYTHON_VERSION${NC}\n"

# Function to install venv package for Debian/Ubuntu
install_venv_package() {
    if command -v apt &> /dev/null; then
        echo -e "${YELLOW}Installing python${PYTHON_VERSION}-venv...${NC}"
        sudo apt update
        # Try version-specific package first, then generic
        if sudo apt install -y python${PYTHON_VERSION}-venv 2>/dev/null; then
            return 0
        elif sudo apt install -y python3-venv 2>/dev/null; then
            return 0
        fi
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y python3-pip python3-devel
        return 0
    elif command -v yum &> /dev/null; then
        sudo yum install -y python3-pip python3-devel
        return 0
    fi
    return 1
}

# Create virtual environment (with auto-install of venv package if needed)
echo -e "${YELLOW}Creating virtual environment...${NC}"

# Check if venv exists AND is valid (has activate script)
if [ -d ".venv" ] && [ ! -f ".venv/bin/activate" ]; then
    echo -e "${YELLOW}Found incomplete venv, removing and recreating...${NC}"
    rm -rf .venv
fi

if [ ! -d ".venv" ]; then
    # Try to create venv, install package if it fails
    if ! $PYTHON_CMD -m venv .venv 2>/dev/null; then
        echo -e "${YELLOW}venv creation failed. Installing required packages...${NC}"
        if install_venv_package; then
            # Retry venv creation after installing package
            if ! $PYTHON_CMD -m venv .venv; then
                echo -e "${RED}Error: Failed to create virtual environment.${NC}"
                echo "Please install python${PYTHON_VERSION}-venv manually:"
                echo "  sudo apt install python${PYTHON_VERSION}-venv"
                exit 1
            fi
        else
            echo -e "${RED}Error: Cannot install venv package automatically.${NC}"
            echo "Please install python${PYTHON_VERSION}-venv manually for your distribution."
            exit 1
        fi
    fi
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${GREEN}✓ Virtual environment already exists${NC}"
fi

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source .venv/bin/activate
echo -e "${GREEN}✓ Virtual environment activated${NC}\n"

# Check pip inside venv (venv should include pip, but may need ensurepip)
echo -e "${YELLOW}Checking pip in virtual environment...${NC}"
if ! python -m pip --version &> /dev/null; then
    echo -e "${YELLOW}pip not found in venv. Installing pip...${NC}"
    
    # Try ensurepip first
    if python -m ensurepip --upgrade &> /dev/null; then
        echo -e "${GREEN}✓ pip installed via ensurepip${NC}"
    else
        # Fallback to get-pip.py (safe inside venv)
        echo -e "${YELLOW}Downloading get-pip.py...${NC}"
        if command -v curl &> /dev/null; then
            curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
        elif command -v wget &> /dev/null; then
            wget -q https://bootstrap.pypa.io/get-pip.py -O /tmp/get-pip.py
        else
            echo -e "${RED}Error: Neither curl nor wget is available to download pip.${NC}"
            exit 1
        fi
        
        python /tmp/get-pip.py
        rm -f /tmp/get-pip.py
        
        if ! python -m pip --version &> /dev/null; then
            echo -e "${RED}Error: Failed to install pip in virtual environment.${NC}"
            exit 1
        fi
        echo -e "${GREEN}✓ pip installed via get-pip.py${NC}"
    fi
fi
echo -e "${GREEN}✓ pip is available${NC}\n"

# Install OpenAkita
echo -e "${YELLOW}Installing OpenAkita...${NC}"
pip install --upgrade pip > /dev/null 2>&1

# Install CPU-only PyTorch first (saves ~2GB disk space)
echo -e "${YELLOW}Installing PyTorch (CPU-only)...${NC}"
pip install torch --index-url https://download.pytorch.org/whl/cpu
echo -e "${GREEN}✓ PyTorch (CPU) installed${NC}"

pip install openakita
echo -e "${GREEN}✓ OpenAkita installed${NC}\n"

# Install Playwright browsers (for web tools)
echo -e "${YELLOW}Installing Playwright browsers...${NC}"
playwright install chromium > /dev/null 2>&1 || true
echo -e "${GREEN}✓ Playwright ready${NC}\n"

echo -e "${GREEN}=== Installation Complete ===${NC}\n"

# Run setup wizard (redirect stdin from /dev/tty for pipe execution)
echo -e "${CYAN}Starting setup wizard...${NC}\n"
if [ -t 0 ]; then
    # Running interactively
    openakita init
else
    # Running via pipe (curl | bash), need to redirect stdin
    exec < /dev/tty
    openakita init
fi

echo -e "\n${GREEN}Setup complete!${NC}"
echo -e "To start OpenAkita, run: ${CYAN}source ~/.venv/bin/activate && openakita${NC}"
echo -e "Or run as service (Telegram/IM): ${CYAN}source ~/.venv/bin/activate && openakita serve${NC}\n"
