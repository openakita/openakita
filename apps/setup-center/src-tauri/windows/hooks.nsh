; OpenAkita Setup Center - NSIS Hooks
; 目标：
; - 卸载时强制杀掉残留进程（Setup Center 本体 + OpenAkita 后台服务）
; - 勾选"清理用户数据"时，删除用户目录下的 ~/.openakita

; ── PATH 辅助脚本 ──
; 通过 PowerShell 安全地读写 PATH 注册表值，解决：
; 1. NSIS ReadRegStr 字符串长度上限导致长 PATH 被截断/清空
; 2. 保持 REG_EXPAND_SZ 类型（保留 %USERPROFILE% 等环境变量引用）
; 3. 使用分号分割后逐条精确比较，避免子字符串误匹配
!macro _OpenAkita_WritePathHelper
  InitPluginsDir
  FileOpen $R9 "$PLUGINSDIR\_oa_pathhelper.ps1" w
  FileWrite $R9 "param([string]$$Action, [string]$$BinDir, [string]$$RegPath)$\r$\n"
  FileWrite $R9 "$$ErrorActionPreference = 'Stop'$\r$\n"
  FileWrite $R9 "try {$\r$\n"
  FileWrite $R9 "    $$key = Get-Item -LiteralPath $$RegPath -ErrorAction SilentlyContinue$\r$\n"
  FileWrite $R9 "    if (-not $$key) {$\r$\n"
  FileWrite $R9 "        if ($$Action -eq 'add') {$\r$\n"
  FileWrite $R9 "            New-Item -Path $$RegPath -Force | Out-Null$\r$\n"
  FileWrite $R9 "            New-ItemProperty -Path $$RegPath -Name 'Path' -Value $$BinDir -PropertyType ExpandString | Out-Null$\r$\n"
  FileWrite $R9 "        }$\r$\n"
  FileWrite $R9 "        exit 0$\r$\n"
  FileWrite $R9 "    }$\r$\n"
  FileWrite $R9 "    $$cur = $$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')$\r$\n"
  FileWrite $R9 "    $$bn = $$BinDir.TrimEnd([char]92)$\r$\n"
  FileWrite $R9 "    if ($$Action -eq 'add') {$\r$\n"
  FileWrite $R9 "        if (-not $$cur) {$\r$\n"
  FileWrite $R9 "            Set-ItemProperty -LiteralPath $$RegPath -Name 'Path' -Value $$BinDir -Type ExpandString$\r$\n"
  FileWrite $R9 "        } else {$\r$\n"
  FileWrite $R9 "            $$entries = $$cur -split ';'$\r$\n"
  FileWrite $R9 "            $$found = $$entries | Where-Object { $$_.TrimEnd([char]92) -ieq $$bn }$\r$\n"
  FileWrite $R9 "            if (-not $$found) {$\r$\n"
  FileWrite $R9 '                $$np = "$$cur;$$BinDir"$\r$\n'
  FileWrite $R9 "                Set-ItemProperty -LiteralPath $$RegPath -Name 'Path' -Value $$np -Type ExpandString$\r$\n"
  FileWrite $R9 "            }$\r$\n"
  FileWrite $R9 "        }$\r$\n"
  FileWrite $R9 "    } elseif ($$Action -eq 'remove') {$\r$\n"
  FileWrite $R9 "        if ($$cur) {$\r$\n"
  FileWrite $R9 "            $$filtered = ($$cur -split ';') | Where-Object { $$_ -and ($$_.TrimEnd([char]92) -ine $$bn) }$\r$\n"
  FileWrite $R9 "            $$np = $$filtered -join ';'$\r$\n"
  FileWrite $R9 "            if ($$np -cne $$cur) {$\r$\n"
  FileWrite $R9 "                Set-ItemProperty -LiteralPath $$RegPath -Name 'Path' -Value $$np -Type ExpandString$\r$\n"
  FileWrite $R9 "            }$\r$\n"
  FileWrite $R9 "        }$\r$\n"
  FileWrite $R9 "    }$\r$\n"
  FileWrite $R9 "    exit 0$\r$\n"
  FileWrite $R9 "} catch {$\r$\n"
  FileWrite $R9 "    exit 1$\r$\n"
  FileWrite $R9 "}$\r$\n"
  FileClose $R9
!macroend

