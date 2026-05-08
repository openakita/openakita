; OpenAkita Setup Center - NSIS Hooks
; 目标：
; - 卸载时强制杀掉残留进程（Setup Center 本体 + OpenAkita 后台服务）
; - 勾选"清理用户数据"时，删除用户目录下的 ~/.openakita

; Declare StrRep from StrFunc.nsh for JSON path escaping
; (StrFunc.nsh is included by installer.nsi before this file)
${StrRep}

; ── Legacy install migration ──
; Detect old "OpenAkita Desktop" installs so the new "OpenAkitaDesktop"
; installer can silently uninstall the old version and migrate CLI/PATH.
!define LEGACY_PRODUCTNAME "OpenAkita Desktop"
!define LEGACY_UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${LEGACY_PRODUCTNAME}"
!define LEGACY_MANUPRODUCTKEY "Software\OpenAkita\${LEGACY_PRODUCTNAME}"

Var LegacyInstallDir
Var LegacyUninstallString
Var LegacyCliOpenakita
Var LegacyCliOa
Var LegacyCliAddPath
Var LegacyMigrated

!macro _OpenAkita_DetectLegacyInstall
  StrCpy $LegacyInstallDir ""
  StrCpy $LegacyUninstallString ""
  StrCpy $LegacyMigrated 0

  ; Primary source: MANUPRODUCTKEY stores $INSTDIR without quotes
  ReadRegStr $LegacyInstallDir HKCU "${LEGACY_MANUPRODUCTKEY}" ""
  ${If} $LegacyInstallDir == ""
    ; Fallback: InstallLocation has surrounding quotes — strip them
    ReadRegStr $LegacyInstallDir HKCU "${LEGACY_UNINSTKEY}" "InstallLocation"
    StrCpy $R0 $LegacyInstallDir 1
    ${If} $R0 == '"'
      StrLen $R1 $LegacyInstallDir
      IntOp $R1 $R1 - 2
      StrCpy $LegacyInstallDir $LegacyInstallDir $R1 1
    ${EndIf}
  ${EndIf}

  ; UninstallString keeps its embedded quotes (ExecWait needs them)
  ReadRegStr $LegacyUninstallString HKCU "${LEGACY_UNINSTKEY}" "UninstallString"

  ; Save CLI preferences BEFORE running old uninstaller (it deletes the key)
  ${If} $LegacyInstallDir != ""
    ReadRegDWORD $LegacyCliOpenakita HKCU "Software\OpenAkita\CLI" "openakita"
    ReadRegDWORD $LegacyCliOa HKCU "Software\OpenAkita\CLI" "oa"
    ReadRegDWORD $LegacyCliAddPath HKCU "Software\OpenAkita\CLI" "addToPath"
  ${EndIf}
!macroend

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

; 读取 custom_root.txt 获取实际数据根目录，结果写入 $R9
; 该文件由 Tauri 端在设置自定义路径时同步写入（纯文本，仅包含路径）
; 如果文件不存在或内容为空，$R9 = 默认路径
!macro _OpenAkita_ResolveRoot
  ExpandEnvStrings $R9 "%USERPROFILE%\.openakita"
  ${If} ${FileExists} "$R9\custom_root.txt"
    ClearErrors
    FileOpen $R8 "$R9\custom_root.txt" "r"
    ${IfNot} ${Errors}
      FileRead $R8 $R7
      FileClose $R8
      ; Strip trailing \r\n from FileRead
      StrCpy $R8 $R7 1 -1
      ${If} $R8 == "$\n"
        StrCpy $R7 $R7 -1
      ${EndIf}
      StrCpy $R8 $R7 1 -1
      ${If} $R8 == "$\r"
        StrCpy $R7 $R7 -1
      ${EndIf}
      ${If} $R7 != ""
        StrCpy $R9 $R7
      ${EndIf}
    ${EndIf}
  ${EndIf}
!macroend

