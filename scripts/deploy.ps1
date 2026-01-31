<#
.SYNOPSIS
    OpenAkita 一键部署脚本 (Windows PowerShell)
.DESCRIPTION
    自动完成 Python 安装、环境配置、依赖安装等全部部署流程
.NOTES
    运行方式: .\scripts\deploy.ps1
    或: powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
#>

# 严格模式
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# =====================================================
# 配置区域
# =====================================================
$PYTHON_MIN_VERSION = "3.11"
$PYTHON_DOWNLOAD_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
$PROJECT_NAME = "openakita"

# =====================================================
# 辅助函数
# =====================================================

function Write-ColorOutput {
    param(
        [string]$Message,
        [string]$Color = "White"
    )
    Write-Host $Message -ForegroundColor $Color
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "[✓] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[!] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "[✗] $Message" -ForegroundColor Red
}

function Write-Info {
    param([string]$Message)
    Write-Host "[i] $Message" -ForegroundColor Blue
}

function Test-Administrator {
    $currentUser = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $currentUser.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Compare-Version {
    param(
        [string]$Version1,
        [string]$Version2
    )
    $v1 = [Version]::Parse($Version1)
    $v2 = [Version]::Parse($Version2)
    return $v1.CompareTo($v2)
}

# =====================================================
# 主要函数
# =====================================================

function Get-PythonPath {
    # 尝试找到合适的 Python
    $pythonCommands = @("python", "python3", "python3.11", "python3.12")
    
    foreach ($cmd in $pythonCommands) {
        try {
            $output = & $cmd --version 2>&1
            if ($output -match "Python (\d+\.\d+)") {
                $version = $Matches[1]
                if ((Compare-Version $version $PYTHON_MIN_VERSION) -ge 0) {
                    Write-Success "找到 Python $version ($cmd)"
                    return $cmd
                }
            }
        } catch {
            continue
        }
    }
    
    return $null
}

function Install-Python {
    Write-Step "安装 Python $PYTHON_MIN_VERSION"
    
    # 检查是否已安装
    $pythonPath = Get-PythonPath
    if ($pythonPath) {
        Write-Success "Python 已安装且版本满足要求"
        return $pythonPath
    }
    
    Write-Info "Python 未安装或版本过低，开始安装..."
    
    # 方法1: 使用 winget
    Write-Info "尝试使用 winget 安装..."
    try {
        $result = winget install Python.Python.3.11 --accept-source-agreements --accept-package-agreements 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Success "winget 安装成功"
            # 刷新环境变量
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            return Get-PythonPath
        }
    } catch {
        Write-Warning "winget 安装失败，尝试其他方式..."
    }
    
    # 方法2: 下载安装包
    Write-Info "下载 Python 安装包..."
    $installerPath = "$env:TEMP\python-installer.exe"
    
    try {
        Invoke-WebRequest -Uri $PYTHON_DOWNLOAD_URL -OutFile $installerPath -UseBasicParsing
        Write-Success "下载完成"
        
        Write-Info "运行安装程序..."
        Start-Process -FilePath $installerPath -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0" -Wait -NoNewWindow
        
        # 刷新环境变量
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        
        $pythonPath = Get-PythonPath
        if ($pythonPath) {
            Write-Success "Python 安装成功"
            return $pythonPath
        }
    } catch {
        Write-Error "安装失败: $_"
    } finally {
        if (Test-Path $installerPath) {
            Remove-Item $installerPath -Force
        }
    }
    
    Write-Error "无法安装 Python，请手动安装 Python 3.11+"
    Write-Info "下载地址: https://www.python.org/downloads/"
    exit 1
}

function Install-Git {
    Write-Step "检查 Git"
    
    try {
        $gitVersion = git --version 2>&1
        if ($gitVersion -match "git version") {
            Write-Success "Git 已安装: $gitVersion"
            return
        }
    } catch {}
    
    Write-Info "Git 未安装，尝试安装..."
    
    try {
        winget install Git.Git --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Git 安装成功"
            # 刷新环境变量
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            return
        }
    } catch {
        Write-Warning "自动安装失败"
    }
    
    Write-Warning "请手动安装 Git: https://git-scm.com/download/win"
    Write-Warning "安装后重新运行此脚本"
}

function Initialize-VirtualEnv {
    param([string]$PythonPath)
    
    Write-Step "创建虚拟环境"
    
    $venvPath = Join-Path (Get-Location) "venv"
    
    if (Test-Path $venvPath) {
        Write-Info "虚拟环境已存在"
        
        $answer = Read-Host "是否重新创建? (y/N)"
        if ($answer -eq "y" -or $answer -eq "Y") {
            Write-Info "删除旧虚拟环境..."
            Remove-Item -Path $venvPath -Recurse -Force
        } else {
            Write-Info "使用现有虚拟环境"
            return Join-Path $venvPath "Scripts\python.exe"
        }
    }
    
    Write-Info "创建虚拟环境..."
    & $PythonPath -m venv venv
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error "创建虚拟环境失败"
        exit 1
    }
    
    Write-Success "虚拟环境创建成功"
    return Join-Path $venvPath "Scripts\python.exe"
}