!macro _OpenAkita_KillPid pid
  StrCpy $0 "${pid}"
  ; 仅在 pid 非空时执行 kill；nsExec 在隐藏控制台中运行，无弹窗
  ; 先 Stop-Process 杀主进程，再 taskkill /T 杀子进程树
  ${If} $0 != ""
    nsExec::ExecToLog 'powershell -NoProfile -Command "Stop-Process -Id $0 -Force -ErrorAction SilentlyContinue"'
    Pop $1
    nsExec::ExecToLog 'taskkill /PID $0 /T /F'
    Pop $1
  ${EndIf}
!macroend

; 读取 custom_root.txt 获取实际数据根目录，结果写入 $R9
; 该文件由 Tauri 端在设置自定义路径时同步写入（纯文本，仅包含路径）
; 如果文件不存在或内容为空，$R9 = 默认路径
!macro _OpenAkita_ResolveRoot
  ExpandEnvStrings $R9 "%USERPROFILE%\.openakita"
  IfFileExists "$R9\custom_root.txt" +1 +8
  ClearErrors
  FileOpen $R8 "$R9\custom_root.txt" "r"
  IfErrors +5 0
  FileRead $R8 $R7
  FileClose $R8
  StrCmp $R7 "" +2 0
  StrCpy $R9 $R7
!macroend

!macro _OpenAkita_KillServicePidsIn dir
  FindFirst $R1 $R2 "${dir}\openakita-*.pid"
  ${DoWhile} $R2 != ""
    FileOpen $R4 "${dir}\$R2" "r"
    ${IfNot} ${Errors}
      FileRead $R4 $R5
      FileClose $R4
      StrCpy $R6 $R5 32
      !insertmacro _OpenAkita_KillPid $R6
    ${EndIf}
    FindNext $R1 $R2
  ${Loop}
  FindClose $R1
!macroend

!macro _OpenAkita_KillAllServicePids
  ; 解析实际数据根目录并清理 PID 文件
  !insertmacro _OpenAkita_ResolveRoot
  !insertmacro _OpenAkita_KillServicePidsIn "$R9\run"
  ; 始终也检查默认路径（兼容残留，重复检查是无害的）
  ExpandEnvStrings $R0 "%USERPROFILE%\.openakita\run"
  !insertmacro _OpenAkita_KillServicePidsIn $R0
!macroend

; 生成合并清理 PowerShell 脚本（环境组件 + 可选用户数据），单次调用替代逐目录多次调用
!macro _OpenAkita_WriteCleanupScript
  InitPluginsDir
  FileOpen $R8 "$PLUGINSDIR\_oa_cleanup.ps1" w
  FileWrite $R8 "param([string]$$Root, [switch]$$CleanUserData)$\r$\n"
  FileWrite $R8 "$$ErrorActionPreference = 'SilentlyContinue'$\r$\n"
  FileWrite $R8 "foreach ($$d in @('run','venv','runtime','modules','python','embedded_python')) {$\r$\n"
  FileWrite $R8 "    $$p = Join-Path $$Root $$d$\r$\n"
  FileWrite $R8 "    if (Test-Path $$p) {$\r$\n"
  FileWrite $R8 "        if ($$d -in @('venv','runtime')) {$\r$\n"
  FileWrite $R8 "            Get-ChildItem -Path $$p -Recurse -Force -File -EA SilentlyContinue | ForEach-Object { $$_.IsReadOnly = $$false }$\r$\n"
  FileWrite $R8 "        }$\r$\n"
  FileWrite $R8 '        Remove-Item -LiteralPath $$p -Recurse -Force -EA SilentlyContinue$\r$\n'
  FileWrite $R8 '        if (Test-Path $$p) { cmd /c rd /s /q "$$p" 2>$$null }$\r$\n'
  FileWrite $R8 "    }$\r$\n"
  FileWrite $R8 "}$\r$\n"
  FileWrite $R8 "if ($$CleanUserData) {$\r$\n"
  FileWrite $R8 "    foreach ($$d in @('workspaces','uploads','logs')) {$\r$\n"
  FileWrite $R8 "        $$p = Join-Path $$Root $$d$\r$\n"
  FileWrite $R8 "        if (Test-Path $$p) {$\r$\n"
  FileWrite $R8 '            Remove-Item -LiteralPath $$p -Recurse -Force -EA SilentlyContinue$\r$\n'
  FileWrite $R8 '            if (Test-Path $$p) { cmd /c rd /s /q "$$p" 2>$$null }$\r$\n'
  FileWrite $R8 "        }$\r$\n"
  FileWrite $R8 "    }$\r$\n"
  FileWrite $R8 "    foreach ($$f in @('state.json','config.json','.env','cli.json')) {$\r$\n"
  FileWrite $R8 "        Remove-Item -LiteralPath (Join-Path $$Root $$f) -Force -EA SilentlyContinue$\r$\n"
  FileWrite $R8 "    }$\r$\n"
  FileWrite $R8 "}$\r$\n"
  FileClose $R8