; Cleanup PowerShell script — resolves BOTH default and custom data roots internally
; (bypasses NSIS encoding limitations for non-ASCII custom paths).
; Architecture matches _oa_kill.ps1 which also self-resolves custom root via PS.
!macro _OpenAkita_WriteCleanupScript
  InitPluginsDir
  FileOpen $R8 "$PLUGINSDIR\_oa_cleanup.ps1" w
  FileWrite $R8 "param([switch]$$CleanUserData)$\r$\n"
  FileWrite $R8 "$$ErrorActionPreference = 'SilentlyContinue'$\r$\n"
  ; ── Resolve all data roots (default + custom) ──
  FileWrite $R8 "$$defaultRoot = Join-Path $$env:USERPROFILE '.openakita'$\r$\n"
  FileWrite $R8 "$$roots = @($$defaultRoot)$\r$\n"
  FileWrite $R8 "$$crf = Join-Path $$defaultRoot 'custom_root.txt'$\r$\n"
  FileWrite $R8 "if (Test-Path $$crf) {$\r$\n"
  ; ReadAllText auto-detects BOM; falls back to UTF-8 for old no-BOM files
  FileWrite $R8 "    try { $$cr = [System.IO.File]::ReadAllText($$crf).Trim() } catch { $$cr = '' }$\r$\n"
  FileWrite $R8 "    if ($$cr -and $$cr -ne $$defaultRoot -and (Test-Path $$cr)) {$\r$\n"
  FileWrite $R8 "        $$roots += $$cr$\r$\n"
  FileWrite $R8 "    }$\r$\n"
  FileWrite $R8 "}$\r$\n"
  FileWrite $R8 "function Test-OASafeRoot([string]$$Root) {$\r$\n"
  FileWrite $R8 "    if (-not $$Root -or -not (Test-Path -LiteralPath $$Root)) { return $$false }$\r$\n"
  FileWrite $R8 "    try { $$full = [System.IO.Path]::GetFullPath($$Root).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) } catch { return $$false }$\r$\n"
  FileWrite $R8 "    $$drive = [System.IO.Path]::GetPathRoot($$full)$\r$\n"
  FileWrite $R8 "    if ($$drive) { $$drive = $$drive.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) }$\r$\n"
  FileWrite $R8 "    if ($$drive -and $$full -ieq $$drive) { return $$false }$\r$\n"
  FileWrite $R8 "    $$home = [System.IO.Path]::GetFullPath($$env:USERPROFILE).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)$\r$\n"
  FileWrite $R8 "    if ($$full -ieq $$home) { return $$false }$\r$\n"
  FileWrite $R8 "    $$defFull = [System.IO.Path]::GetFullPath($$defaultRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)$\r$\n"
  FileWrite $R8 "    if ($$full -ieq $$defFull) { return $$true }$\r$\n"
  FileWrite $R8 "    return (Test-Path -LiteralPath (Join-Path $$full '.openakita-root'))$\r$\n"
  FileWrite $R8 "}$\r$\n"
  ; ── Clean each root ──
  FileWrite $R8 "foreach ($$Root in ($$roots | Select-Object -Unique)) {$\r$\n"
  FileWrite $R8 "    if (-not (Test-OASafeRoot $$Root)) { continue }$\r$\n"
  FileWrite $R8 "    foreach ($$d in @('run','venv','runtime','modules','python','embedded_python')) {$\r$\n"
  FileWrite $R8 "        $$p = Join-Path $$Root $$d$\r$\n"
  FileWrite $R8 "        if (Test-Path $$p) {$\r$\n"
  FileWrite $R8 "            if ($$d -in @('venv','runtime')) {$\r$\n"
  FileWrite $R8 "                Get-ChildItem -Path $$p -Recurse -Force -File -EA SilentlyContinue | ForEach-Object { $$_.IsReadOnly = $$false }$\r$\n"
  FileWrite $R8 "            }$\r$\n"
  FileWrite $R8 '            Remove-Item -LiteralPath $$p -Recurse -Force -EA SilentlyContinue$\r$\n'
  FileWrite $R8 '            if (Test-Path $$p) { cmd /c rd /s /q "$$p" 2>$$null }$\r$\n'
  FileWrite $R8 "        }$\r$\n"
  FileWrite $R8 "    }$\r$\n"
  FileWrite $R8 "    if ($$CleanUserData) {$\r$\n"
  FileWrite $R8 "        foreach ($$d in @('workspaces','uploads','logs')) {$\r$\n"
  FileWrite $R8 "            $$p = Join-Path $$Root $$d$\r$\n"
  FileWrite $R8 "            if (Test-Path $$p) {$\r$\n"
  FileWrite $R8 '                Remove-Item -LiteralPath $$p -Recurse -Force -EA SilentlyContinue$\r$\n'
  FileWrite $R8 '                if (Test-Path $$p) { cmd /c rd /s /q "$$p" 2>$$null }$\r$\n'
  FileWrite $R8 "            }$\r$\n"
  FileWrite $R8 "        }$\r$\n"
  FileWrite $R8 "        foreach ($$f in @('state.json','config.json','.env','cli.json')) {$\r$\n"
  FileWrite $R8 "            Remove-Item -LiteralPath (Join-Path $$Root $$f) -Force -EA SilentlyContinue$\r$\n"
  FileWrite $R8 "        }$\r$\n"
  FileWrite $R8 "    }$\r$\n"
  FileWrite $R8 "}$\r$\n"
  FileClose $R8
