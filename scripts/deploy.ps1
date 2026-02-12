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
    
    Write-Host ""
    Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Yellow
    Write-Host "║  ⏳ 此步骤需要下载并安装大量 Python 依赖包               ║" -ForegroundColor Yellow
    Write-Host "║  根据网络状况，可能需要 5~15 分钟，请耐心等待...         ║" -ForegroundColor Yellow
    Write-Host "║  如果安装失败，脚本会自动回退到清华镜像源重试            ║" -ForegroundColor Yellow
    Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Yellow
    Write-Host ""
    
    $pipPath = Join-Path (Split-Path $PythonPath) "pip.exe"
    
    # 升级 pip
    Write-Info "升级 pip..."
    & $PythonPath -m pip install --upgrade pip
    
    # 检查安装方式
    $pyprojectPath = Join-Path (Get-Location) "pyproject.toml"
    $requirementsPath = Join-Path (Get-Location) "requirements.txt"
    
    $usePyproject = $false
    if (Test-Path $pyprojectPath) {
        Write-Info "使用 pyproject.toml 安装..."
        & $pipPath install -e .
        $usePyproject = $true
    } elseif (Test-Path $requirementsPath) {
        Write-Info "使用 requirements.txt 安装..."
        & $pipPath install -r requirements.txt
    } else {
        Write-Error "找不到依赖配置文件"
        exit 1
    }
    
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "部分依赖安装失败，尝试使用国内镜像..."
        if ($usePyproject) {
            & $pipPath install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
        } else {
            & $pipPath install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        }
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

function Install-WhisperModel {
    param([string]$PythonPath)
    
    Write-Step "预下载 Whisper 语音模型"
    
    # 检查是否已安装 whisper
    $whisperCheck = & $PythonPath -c "import whisper; print('ok')" 2>$null
    if ($whisperCheck -ne "ok") {
        Write-Warning "Whisper 未安装，跳过模型下载"
        return
    }
    
    # 模型选项
    Write-Info "Whisper 语音识别模型选项:"
    Write-Info "  1. tiny   - 最小 (~39MB)  - 速度最快，准确度较低"
    Write-Info "  2. base   - 基础 (~74MB)  - 推荐，平衡速度和准确度"
    Write-Info "  3. small  - 小型 (~244MB) - 较高准确度"
    Write-Info "  4. medium - 中型 (~769MB) - 高准确度"
    Write-Info "  5. large  - 大型 (~1.5GB) - 最高准确度，需要较多资源"
    Write-Info "  0. 跳过   - 不下载，首次使用时再下载"
    Write-Host ""
    
    $choice = Read-Host "请选择模型 (默认 2-base)"
    if ([string]::IsNullOrWhiteSpace($choice)) { $choice = "2" }
    
    $modelName = switch ($choice) {
        "1" { "tiny" }
        "2" { "base" }
        "3" { "small" }
        "4" { "medium" }
        "5" { "large" }
        "0" { $null }
        default { "base" }
    }
    
    if ($null -eq $modelName) {
        Write-Info "跳过 Whisper 模型下载"
        return
    }
    
    # 询问语言（英语时自动使用 .en 模型，更小更快）
    Write-Host ""
    Write-Info "语音识别语言选项:"
    Write-Info "  1. zh   - 中文（使用多语言模型）"
    Write-Info "  2. en   - 英文（自动切换为更小更快的 .en 专用模型）"
    Write-Info "  3. auto - 自动检测语言"
    Write-Host ""
    $langChoice = Read-Host "请选择语言 (默认 1-zh)"
    if ([string]::IsNullOrWhiteSpace($langChoice)) { $langChoice = "1" }
    
    $whisperLang = switch ($langChoice) {
        "1" { "zh" }
        "2" { "en" }
        "3" { "auto" }
        default { "zh" }
    }
    
    # 英语且模型有 .en 变体时，切换到 .en 模型
    $actualModel = $modelName
    if ($whisperLang -eq "en" -and $modelName -ne "large") {
        $actualModel = "$modelName.en"
        Write-Info "英语模式 -> 使用 $actualModel 专用模型（更小更快）"
    }
    
    # 检查模型是否已存在
    $cacheDir = Join-Path $env:USERPROFILE ".cache\whisper"
    $modelFile = Join-Path $cacheDir "$actualModel.pt"
    
    if ((Test-Path $modelFile) -and (Get-Item $modelFile).Length -gt 1000000) {
        Write-Info "Whisper $actualModel 模型已存在，跳过下载"
        return
    }
    
    Write-Info "下载 Whisper $actualModel 模型..."
    
    # 使用 Python 下载模型
    $downloadScript = "import whisper; print('正在下载...'); whisper.load_model('$actualModel'); print('完成!')"
    
    & $PythonPath -c $downloadScript
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Whisper $actualModel 模型下载成功"
    } else {
        Write-Warning "Whisper 模型下载失败，语音识别功能将在首次使用时下载"
    }
}

function Initialize-Config {
    Write-Step "初始化配置"
    
    $envExample = Join-Path (Get-Location) "examples\.env.example"
    $envFile = Join-Path (Get-Location) ".env"
    
    # 1. 基础环境配置 (.env)
    if (Test-Path $envFile) {
        Write-Info ".env 配置文件已存在"
        $answer = Read-Host "是否覆盖? (Y/n)"
        if ($answer -eq "n" -or $answer -eq "N") {
            Write-Info "保留现有 .env 配置"
        } else {
            New-EnvFile -EnvFile $envFile -EnvExample $envExample
        }
    } else {
        New-EnvFile -EnvFile $envFile -EnvExample $envExample
    }
    
    # 2. LLM 端点配置 (data/llm_endpoints.json)
    Initialize-LLMEndpoints
    
    # 3. Identity 模板文件
    Initialize-IdentityTemplates
    
    Write-Warning "请编辑配置文件:"
    Write-Info "  - .env: 基础设置 (Telegram Token 等)"
    Write-Info "  - data\llm_endpoints.json: LLM 端点配置 (API Key, 模型等)"
    Write-Info "  - identity\SOUL.md: Agent 身份与核心特质"
}