!macroend

!macro NSIS_HOOK_PREINSTALL
  ; 安装前（Section Install 入口处）：强制杀掉所有旧进程，防止文件锁定导致覆盖失败。
  ; 所有清理操作统一在此执行（有进度日志），PageLeaveEnvCheck 仅设置标志位。
  ;
  ; 所有命令通过 nsExec 在隐藏控制台中执行，完全无弹窗。
  ; 策略：四层递进式进程终止，确保文件锁完全释放。

  ; 1) 按进程名杀掉已知进程 + 子进程树
  DetailPrint "Stopping OpenAkita processes..."
  nsExec::ExecToLog 'powershell -NoProfile -Command "Get-Process -Name openakita-setup-center,openakita-server -EA SilentlyContinue | Stop-Process -Force"'
  Pop $0
  nsExec::ExecToLog 'taskkill /IM openakita-setup-center.exe /T /F'
  Pop $0
  nsExec::ExecToLog 'taskkill /IM openakita-server.exe /T /F'
  Pop $0

  ; 2) 杀掉 PID 文件追踪的服务进程（python 方式启动的后端）
  !insertmacro _OpenAkita_KillAllServicePids

  ; 3) 兜底：杀掉安装目录下所有残留进程（捕获孤儿 Python 子进程和其他锁文件的进程）
  ;    openakita-server.exe (PyInstaller) 会派生 _internal\python*.exe 等子进程，
  ;    如果父子关系断裂，taskkill /T 无法杀到；按路径通杀可兜底。
  ;    路径通过环境变量传递，避免空格导致 PowerShell 引号解析出错。
  DetailPrint "Cleaning residual processes in install directory..."
  System::Call 'kernel32::SetEnvironmentVariable(t "OA_KILL_DIR", t "$INSTDIR")'
  nsExec::ExecToLog 'powershell -NoProfile -Command "Get-Process | Where-Object { $$_.Path -and $$_.Path.StartsWith($$env:OA_KILL_DIR, [System.StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -EA SilentlyContinue"'
  Pop $0
  ; 同样处理 ~/.openakita 下的 venv/python 进程
  ExpandEnvStrings $R0 "%USERPROFILE%\.openakita"
  System::Call 'kernel32::SetEnvironmentVariable(t "OA_KILL_DIR", t "$R0")'
  nsExec::ExecToLog 'powershell -NoProfile -Command "Get-Process | Where-Object { $$_.Path -and $$_.Path.StartsWith($$env:OA_KILL_DIR, [System.StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -EA SilentlyContinue"'
  Pop $0

  ; 4) 等待进程完全退出 + Windows 释放文件锁（DLL 卸载需要额外时间）
  Sleep 3000

  ; 5) 合并清理环境组件 + 可选用户数据（单次 PowerShell 调用替代逐目录多次调用）
  ExpandEnvStrings $R0 "%USERPROFILE%\.openakita"
  ${If} ${FileExists} "$R0\*"
    DetailPrint "Cleaning previous installation components..."
    !insertmacro _OpenAkita_WriteCleanupScript
    ${If} $EnvCleanUserDataConfirmed = 1
      DetailPrint "Cleaning user data (as requested)..."
      nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\_oa_cleanup.ps1" -Root "$R0" -CleanUserData'
      Pop $0
      ; Tauri 应用数据目录（WebView 缓存、localStorage 等前端数据）
      SetShellVarContext current
      RmDir /r "$APPDATA\${BUNDLEID}"
      RmDir /r "$LOCALAPPDATA\${BUNDLEID}"
    ${Else}
      nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\_oa_cleanup.ps1" -Root "$R0"'
      Pop $0
    ${EndIf}
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; 卸载前：强制杀掉残留进程（合并 PowerShell 调用，nsExec 无弹窗）
  nsExec::ExecToLog 'powershell -NoProfile -Command "Get-Process -Name openakita-setup-center,openakita-server -EA SilentlyContinue | Stop-Process -Force"'
  Pop $0
  nsExec::ExecToLog 'taskkill /IM openakita-setup-center.exe /T /F'
  Pop $0
  nsExec::ExecToLog 'taskkill /IM openakita-server.exe /T /F'
  Pop $0
  !insertmacro _OpenAkita_KillAllServicePids
  ; 兜底：杀掉安装目录和数据目录下所有残留进程
  System::Call 'kernel32::SetEnvironmentVariable(t "OA_KILL_DIR", t "$INSTDIR")'
  nsExec::ExecToLog 'powershell -NoProfile -Command "Get-Process | Where-Object { $$_.Path -and $$_.Path.StartsWith($$env:OA_KILL_DIR, [System.StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -EA SilentlyContinue"'
  Pop $0
  ExpandEnvStrings $R0 "%USERPROFILE%\.openakita"
  System::Call 'kernel32::SetEnvironmentVariable(t "OA_KILL_DIR", t "$R0")'
  nsExec::ExecToLog 'powershell -NoProfile -Command "Get-Process | Where-Object { $$_.Path -and $$_.Path.StartsWith($$env:OA_KILL_DIR, [System.StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -EA SilentlyContinue"'
  Pop $0
  Sleep 3000
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; 安装完成后：写入版本信息到 state.json（供 App 环境检测用）
  ; 注意：state.json 可能已存在（升级安装），仅更新版本字段
  ; 解析实际数据根目录（可能被用户自定义到其他磁盘）
  !insertmacro _OpenAkita_ResolveRoot
  StrCpy $R0 $R9
  CreateDirectory "$R0"

  ; 写入 cli.json（供 Rust get_cli_status 读取）
  ReadRegDWORD $R1 HKCU "Software\OpenAkita\CLI" "openakita"
  ReadRegDWORD $R2 HKCU "Software\OpenAkita\CLI" "oa"
  ReadRegDWORD $R3 HKCU "Software\OpenAkita\CLI" "addToPath"
  ; 构造 JSON 中的 commands 数组
  StrCpy $R4 ""
  ${If} $R1 = ${BST_CHECKED}
    StrCpy $R4 '"openakita"'
  ${EndIf}
  ${If} $R2 = ${BST_CHECKED}
    ${If} $R4 != ""
      StrCpy $R4 '$R4, "oa"'
    ${Else}
      StrCpy $R4 '"oa"'
    ${EndIf}
  ${EndIf}
  ; 写入 cli.json
  ${If} $R4 != ""
    FileOpen $R5 "$R0\cli.json" w
    FileWrite $R5 '{"commands": [$R4], "addToPath": '
    ${If} $R3 = ${BST_CHECKED}
      FileWrite $R5 'true'
    ${Else}
      FileWrite $R5 'false'
    ${EndIf}
    FileWrite $R5 ', "binDir": "$INSTDIR\bin", "installedAt": "${VERSION}"}'
    FileClose $R5
  ${EndIf}

  ; venv/runtime 清理已统一在 NSIS_HOOK_PREINSTALL 中通过 PowerShell 脚本完成，
  ; 无需再以用户身份单独启动应用执行 --clean-env。
!macroend

!macro _OpenAkita_ForceRemoveDir dir
  System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t "${dir}")'
  nsExec::ExecToLog 'powershell -NoProfile -Command "Remove-Item -LiteralPath $$env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"'
  Pop $0
  ${If} $0 != 0
    nsExec::ExecToLog 'cmd /c rd /s /q "${dir}"'
    Pop $0
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; 勾选"清理用户数据"时：删除数据目录
  ; 同时清理自定义路径和默认路径（重复删除无害）
  ; 仅在非更新模式下清理
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    ; 先读取自定义路径（在删除默认目录之前，因为 custom_root.txt 在默认目录里）
    !insertmacro _OpenAkita_ResolveRoot
    ; 清理自定义路径（如果有）
    !insertmacro _OpenAkita_ForceRemoveDir $R9
    ; 始终清理默认路径（包含 root_config.json 和 custom_root.txt）
    ExpandEnvStrings $R0 "%USERPROFILE%\.openakita"
    !insertmacro _OpenAkita_ForceRemoveDir $R0
  ${EndIf}
!macroend
