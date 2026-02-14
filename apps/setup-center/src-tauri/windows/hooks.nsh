; OpenAkita Setup Center - NSIS Hooks
; 目标：
; - 卸载时强制杀掉残留进程（Setup Center 本体 + OpenAkita 后台服务）
; - 勾选“清理用户数据”时，删除用户目录下的 ~/.openakita

!macro _OpenAkita_KillPid pid
  StrCpy $0 "${pid}"
  ; 仅在 pid 非空时执行 kill（注意：不能在 macro 中用 Return，会退出调用方 Section）
  ${If} $0 != ""
    ExecWait 'taskkill /PID $0 /T /F' $1
  ${EndIf}
!macroend

!macro _OpenAkita_KillAllServicePids
  ; ~/.openakita/run/openakita-*.pid
  ; 先构建基础目录路径，再拼接通配符
  ExpandEnvStrings $R7 "%USERPROFILE%\\.openakita\\run"
  FindFirst $R1 $R2 "$R7\\openakita-*.pid"
  ${DoWhile} $R2 != ""
    ; 读 pid 文件（$R2 是文件名，$R7 是目录，拼接完整路径）
    FileOpen $R4 "$R7\\$R2" "r"
    ${IfNot} ${Errors}
      FileRead $R4 $R5
      FileClose $R4
      ; $R5 可能带 \r\n，截取前 32 字符
      StrCpy $R6 $R5 32
      ; kill
      !insertmacro _OpenAkita_KillPid $R6
    ${EndIf}
    ; next
    FindNext $R1 $R2
  ${Loop}
  FindClose $R1
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; 卸载前：强制杀掉残留进程，避免“卸载后仍在后台跑”
  ; 1) 杀掉 Setup Center（可能在托盘常驻）
  ExecWait 'taskkill /IM openakita-setup-center.exe /T /F' $0

  ; 2) 杀掉 OpenAkita serve（按 pid 文件枚举）
  !insertmacro _OpenAkita_KillAllServicePids
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; 安装完成后：写入版本信息到 state.json（供 App 环境检测用）
  ; 注意：state.json 可能已存在（升级安装），仅更新版本字段
  ExpandEnvStrings $R0 "%USERPROFILE%\.openakita"
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

  ; Finish 页面会提供"运行应用程序"选项 (带 --first-run 参数)
  ; 这里无需额外操作，RunMainBinary 已带 --first-run
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; 勾选“清理用户数据”时：删除 ~/.openakita（真实用户数据目录）
  ; 仅在非更新模式下清理（与默认行为保持一致）
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    ExpandEnvStrings $R0 "%USERPROFILE%\\.openakita"
    ExecWait 'cmd /c rd /s /q "$R0"'
  ${EndIf}
!macroend