function Install-Dependencies {
    param([string]$PythonPath)
    
    Write-Step "安装项目依赖"
    
    $pipPath = Join-Path (Split-Path $PythonPath) "pip.exe"
    
    # 升级 pip
    Write-Info "升级 pip..."
    & $PythonPath -m pip install --upgrade pip
    
    # 检查安装方式
    $pyprojectPath = Join-Path (Get-Location) "pyproject.toml"
    $requirementsPath = Join-Path (Get-Location) "requirements.txt"
    
    if (Test-Path $pyprojectPath) {
        Write-Info "使用 pyproject.toml 安装..."
        & $pipPath install -e .
    } elseif (Test-Path $requirementsPath) {
        Write-Info "使用 requirements.txt 安装..."
        & $pipPath install -r requirements.txt
    } else {
        Write-Error "找不到依赖配置文件"
        exit 1
    }
    
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "部分依赖安装失败，尝试使用国内镜像..."
        & $pipPath install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
    }
    
    Write-Success "依赖安装完成"
}

function Install-Playwright {
    param([string]$PythonPath)
    
    Write-Step "安装 Playwright 浏览器"
    
    $answer = Read-Host "是否安装 Playwright 浏览器内核? (Y/n)"
    if ($answer -eq "n" -or $answer -eq "N") {
        Write-Info "跳过 Playwright 安装"
        return
    }
    
    Write-Info "安装 Chromium..."
    & $PythonPath -m playwright install chromium
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Playwright 安装成功"
    } else {
        Write-Warning "Playwright 安装失败，浏览器功能可能不可用"
    }
}

function Initialize-Config {
    Write-Step "初始化配置"
    
    $envExample = Join-Path (Get-Location) ".env.example"
    $envFile = Join-Path (Get-Location) ".env"
    
    if (Test-Path $envFile) {
        Write-Info ".env 配置文件已存在"
        $answer = Read-Host "是否覆盖? (y/N)"
        if ($answer -ne "y" -and $answer -ne "Y") {
            Write-Info "保留现有配置"
            return
        }
    }
    
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile -Force
        Write-Success "配置文件已创建: .env"
    } else {
        # 创建基础配置
        $config = @"
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
"@
        Set-Content -Path $envFile -Value $config
        Write-Success "配置文件已创建: .env"
    }
    
    Write-Warning "请编辑 .env 文件，填入你的 API Key"
}

function Initialize-DataDirs {
    Write-Step "初始化数据目录"
    
    $dirs = @(
        "data",
        "data\sessions",
        "data\media",
        "skills",
        "plugins"
    )
    
    foreach ($dir in $dirs) {
        $path = Join-Path (Get-Location) $dir
        if (-not (Test-Path $path)) {
            New-Item -ItemType Directory -Path $path -Force | Out-Null
            Write-Info "创建目录: $dir"
        }
    }
    
    Write-Success "数据目录初始化完成"
}

function Test-Installation {
    param([string]$PythonPath)
    
    Write-Step "验证安装"
    
    Write-Info "检查模块导入..."
    
    $testCode = @"
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
"@
    
    $result = & $PythonPath -c $testCode 2>&1
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "安装验证通过"
    } else {
        Write-Warning "部分模块可能未正确安装: $result"
    }
}

function Show-Completion {
    param([string]$VenvPython)
    
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "        部署完成!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "后续步骤:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. 编辑配置文件，填入 API Key:" -ForegroundColor White
    Write-Host "     notepad .env" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  2. 激活虚拟环境:" -ForegroundColor White
    Write-Host "     .\venv\Scripts\Activate.ps1" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  3. 启动 Agent:" -ForegroundColor White
    Write-Host "     openakita" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  4. 启动 Telegram Bot (可选):" -ForegroundColor White
    Write-Host "     python scripts/run_telegram_bot.py" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
}

# =====================================================
# 主流程
# =====================================================

function Main {
    Write-Host ""
    Write-Host "╔════════════════════════════════════════╗" -ForegroundColor Magenta
    Write-Host "║     OpenAkita 一键部署脚本 (Windows)     ║" -ForegroundColor Magenta
    Write-Host "╚════════════════════════════════════════╝" -ForegroundColor Magenta
    Write-Host ""
    
    # 检查是否在项目目录
    $pyprojectPath = Join-Path (Get-Location) "pyproject.toml"
    if (-not (Test-Path $pyprojectPath)) {
        Write-Error "请在项目根目录运行此脚本"
        Write-Info "当前目录: $(Get-Location)"
        exit 1
    }
    
    Write-Info "项目目录: $(Get-Location)"
    Write-Info "开始部署..."
    
    # 步骤 1: 安装 Python
    $pythonPath = Install-Python
    
    # 步骤 2: 检查 Git
    Install-Git
    
    # 步骤 3: 创建虚拟环境
    $venvPython = Initialize-VirtualEnv -PythonPath $pythonPath
    
    # 步骤 4: 安装依赖
    Install-Dependencies -PythonPath $venvPython
    
    # 步骤 5: 安装 Playwright (可选)
    Install-Playwright -PythonPath $venvPython
    
    # 步骤 6: 初始化配置
    Initialize-Config
    
    # 步骤 7: 初始化数据目录
    Initialize-DataDirs
    
    # 步骤 8: 验证安装
    Test-Installation -PythonPath $venvPython
    
    # 完成
    Show-Completion -VenvPython $venvPython
}

# 运行主函数
Main