!macroend

; ── Consolidated process-kill + verify script ──
; Generates a single PowerShell script that:
;   1. Kills by process name (Stop-Process + taskkill /T for child trees)
;   2. Kills by PID files (reads openakita-*.pid from data dirs)
;   3. Kills by install path (catches orphaned/detached child processes)
;   4. Batch-verifies file locks on every *.dll/*.pyd/*.exe under resources/
;      (single-file VCRUNTIME140.dll sentinel gives false negatives because AV
;       releases MS-signed runtimes first while still holding bundled .pyd files)
;   5. Retries with increasing delays; on persistent lock, writes _oa_locked.txt
;      so NSIS_HOOK_PREINSTALL / PREUNINSTALL can MessageBox + Abort with a
;      clear message instead of letting NSIS's native File command surface a
;      cryptic "Cannot open file for writing" dialog.
; All logic in ONE PowerShell process — eliminates 6+ separate PS startup overhead.
!macro _OpenAkita_WriteKillScript
  InitPluginsDir
  FileOpen $R9 "$PLUGINSDIR\_oa_kill.ps1" w
  FileWrite $R9 "param([string]$$InstDir)$\r$\n"
  FileWrite $R9 "$$EA = 'SilentlyContinue'$\r$\n"
  ; ── function: Kill all OpenAkita processes ──
  FileWrite $R9 "function Kill-OA {$\r$\n"
  FileWrite $R9 "    Get-Process -Name openakita-setup-center,openakita-server -EA $$EA |$\r$\n"
  FileWrite $R9 "        Stop-Process -Force -EA $$EA$\r$\n"
  FileWrite $R9 "    & cmd /c 'taskkill /IM openakita-setup-center.exe /T /F >nul 2>&1'$\r$\n"
  FileWrite $R9 "    & cmd /c 'taskkill /IM openakita-server.exe /T /F >nul 2>&1'$\r$\n"
  ; Kill by PID files (resolve custom data root first)
  FileWrite $R9 "    $$root = Join-Path $$env:USERPROFILE '.openakita'$\r$\n"
  FileWrite $R9 "    $$crf = Join-Path $$root 'custom_root.txt'$\r$\n"
  FileWrite $R9 "    $$customRoot = $$null$\r$\n"
  FileWrite $R9 "    if (Test-Path $$crf) {$\r$\n"
  FileWrite $R9 "        try { $$cr = [System.IO.File]::ReadAllText($$crf).Trim() } catch { $$cr = '' }$\r$\n"
  FileWrite $R9 "        if ($$cr) { $$customRoot = $$cr }$\r$\n"
  FileWrite $R9 "    }$\r$\n"
  FileWrite $R9 "    foreach ($$rd in @($$root, $$customRoot)) {$\r$\n"
  FileWrite $R9 "        if (-not $$rd) { continue }$\r$\n"
  FileWrite $R9 "        $$runDir = Join-Path $$rd 'run'$\r$\n"
  FileWrite $R9 "        if (-not (Test-Path $$runDir)) { continue }$\r$\n"
  FileWrite $R9 "        Get-ChildItem (Join-Path $$runDir 'openakita-*.pid') -EA $$EA | ForEach-Object {$\r$\n"
  FileWrite $R9 "            $$p = (Get-Content $$_.FullName -First 1 -EA $$EA)$\r$\n"
  FileWrite $R9 "            if ($$p) { $$p = $$p.Trim() }$\r$\n"
  FileWrite $R9 "            if ($$p -match '^\d+$$') {$\r$\n"
  FileWrite $R9 "                Stop-Process -Id $$p -Force -EA $$EA$\r$\n"
  FileWrite $R9 "                & cmd /c $\"taskkill /PID $$p /T /F >nul 2>&1$\"$\r$\n"
  FileWrite $R9 "            }$\r$\n"
  FileWrite $R9 "        }$\r$\n"
  FileWrite $R9 "    }$\r$\n"
  ; Kill by install path (catches detached/orphaned processes)
  FileWrite $R9 "    foreach ($$dir in @($$InstDir, $$root, $$customRoot)) {$\r$\n"
  FileWrite $R9 "        if (-not $$dir) { continue }$\r$\n"
  FileWrite $R9 "        $$d = $$dir.TrimEnd([char]92) + [char]92$\r$\n"
  FileWrite $R9 "        Get-Process | Where-Object {$\r$\n"
  FileWrite $R9 "            $$_.Path -and $$_.Path.StartsWith($$d, [System.StringComparison]::OrdinalIgnoreCase)$\r$\n"
  FileWrite $R9 "        } | Stop-Process -Force -EA $$EA$\r$\n"
  FileWrite $R9 "    }$\r$\n"
  FileWrite $R9 "}$\r$\n"
  ; ── function: Test file lock ──
  FileWrite $R9 "function Test-Locked([string]$$f) {$\r$\n"
  FileWrite $R9 "    if (-not (Test-Path $$f)) { return $$false }$\r$\n"
  FileWrite $R9 "    try {$\r$\n"
  FileWrite $R9 "        $$s = [IO.File]::Open($$f, 'Open', 'ReadWrite', 'None')$\r$\n"
  FileWrite $R9 "        $$s.Close(); return $$false$\r$\n"
  FileWrite $R9 "    } catch { return $$true }$\r$\n"
  FileWrite $R9 "}$\r$\n"
  ; ── main: kill + batch-verify + retry ──
  ; Reset locked-file marker from any previous invocation in this NSIS run
  ; (e.g., the reinst_uninstall path in installer.nsi calls KILLPROCS first).
  FileWrite $R9 "$$lockedListPath = Join-Path (Split-Path $$PSCommandPath) '_oa_locked.txt'$\r$\n"
  FileWrite $R9 "Remove-Item $$lockedListPath -Force -EA $$EA$\r$\n"
  ; Enumerate every file the live process may be holding.
  FileWrite $R9 "$$resRoot = Join-Path $$InstDir 'resources'$\r$\n"
  FileWrite $R9 "$$sentinels = @()$\r$\n"
  ; -LiteralPath: $InstDir defaults to $LOCALAPPDATA\OpenAkita; tolerate Chinese
  ;   usernames or rare bracket chars in path without wildcard interpretation.
  ; -File: skip directories and reparse points cleanly.
  ; Where-Object Extension -in: more deterministic than -Include in PS 5.1.
  FileWrite $R9 "if (Test-Path -LiteralPath $$resRoot) {$\r$\n"
  FileWrite $R9 "    $$sentinels = @(Get-ChildItem -LiteralPath $$resRoot -File -Recurse -Force -EA $$EA |$\r$\n"
  FileWrite $R9 "        Where-Object { $$_.Extension -in '.dll','.pyd','.exe' } |$\r$\n"
  FileWrite $R9 "        ForEach-Object { $$_.FullName })$\r$\n"
  FileWrite $R9 "}$\r$\n"
  ; 4 rounds, sleep 2/4/6/8 = 20s total. Plus 1s settle after success to let
  ; AV scanners release any tail-end oplock before NSIS's File loop begins.
  FileWrite $R9 "$$stillLocked = @()$\r$\n"
  FileWrite $R9 "for ($$i = 0; $$i -lt 4; $$i++) {$\r$\n"
  FileWrite $R9 "    Kill-OA$\r$\n"
  FileWrite $R9 "    Start-Sleep -Seconds (2 + $$i * 2)$\r$\n"
  FileWrite $R9 "    $$stillLocked = @($$sentinels | Where-Object { Test-Locked $$_ })$\r$\n"
  FileWrite $R9 "    if ($$stillLocked.Count -eq 0) {$\r$\n"
  FileWrite $R9 "        Start-Sleep -Milliseconds 1000$\r$\n"
  FileWrite $R9 "        exit 0$\r$\n"
  FileWrite $R9 "    }$\r$\n"
  FileWrite $R9 "}$\r$\n"
  ; Persistent lock — surface to NSIS instead of silent exit 0 + obscure File error.
  FileWrite $R9 "$$stillLocked | Out-File -FilePath $$lockedListPath -Encoding utf8 -Force$\r$\n"
  FileWrite $R9 "exit 1$\r$\n"
  FileClose $R9
!macroend

; Unified process-killing macro — one PowerShell process handles everything.
; Called from: NSIS_HOOK_PREINSTALL, NSIS_HOOK_PREUNINSTALL, reinst_uninstall.
; Only clobbers $0 (nsExec return code). No register side-effects.
!macro NSIS_HOOK_PREINSTALL_KILLPROCS
  !insertmacro _OpenAkita_WriteKillScript
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\_oa_kill.ps1" -InstDir "$INSTDIR"'
  Pop $0
!macroend

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Stopping OpenAkita processes..."
  !insertmacro NSIS_HOOK_PREINSTALL_KILLPROCS

  ; Persistent file lock detected by _oa_kill.ps1 (4 rounds × 20s could not
  ; release every *.dll/*.pyd/*.exe under resources/). Abort with a clear
  ; instruction instead of letting NSIS's native File command fail later
  ; with the obscure "Cannot open file for writing" dialog.
  ; /SD IDOK: defensive default for any future /S (silent) invocation —
  ; passive mode (/P used by Tauri updater) does not call SetSilent so the
  ; MessageBox still shows interactively; this only kicks in if /S is added.
  ${If} ${FileExists} "$PLUGINSDIR\_oa_locked.txt"
    MessageBox MB_OK|MB_ICONSTOP "$(installAbortLocked)" /SD IDOK
    Abort
  ${EndIf}

  ; ── Legacy "OpenAkita Desktop" → "OpenAkitaDesktop" migration ──
  ${If} $LegacyInstallDir != ""
  ${AndIf} $LegacyUninstallString != ""
    DetailPrint "Migrating from legacy install at $LegacyInstallDir..."

    ; Run old uninstaller in passive mode (NOT /UPDATE) so it fully cleans
    ; shortcuts, Run entry, PATH, CLI registry, and uninstall key.
    ; User data is safe: $DeleteAppDataCheckboxState defaults to "" (unchecked)
    ; and /P skips the confirm page so it can never become 1.
    ; _?= makes ExecWait truly synchronous (uninstaller runs in-place).
    ExecWait '$LegacyUninstallString /P _?=$LegacyInstallDir' $0

    ; Residual cleanup — old uninstaller cannot self-delete (running via _?=)
    ; and may have been blocked by AV.
    Delete "$LegacyInstallDir\uninstall.exe"
    RMDir /r "$LegacyInstallDir"

    ; Clean leftover registry
    DeleteRegKey HKCU "${LEGACY_UNINSTKEY}"
    DeleteRegKey HKCU "${LEGACY_MANUPRODUCTKEY}"
    DeleteRegKey /ifempty HKCU "Software\OpenAkita"

    ; Clean leftover shortcuts (in case old uninstaller failed)
    Delete "$SMPROGRAMS\${LEGACY_PRODUCTNAME}\${LEGACY_PRODUCTNAME}.lnk"
    RMDir "$SMPROGRAMS\${LEGACY_PRODUCTNAME}"
    Delete "$SMPROGRAMS\${LEGACY_PRODUCTNAME}.lnk"
    Delete "$DESKTOP\${LEGACY_PRODUCTNAME}.lnk"

    ; Clean leftover autostart Run entry
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${LEGACY_PRODUCTNAME}"

    ; Write back CLI preferences (old uninstaller deleted the key)
    ${If} $LegacyCliOpenakita != ""
      WriteRegDWORD HKCU "Software\OpenAkita\CLI" "openakita" $LegacyCliOpenakita
      WriteRegDWORD HKCU "Software\OpenAkita\CLI" "oa" $LegacyCliOa
      WriteRegDWORD HKCU "Software\OpenAkita\CLI" "addToPath" $LegacyCliAddPath
    ${EndIf}

    ; Log migration result for passive/silent installs where UI is hidden
    ExpandEnvStrings $R0 "%USERPROFILE%\.openakita\logs"
    CreateDirectory "$R0"
    ${If} $0 = 0
      FileOpen $R1 "$R0\migration.log" w
      FileWrite $R1 "Migration from $LegacyInstallDir completed successfully (exit code 0)$\r$\n"
      FileClose $R1
    ${Else}
      FileOpen $R1 "$R0\migration.log" w
      FileWrite $R1 "Migration from $LegacyInstallDir: old uninstaller exited with code $0 (residuals force-cleaned)$\r$\n"
      FileClose $R1
    ${EndIf}

    StrCpy $LegacyMigrated 1
  ${EndIf}

  ; Skip cleanup entirely when no data dir exists (fresh install).
  ; If default root doesn't exist, custom_root.txt can't exist either.
  ExpandEnvStrings $R0 "%USERPROFILE%\.openakita"
  ${If} ${FileExists} "$R0\*"
    ; The cleanup PS script self-resolves both default and custom data roots,
    ; so NSIS no longer needs to parse custom_root.txt (avoids encoding issues).
    !insertmacro _OpenAkita_WriteCleanupScript

    DetailPrint "Cleaning previous installation components..."
    ${If} $EnvCleanUserDataConfirmed = 1
      DetailPrint "Cleaning user data (as requested)..."
      nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\_oa_cleanup.ps1" -CleanUserData'
      Pop $0
      ; Tauri 应用数据目录（WebView 缓存、localStorage 等前端数据）
      SetShellVarContext current
      RmDir /r "$APPDATA\${BUNDLEID}"
      RmDir /r "$LOCALAPPDATA\${BUNDLEID}"
    ${Else}
      nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\_oa_cleanup.ps1"'
      Pop $0
    ${EndIf}
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro NSIS_HOOK_PREINSTALL_KILLPROCS

  ; Same persistent-lock guard as PREINSTALL (uninstaller has its own
  ; $PLUGINSDIR, so the marker file is independent of the install side).
  ${If} ${FileExists} "$PLUGINSDIR\_oa_locked.txt"
    MessageBox MB_OK|MB_ICONSTOP "$(installAbortLocked)" /SD IDOK
    Abort
  ${EndIf}
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
    ; Escape backslashes in path for valid JSON (\ → \\)
    ${StrRep} $R6 "$INSTDIR\bin" "\" "\\"
    FileOpen $R5 "$R0\cli.json" w
    FileWrite $R5 '{"commands": [$R4], "addToPath": '
    ${If} $R3 = ${BST_CHECKED}
      FileWrite $R5 'true'
    ${Else}
      FileWrite $R5 'false'
    ${EndIf}
    FileWrite $R5 ', "binDir": "$R6", "installedAt": "${VERSION}"}'
    FileClose $R5
  ${EndIf}

  ; venv/runtime 清理已统一在 NSIS_HOOK_PREINSTALL 中通过 PowerShell 脚本完成，
  ; 无需再以用户身份单独启动应用执行 --clean-env。
!macroend

; Generates a PowerShell script that resolves BOTH data roots and removes only OpenAkita-owned entries.
; Used by NSIS_HOOK_POSTUNINSTALL — same self-resolving pattern as _oa_cleanup.ps1.
!macro _OpenAkita_WriteUninstDataScript
  InitPluginsDir
  FileOpen $R8 "$PLUGINSDIR\_oa_uninst_data.ps1" w
  FileWrite $R8 "$$EA = 'SilentlyContinue'$\r$\n"
  FileWrite $R8 "$$def = Join-Path $$env:USERPROFILE '.openakita'$\r$\n"
  FileWrite $R8 "$$roots = @($$def)$\r$\n"
  FileWrite $R8 "$$crf = Join-Path $$def 'custom_root.txt'$\r$\n"
  FileWrite $R8 "if (Test-Path $$crf) {$\r$\n"
  FileWrite $R8 "    try { $$cr = [System.IO.File]::ReadAllText($$crf).Trim() } catch { $$cr = '' }$\r$\n"
  FileWrite $R8 "    if ($$cr -and $$cr -ne $$def) { $$roots += $$cr }$\r$\n"
  FileWrite $R8 "}$\r$\n"
  FileWrite $R8 "function Test-OASafeRoot([string]$$Root) {$\r$\n"
  FileWrite $R8 "    if (-not $$Root -or -not (Test-Path -LiteralPath $$Root)) { return $$false }$\r$\n"
  FileWrite $R8 "    try { $$full = [System.IO.Path]::GetFullPath($$Root).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) } catch { return $$false }$\r$\n"
  FileWrite $R8 "    $$drive = [System.IO.Path]::GetPathRoot($$full)$\r$\n"
  FileWrite $R8 "    if ($$drive) { $$drive = $$drive.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) }$\r$\n"
  FileWrite $R8 "    if ($$drive -and $$full -ieq $$drive) { return $$false }$\r$\n"
  FileWrite $R8 "    $$home = [System.IO.Path]::GetFullPath($$env:USERPROFILE).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)$\r$\n"
  FileWrite $R8 "    if ($$full -ieq $$home) { return $$false }$\r$\n"
  FileWrite $R8 "    $$defFull = [System.IO.Path]::GetFullPath($$def).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)$\r$\n"
  FileWrite $R8 "    if ($$full -ieq $$defFull) { return $$true }$\r$\n"
  FileWrite $R8 "    return (Test-Path -LiteralPath (Join-Path $$full '.openakita-root'))$\r$\n"
  FileWrite $R8 "}$\r$\n"
  FileWrite $R8 "foreach ($$r in ($$roots | Select-Object -Unique)) {$\r$\n"
  FileWrite $R8 "    if (-not (Test-OASafeRoot $$r)) { continue }$\r$\n"
  FileWrite $R8 "    foreach ($$d in @('workspaces','venv','runtime','run','logs','modules','bin','data','uploads','python','embedded_python')) {$\r$\n"
  FileWrite $R8 "        $$p = Join-Path $$r $$d$\r$\n"
  FileWrite $R8 '        if (Test-Path -LiteralPath $$p) { Remove-Item -LiteralPath $$p -Recurse -Force -EA $$EA }$\r$\n'
  FileWrite $R8 '        if (Test-Path -LiteralPath $$p) { cmd /c rd /s /q "$$p" 2>$$null }$\r$\n'
  FileWrite $R8 "    }$\r$\n"
  FileWrite $R8 "    foreach ($$f in @('state.json','config.json','.env','cli.json','root_config.json','custom_root.txt','.openakita-root')) {$\r$\n"
  FileWrite $R8 "        Remove-Item -LiteralPath (Join-Path $$r $$f) -Force -EA $$EA$\r$\n"
  FileWrite $R8 "    }$\r$\n"
  FileWrite $R8 "    if ((Get-ChildItem -LiteralPath $$r -Force -EA $$EA | Measure-Object).Count -eq 0) {$\r$\n"
  FileWrite $R8 "        Remove-Item -LiteralPath $$r -Force -EA $$EA$\r$\n"
  FileWrite $R8 "    }$\r$\n"
  FileWrite $R8 "}$\r$\n"
  FileClose $R8
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; Delete all data directories when user checked "remove data" and not updating.
  ; Uses a PS script to resolve custom root (same encoding-safe approach as cleanup).
  ; Note: Tauri app data ($APPDATA/$LOCALAPPDATA) is already removed by installer.nsi
  ; before this hook runs, so we only handle ~/.openakita + custom root here.
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    !insertmacro _OpenAkita_WriteUninstDataScript
    nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\_oa_uninst_data.ps1"'
    Pop $0
  ${EndIf}
!macroend