function Initialize-IdentityTemplates {
    Write-Info "初始化 Identity 模板..."
    
    $identityDir = Join-Path (Get-Location) "identity"
    if (-not (Test-Path $identityDir)) {
        New-Item -ItemType Directory -Path $identityDir -Force | Out-Null
    }
    
    $templates = @("SOUL", "AGENT", "USER", "MEMORY")
    foreach ($name in $templates) {
        $target = Join-Path $identityDir "$name.md"
        $example = Join-Path $identityDir "$name.md.example"
        if ((-not (Test-Path $target)) -and (Test-Path $example)) {
            Copy-Item $example $target
            Write-Success "已创建 identity\$name.md (从 example 复制)"
        }
    }
    
    # 如果 SOUL.md 仍不存在（没有 example），创建基础模板
    $soulPath = Join-Path $identityDir "SOUL.md"
    if (-not (Test-Path $soulPath)) {
        $soulContent = @"
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
"@
        Set-Content -Path $soulPath -Value $soulContent -Encoding UTF8
        Write-Success "已创建 identity\SOUL.md (默认模板)"
    }
}

function New-EnvFile {
    param(
        [string]$EnvFile,
        [string]$EnvExample
    )
    
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile -Force
        Write-Success "配置文件已创建: .env"
    } else {
        $config = @"
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

QQ_ENABLED=false
# QQ_ONEBOT_URL=ws://127.0.0.1:8080

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
"@
        Set-Content -Path $EnvFile -Value $config
        Write-Success "配置文件已创建: .env"
    }
}

function Initialize-LLMEndpoints {
    $llmConfig = Join-Path (Get-Location) "data\llm_endpoints.json"
    $llmExample = Join-Path (Get-Location) "data\llm_endpoints.json.example"
    
    if (Test-Path $llmConfig) {
        Write-Info "LLM 端点配置已存在: $llmConfig"
        return
    }
    
    Write-Info "创建 LLM 端点配置..."
    
    # 确保 data 目录存在
    $dataDir = Join-Path (Get-Location) "data"
    if (-not (Test-Path $dataDir)) {
        New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    }
    
    # 如果 example 文件存在，则复制它
    if (Test-Path $llmExample) {
        Copy-Item $llmExample $llmConfig
        Write-Success "LLM 端点配置已创建: $llmConfig (从 example 复制)"
    } else {
        # 生成空端点配置（由用户通过 Setup Center 或 llm-config 添加端点）
        $llmConfigContent = @"
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
"@
        Set-Content -Path $llmConfig -Value $llmConfigContent -Encoding UTF8
        Write-Success "LLM 端点配置已创建: $llmConfig"
    }
    Write-Info "提示: 通过 Setup Center 或 openakita llm-config 添加 LLM 端点"
    Write-Info "提示: 可添加多个端点实现自动故障切换"
}

function Initialize-DataDirs {
    Write-Step "初始化数据目录"
    
    $dirs = @(
        "data",
        "data\sessions",
        "data\media",
        "data\scheduler",
        "data\temp",
        "data\telegram\pairing",
        "data\sticker",
        "identity",
        "skills",
        "plugins",
        "logs"
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
    Write-Host "  1. 配置 LLM 端点 (二选一):" -ForegroundColor White
    Write-Host "     openakita llm-config" -ForegroundColor Cyan -NoNewline
    Write-Host "  # 交互式配置向导" -ForegroundColor Gray
    Write-Host "     notepad data\llm_endpoints.json" -ForegroundColor Cyan -NoNewline
    Write-Host "  # 直接编辑" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  2. (可选) 配置 Telegram:" -ForegroundColor White
    Write-Host "     notepad .env" -ForegroundColor Cyan -NoNewline
    Write-Host "  # 填入 TELEGRAM_BOT_TOKEN" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  3. 激活虚拟环境:" -ForegroundColor White
    Write-Host "     .\venv\Scripts\Activate.ps1" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  4. 启动 Agent:" -ForegroundColor White
    Write-Host "     openakita" -ForegroundColor Cyan -NoNewline
    Write-Host "        # 交互模式" -ForegroundColor Gray
    Write-Host "     openakita serve" -ForegroundColor Cyan -NoNewline
    Write-Host "  # 服务模式 (Telegram/IM)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "新特性:" -ForegroundColor Blue
    Write-Host "  - 多 LLM 端点支持，自动故障切换" -ForegroundColor Gray
    Write-Host "  - 端点 3 分钟冷静期机制" -ForegroundColor Gray
    Write-Host "  - 能力路由 (text/vision/video/tools)" -ForegroundColor Gray
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
    
    # 步骤 6: 下载 Whisper 语音模型 (可选)
    Install-WhisperModel -PythonPath $venvPython
    
    # 步骤 7: 初始化配置
    Initialize-Config
    
    # 步骤 8: 初始化数据目录
    Initialize-DataDirs
    
    # 步骤 9: 验证安装
    Test-Installation -PythonPath $venvPython
    
    # 完成
    Show-Completion -VenvPython $venvPython
}

# 运行主函数
Main
