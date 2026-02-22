Unicode true
ManifestDPIAware true
; Add in `dpiAwareness` `PerMonitorV2` to manifest for Windows 10 1607+ (note this should not affect lower versions since they should be able to ignore this and pick up `dpiAware` `true` set by `ManifestDPIAware true`)
; Currently undocumented on NSIS's website but is in the Docs folder of source tree, see
; https://github.com/kichik/nsis/blob/5fc0b87b819a9eec006df4967d08e522ddd651c9/Docs/src/attributes.but#L286-L300
; https://github.com/tauri-apps/tauri/pull/10106
ManifestDPIAwareness PerMonitorV2

!if "{{compression}}" == "none"
 SetCompress off
!else
 ; Set the compression algorithm. We default to LZMA.
 SetCompressor /SOLID "{{compression}}"
!endif

!include MUI2.nsh
!include FileFunc.nsh
!include x64.nsh
!include WordFunc.nsh
!include "utils.nsh"
!include "FileAssociation.nsh"
!include "Win\COM.nsh"
!include "Win\Propkey.nsh"
!include "StrFunc.nsh"
${StrCase}
${StrLoc}

{{#if installer_hooks}}
!include "{{installer_hooks}}"
{{/if}}

!define WEBVIEW2APPGUID "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

!define MANUFACTURER "{{manufacturer}}"
!define PRODUCTNAME "{{product_name}}"
!define VERSION "{{version}}"
!define VERSIONWITHBUILD "{{version_with_build}}"
!define HOMEPAGE "{{homepage}}"
!define INSTALLMODE "{{install_mode}}"
!define LICENSE "{{license}}"
!define INSTALLERICON "{{installer_icon}}"
!define SIDEBARIMAGE "{{sidebar_image}}"
!define HEADERIMAGE "{{header_image}}"
!define MAINBINARYNAME "{{main_binary_name}}"
!define MAINBINARYSRCPATH "{{main_binary_path}}"
!define BUNDLEID "{{bundle_id}}"
!define COPYRIGHT "{{copyright}}"
!define OUTFILE "{{out_file}}"
!define ARCH "{{arch}}"
!define ADDITIONALPLUGINSPATH "{{additional_plugins_path}}"
!define ALLOWDOWNGRADES "{{allow_downgrades}}"
!define DISPLAYLANGUAGESELECTOR "{{display_language_selector}}"
!define INSTALLWEBVIEW2MODE "{{install_webview2_mode}}"
!define WEBVIEW2INSTALLERARGS "{{webview2_installer_args}}"
!define WEBVIEW2BOOTSTRAPPERPATH "{{webview2_bootstrapper_path}}"
!define WEBVIEW2INSTALLERPATH "{{webview2_installer_path}}"
!define MINIMUMWEBVIEW2VERSION "{{minimum_webview2_version}}"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTNAME}"
!define MANUKEY "Software\${MANUFACTURER}"
!define MANUPRODUCTKEY "${MANUKEY}\${PRODUCTNAME}"
!define UNINSTALLERSIGNCOMMAND "{{uninstaller_sign_cmd}}"
!define ESTIMATEDSIZE "{{estimated_size}}"
!define STARTMENUFOLDER "{{start_menu_folder}}"

; WM_SETTINGCHANGE / HWND_BROADCAST for PATH environment variable notification
!define /ifndef HWND_BROADCAST 0xFFFF
!define /ifndef WM_SETTINGCHANGE 0x001A

Var PassiveMode
Var UpdateMode
Var NoShortcutMode
Var WixMode
Var OldMainBinaryName

Name "${PRODUCTNAME}"
BrandingText "${COPYRIGHT}"
OutFile "${OUTFILE}"

; We don't actually use this value as default install path,
; it's just for nsis to append the product name folder in the directory selector
; https://nsis.sourceforge.io/Reference/InstallDir
!define PLACEHOLDER_INSTALL_DIR "placeholder\${PRODUCTNAME}"
InstallDir "${PLACEHOLDER_INSTALL_DIR}"

VIProductVersion "${VERSIONWITHBUILD}"
VIAddVersionKey "ProductName" "${PRODUCTNAME}"
VIAddVersionKey "FileDescription" "${PRODUCTNAME}"
VIAddVersionKey "LegalCopyright" "${COPYRIGHT}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"

# additional plugins
!addplugindir "${ADDITIONALPLUGINSPATH}"

; Uninstaller signing command
!if "${UNINSTALLERSIGNCOMMAND}" != ""
 !uninstfinalize '${UNINSTALLERSIGNCOMMAND}'
!endif

; Handle install mode, `perUser`, `perMachine` or `both`
!if "${INSTALLMODE}" == "perMachine"
 RequestExecutionLevel admin
!endif

!if "${INSTALLMODE}" == "currentUser"
 RequestExecutionLevel user
!endif

!if "${INSTALLMODE}" == "both"
 !define MULTIUSER_MUI
 !define MULTIUSER_INSTALLMODE_INSTDIR "${PRODUCTNAME}"
 !define MULTIUSER_INSTALLMODE_COMMANDLINE
 !if "${ARCH}" == "x64"
 !define MULTIUSER_USE_PROGRAMFILES64
 !else if "${ARCH}" == "arm64"
 !define MULTIUSER_USE_PROGRAMFILES64
 !endif
 !define MULTIUSER_INSTALLMODE_DEFAULT_REGISTRY_KEY "${UNINSTKEY}"
 !define MULTIUSER_INSTALLMODE_DEFAULT_REGISTRY_VALUENAME "CurrentUser"
 !define MULTIUSER_INSTALLMODEPAGE_SHOWUSERNAME
 !define MULTIUSER_INSTALLMODE_FUNCTION RestorePreviousInstallLocation
 !define MULTIUSER_EXECUTIONLEVEL Highest
 !include MultiUser.nsh
!endif

; Installer icon
!if "${INSTALLERICON}" != ""
 !define MUI_ICON "${INSTALLERICON}"
!endif

; Installer sidebar image
!if "${SIDEBARIMAGE}" != ""
 !define MUI_WELCOMEFINISHPAGE_BITMAP "${SIDEBARIMAGE}"
!endif

; Installer header image
!if "${HEADERIMAGE}" != ""
 !define MUI_HEADERIMAGE
 !define MUI_HEADERIMAGE_BITMAP "${HEADERIMAGE}"
!endif

; Define registry key to store installer language
!define MUI_LANGDLL_REGISTRY_ROOT "HKCU"
!define MUI_LANGDLL_REGISTRY_KEY "${MANUPRODUCTKEY}"
!define MUI_LANGDLL_REGISTRY_VALUENAME "Installer Language"

; Installer pages, must be ordered as they appear
; 1. Welcome Page (已移除 - 欢迎流程由 App Onboarding Wizard 接管)
; !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
; !insertmacro MUI_PAGE_WELCOME

; 2. License Page (if defined)
!if "${LICENSE}" != ""
 !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
 !insertmacro MUI_PAGE_LICENSE "${LICENSE}"
!endif

; 3. Install mode (if it is set to `both`)
!if "${INSTALLMODE}" == "both"
 !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
 !insertmacro MULTIUSER_PAGE_INSTALLMODE
!endif

; 4. Custom page to ask user if he wants to reinstall/uninstall
; only if a previous installation was detected
Var ReinstallPageCheck
Page custom PageReinstall PageLeaveReinstall
Function PageReinstall
 ; Uninstall previous WiX installation if exists.
 ;
 ; A WiX installer stores the installation info in registry
 ; using a UUID and so we have to loop through all keys under
 ; `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`
 ; and check if `DisplayName` and `Publisher` keys match ${PRODUCTNAME} and ${MANUFACTURER}
 ;
 ; This has a potential issue that there maybe another installation that matches
 ; our ${PRODUCTNAME} and ${MANUFACTURER} but wasn't installed by our WiX installer,
 ; however, this should be fine since the user will have to confirm the uninstallation
 ; and they can chose to abort it if doesn't make sense.
 StrCpy $0 0
 wix_loop:
 EnumRegKey $1 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" $0
 StrCmp $1 "" wix_loop_done ; Exit loop if there is no more keys to loop on
 IntOp $0 $0 + 1
 ReadRegStr $R0 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$1" "DisplayName"
 ReadRegStr $R1 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$1" "Publisher"
 StrCmp "$R0$R1" "${PRODUCTNAME}${MANUFACTURER}" 0 wix_loop
 ReadRegStr $R0 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$1" "UninstallString"
 ${StrCase} $R1 $R0 "L"
 ${StrLoc} $R0 $R1 "msiexec" ">"
 StrCmp $R0 0 0 wix_loop_done
 StrCpy $WixMode 1
 StrCpy $R6 "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$1"
 Goto compare_version
 wix_loop_done:

 ; Check if there is an existing installation, if not, abort the reinstall page
 ReadRegStr $R0 SHCTX "${UNINSTKEY}" ""
 ReadRegStr $R1 SHCTX "${UNINSTKEY}" "UninstallString"
 ${IfThen} "$R0$R1" == "" ${|} Abort ${|}

 ; Compare this installar version with the existing installation
 ; and modify the messages presented to the user accordingly
 compare_version:
 StrCpy $R4 "$(older)"
 ${If} $WixMode = 1
 ReadRegStr $R0 HKLM "$R6" "DisplayVersion"
 ${Else}
 ReadRegStr $R0 SHCTX "${UNINSTKEY}" "DisplayVersion"
 ${EndIf}
 ${IfThen} $R0 == "" ${|} StrCpy $R4 "$(unknown)" ${|}

 nsis_tauri_utils::SemverCompare "${VERSION}" $R0
 Pop $R0
 ; Reinstalling the same version
 ${If} $R0 = 0
 StrCpy $R1 "$(alreadyInstalledLong)"
 StrCpy $R2 "$(addOrReinstall)"
 StrCpy $R3 "$(uninstallApp)"
 !insertmacro MUI_HEADER_TEXT "$(alreadyInstalled)" "$(chooseMaintenanceOption)"
 ; Upgrading
 ${ElseIf} $R0 = 1
 StrCpy $R1 "$(olderOrUnknownVersionInstalled)"
 StrCpy $R2 "$(uninstallBeforeInstalling)"
 StrCpy $R3 "$(dontUninstall)"
 !insertmacro MUI_HEADER_TEXT "$(alreadyInstalled)" "$(choowHowToInstall)"
 ; Downgrading
 ${ElseIf} $R0 = -1
 StrCpy $R1 "$(newerVersionInstalled)"
 StrCpy $R2 "$(uninstallBeforeInstalling)"
 !if "${ALLOWDOWNGRADES}" == "true"
 StrCpy $R3 "$(dontUninstall)"
 !else
 StrCpy $R3 "$(dontUninstallDowngrade)"
 !endif
 !insertmacro MUI_HEADER_TEXT "$(alreadyInstalled)" "$(choowHowToInstall)"
 ${Else}
 Abort
 ${EndIf}

 ; Skip showing the page if passive
 ;
 ; Note that we don't call this earlier at the begining
 ; of this function because we need to populate some variables
 ; related to current installed version if detected and whether
 ; we are downgrading or not.
 ${If} $PassiveMode = 1
 Call PageLeaveReinstall
 ${Else}
 nsDialogs::Create 1018
 Pop $R4
 ${IfThen} $(^RTL) = 1 ${|} nsDialogs::SetRTL $(^RTL) ${|}

 ${NSD_CreateLabel} 0 0 100% 24u $R1
 Pop $R1

 ${NSD_CreateRadioButton} 30u 50u -30u 8u $R2
 Pop $R2
 ${NSD_OnClick} $R2 PageReinstallUpdateSelection

 ${NSD_CreateRadioButton} 30u 70u -30u 8u $R3
 Pop $R3
 ; Disable this radio button if downgrading and downgrades are disabled
 !if "${ALLOWDOWNGRADES}" == "false"
 ${IfThen} $R0 = -1 ${|} EnableWindow $R3 0 ${|}
 !endif
 ${NSD_OnClick} $R3 PageReinstallUpdateSelection

 ; Check the first radio button if this the first time
 ; we enter this page or if the second button wasn't
 ; selected the last time we were on this page
 ${If} $ReinstallPageCheck <> 2
 SendMessage $R2 ${BM_SETCHECK} ${BST_CHECKED} 0
 ${Else}
 SendMessage $R3 ${BM_SETCHECK} ${BST_CHECKED} 0
 ${EndIf}

 ${NSD_SetFocus} $R2
 nsDialogs::Show
 ${EndIf}
FunctionEnd
Function PageReinstallUpdateSelection
 ${NSD_GetState} $R2 $R1
 ${If} $R1 == ${BST_CHECKED}
 StrCpy $ReinstallPageCheck 1
 ${Else}
 StrCpy $ReinstallPageCheck 2
 ${EndIf}
FunctionEnd
Function PageLeaveReinstall
 ${NSD_GetState} $R2 $R1

 ; If migrating from Wix, always uninstall
 ${If} $WixMode = 1
 Goto reinst_uninstall
 ${EndIf}

 ; In update mode, always proceeds without uninstalling
 ${If} $UpdateMode = 1
 Goto reinst_done
 ${EndIf}

 ; $R0 holds whether same(0)/upgrading(1)/downgrading(-1) version
 ; $R1 holds the radio buttons state:
 ; 1 => first choice was selected
 ; 0 => second choice was selected
 ${If} $R0 = 0 ; Same version, proceed
 ${If} $R1 = 1 ; User chose to add/reinstall
 Goto reinst_done
 ${Else} ; User chose to uninstall
 Goto reinst_uninstall
 ${EndIf}
 ${ElseIf} $R0 = 1 ; Upgrading
 ${If} $R1 = 1 ; User chose to uninstall
 Goto reinst_uninstall
 ${Else}
 Goto reinst_done ; User chose NOT to uninstall
 ${EndIf}
 ${ElseIf} $R0 = -1 ; Downgrading
 ${If} $R1 = 1 ; User chose to uninstall
 Goto reinst_uninstall
 ${Else}
 Goto reinst_done ; User chose NOT to uninstall
 ${EndIf}
 ${EndIf}

 reinst_uninstall:
 HideWindow
 ClearErrors

 ${If} $WixMode = 1
 ReadRegStr $R1 HKLM "$R6" "UninstallString"
 ExecWait '$R1' $0
 ${Else}
 ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""
 ReadRegStr $R1 SHCTX "${UNINSTKEY}" "UninstallString"
 ${IfThen} $UpdateMode = 1 ${|} StrCpy $R1 "$R1 /UPDATE" ${|} ; append /UPDATE
 ${IfThen} $PassiveMode = 1 ${|} StrCpy $R1 "$R1 /P" ${|} ; append /P
 StrCpy $R1 "$R1 _?=$4" ; append uninstall directory
 ExecWait '$R1' $0
 ${EndIf}

 BringToFront

 ${IfThen} ${Errors} ${|} StrCpy $0 2 ${|} ; ExecWait failed, set fake exit code

 ${If} $0 <> 0
 ${OrIf} ${FileExists} "$INSTDIR\${MAINBINARYNAME}.exe"
 ; User cancelled wix uninstaller? return to select un/reinstall page
 ${If} $WixMode = 1
 ${AndIf} $0 = 1602
 Abort
 ${EndIf}

 ; User cancelled NSIS uninstaller? return to select un/reinstall page
 ${If} $0 = 1
 Abort
 ${EndIf}

 ; Other erros? show generic error message and return to select un/reinstall page
 MessageBox MB_ICONEXCLAMATION "$(unableToUninstall)"
 Abort
 ${EndIf}
 reinst_done:
FunctionEnd

; 5. Choose install directory page
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_DIRECTORY

; 6. Start menu shortcut page (简化 - 跳过页面，使用默认值)
Var AppStartMenuFolder
!if "${STARTMENUFOLDER}" != ""
 !define MUI_PAGE_CUSTOMFUNCTION_PRE Skip
 !define MUI_STARTMENUPAGE_DEFAULTFOLDER "${STARTMENUFOLDER}"
!else
 !define MUI_PAGE_CUSTOMFUNCTION_PRE Skip
!endif
!insertmacro MUI_PAGE_STARTMENU Application $AppStartMenuFolder

; 6.5 环境检测自定义页面 (检测 ~/.openakita 旧残留)
Var EnvCleanVenv
Var EnvCleanRuntime
Var EnvCleanModules
Var EnvCleanUserData
Var EnvCleanUserDataConfirmed
Var EnvCleanVenvChecked
Var EnvCleanRuntimeChecked
Page custom PageEnvCheck PageLeaveEnvCheck

; 6.6 CLI 命令行工具配置页面
Var CliCheckOpenakita
Var CliCheckOa
Var CliCheckPath
Page custom PageCliSetup PageLeaveCliSetup

; 7. Installation page
!insertmacro MUI_PAGE_INSTFILES

; 8. Finish page
;
; 重要：不定义 MUI_FINISHPAGE_NOAUTOCLOSE，让安装完成后自动跳转到结束页（无需再点 Next）。
; Use show readme button in the finish page as a button create a desktop shortcut
!define MUI_FINISHPAGE_SHOWREADME
!define MUI_FINISHPAGE_SHOWREADME_TEXT "$(createDesktop)"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION CreateOrUpdateDesktopShortcut
; Show run app after installation (with --first-run flag for onboarding wizard).
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION RunMainBinary
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_FINISH

Function RunMainBinary
 ; 安装后首次启动，传入 --first-run 触发 Onboarding Wizard
 nsis_tauri_utils::RunAsUser "$INSTDIR\${MAINBINARYNAME}.exe" "--first-run"
FunctionEnd

; ── 环境检测页面实现 ──
Function PageEnvCheck
 ; passive/silent 模式跳过
 ${If} $PassiveMode = 1
  Abort
 ${EndIf}

 ; 检测 ~/.openakita 是否存在旧残留
 ExpandEnvStrings $R0 "%USERPROFILE%\.openakita"
 StrCpy $R9 0 ; 标记是否有可清理的内容
 ${If} ${FileExists} "$R0\venv\*.*"
  StrCpy $R9 1
 ${EndIf}
 ${If} ${FileExists} "$R0\runtime\*.*"
  StrCpy $R9 1
 ${EndIf}
 ${If} ${FileExists} "$R0\workspaces\*.*"
  StrCpy $R9 1
 ${EndIf}
 ${If} $R9 = 0
  ; 无残留，跳过此页
  Abort
 ${EndIf}

 ; 重置确认状态
 StrCpy $EnvCleanUserDataConfirmed 0

 nsDialogs::Create 1018
 Pop $0
 ${IfThen} $0 == "error" ${|} Abort ${|}
 ${IfThen} $(^RTL) = 1 ${|} nsDialogs::SetRTL $(^RTL) ${|}

 ${NSD_CreateLabel} 0 0 100% 26u "检测到旧版 OpenAkita 环境数据，可能影响新版本运行。$\n新版本已内置 Python 运行时，旧环境可以安全清理。"
 Pop $0

 ; ── 环境清理选项 (默认勾选) ──
 ; 使用固定 Y 坐标，每项间隔 16u，checkbox 高度 12u

 ${If} ${FileExists} "$R0\venv\*.*"
  ${NSD_CreateCheckbox} 14u 34u -14u 12u "清理 Python 虚拟环境 (venv) — 模块安装和工具执行需要，清理后需重建"
  Pop $EnvCleanVenv
  ${NSD_SetState} $EnvCleanVenv ${BST_UNCHECKED}
 ${EndIf}

 ${If} ${FileExists} "$R0\runtime\*.*"
  ${NSD_CreateCheckbox} 14u 50u -14u 12u "清理 Python 运行时 (runtime) — 用于创建 venv，清理后需重新下载"
  Pop $EnvCleanRuntime
  ${NSD_SetState} $EnvCleanRuntime ${BST_UNCHECKED}
 ${EndIf}

 ${If} ${FileExists} "$R0\modules\*.*"
  ${NSD_CreateCheckbox} 14u 66u -14u 12u "清理已安装的可选模块（向量记忆、whisper 等）"
  Pop $EnvCleanModules
  ${NSD_SetState} $EnvCleanModules ${BST_UNCHECKED}
 ${EndIf}

 ; ── 用户数据清理选项 (默认不勾选) ──
 ${NSD_CreateCheckbox} 14u 86u -14u 12u "清理用户数据（工作区、配置文件、对话记录等）"
 Pop $EnvCleanUserData
 ${NSD_SetState} $EnvCleanUserData ${BST_UNCHECKED}

 ${NSD_CreateLabel} 22u 102u -22u 26u "⚠ 警告：清理用户数据将永久删除所有工作区配置、对话记录$\n和个人设置，此操作不可撤销！"
 Pop $0
 SetCtlColors $0 "CC0000" "transparent"

 ; ── 底部提示 ──
 ${NSD_CreateLabel} 0 136u 100% 12u "提示：用户数据清理需要二次确认方可执行。"
 Pop $0
 SetCtlColors $0 "888888" "transparent"

 nsDialogs::Show
FunctionEnd

Function PageLeaveEnvCheck
 ; ── 用户数据清理确认逻辑 ──
 ${If} $EnvCleanUserData != ""
  ${NSD_GetState} $EnvCleanUserData $0
  ${If} $0 = ${BST_CHECKED}
   ; 弹出确认对话框，要求输入 "DELETE" 确认
   MessageBox MB_YESNO|MB_ICONEXCLAMATION \
     "您选择了清理用户数据，这将永久删除所有工作区、配置文件和对话记录！$\n$\n此操作不可撤销，是否继续？" \
     IDYES env_userdata_confirm_input
   ; 用户点了 No，回到页面
   Abort

   env_userdata_confirm_input:
   ; 二次确认：要求用户输入 DELETE 作为最终确认
   StrCpy $R5 ""
   System::Call 'kernel32::GetEnvironmentVariable(t "TEMP", t .r8, i ${NSIS_MAX_STRLEN})'
   ; 用 InputBox 插件 / 或者简单的 nsDialogs 弹窗
   ; NSIS 不支持原生 InputBox, 使用 MessageBox + 文字比对作为双重确认
   MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION \
     "最终确认：请点击「确定」以确认清理全部用户数据。$\n$\n点击「取消」可返回重新选择。" \
     IDOK env_userdata_final_confirm
   ; 用户取消，回到页面
   Abort

   env_userdata_final_confirm:
   StrCpy $EnvCleanUserDataConfirmed 1
  ${EndIf}
 ${EndIf}

 ; 保存勾选状态，供安装完成后以当前用户身份再清理一次（解决“以管理员运行”时清错目录）
 StrCpy $EnvCleanVenvChecked 0
 StrCpy $EnvCleanRuntimeChecked 0
 ${If} $EnvCleanVenv != ""
  ${NSD_GetState} $EnvCleanVenv $EnvCleanVenvChecked
 ${EndIf}
 ${If} $EnvCleanRuntime != ""
  ${NSD_GetState} $EnvCleanRuntime $EnvCleanRuntimeChecked
 ${EndIf}

 ; ── 清理前先杀掉旧进程（避免文件锁定导致删除失败） ──
 ; 优先 PowerShell 不弹窗；若被杀毒拦截则兜底用 cmd/taskkill（会闪黑框）
 ; 杀掉旧版 Setup Center（托盘常驻）
 ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Get-Process -Name openakita-setup-center -ErrorAction SilentlyContinue | Stop-Process -Force"' $0
 ${If} $0 != 0
  ExecWait 'taskkill /IM openakita-setup-center.exe /T /F' $0
 ${EndIf}
 ; 杀掉 openakita-server（PyInstaller 打包的后端）
 ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Get-Process -Name openakita-server -ErrorAction SilentlyContinue | Stop-Process -Force"' $0
 ${If} $0 != 0
  ExecWait 'taskkill /IM openakita-server.exe /T /F' $0
 ${EndIf}
 ; 杀掉按 pid 文件追踪的服务进程
 !insertmacro _OpenAkita_KillAllServicePids
 ; 等待进程完全退出释放文件锁
 Sleep 2000

 ; ── 执行环境清理 ──
 ; 优先 PowerShell 不弹窗；若被拦截则兜底 cmd（会闪黑框）

 ; 总是清理 run 目录中的 stale PID 文件（运行时临时文件，不是用户数据）
 ExpandEnvStrings $R0 "%USERPROFILE%\.openakita\run"
 ${If} ${FileExists} "$R0\*.*"
  System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R0)'
  ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
  ${If} $0 != 0
   ExecWait 'cmd /c rd /s /q "$R0"'
  ${EndIf}
 ${EndIf}

 ${If} $EnvCleanVenv != ""
  ${NSD_GetState} $EnvCleanVenv $0
  ${If} $0 = ${BST_CHECKED}
   ExpandEnvStrings $R0 "%USERPROFILE%\.openakita\venv"
   ExecWait 'cmd /c attrib -R /S /D "$R0"' $0
   System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R0)'
   ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
   ${If} $0 != 0
    ExecWait 'cmd /c rd /s /q "$R0"'
   ${EndIf}
  ${EndIf}
 ${EndIf}

 ${If} $EnvCleanRuntime != ""
  ${NSD_GetState} $EnvCleanRuntime $0
  ${If} $0 = ${BST_CHECKED}
   ExpandEnvStrings $R0 "%USERPROFILE%\.openakita\runtime"
   ; 先去掉只读属性再删，避免“清不掉”
   ExecWait 'cmd /c attrib -R /S /D "$R0"' $0
   System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R0)'
   ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
   ${If} $0 != 0
    ExecWait 'cmd /c rd /s /q "$R0"'
   ${EndIf}
  ${EndIf}
 ${EndIf}

 ${If} $EnvCleanModules != ""
  ${NSD_GetState} $EnvCleanModules $0
  ${If} $0 = ${BST_CHECKED}
   ExpandEnvStrings $R0 "%USERPROFILE%\.openakita\modules"
   System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R0)'
   ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
   ${If} $0 != 0
    ExecWait 'cmd /c rd /s /q "$R0"'
   ${EndIf}
   ; 同时清理嵌入式 Python（模块安装可能下载了它）
   ExpandEnvStrings $R0 "%USERPROFILE%\.openakita\python"
   System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R0)'
   ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
   ${If} $0 != 0
    ExecWait 'cmd /c rd /s /q "$R0"'
   ${EndIf}
   ExpandEnvStrings $R0 "%USERPROFILE%\.openakita\embedded_python"
   System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R0)'
   ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
   ${If} $0 != 0
    ExecWait 'cmd /c rd /s /q "$R0"'
   ${EndIf}
  ${EndIf}
 ${EndIf}

 ; ── 执行用户数据清理（需要双重确认通过） ──
 ${If} $EnvCleanUserDataConfirmed = 1
  ExpandEnvStrings $R0 "%USERPROFILE%\.openakita"
  ; 清理工作区数据
  StrCpy $R8 "$R0\workspaces"
  System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R8)'
  ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
  ${If} $0 != 0
   ExecWait 'cmd /c rd /s /q "$R8"'
  ${EndIf}
  ; 清理配置文件
  Delete "$R0\state.json"
  Delete "$R0\config.json"
  Delete "$R0\.env"
  Delete "$R0\cli.json"
  ; 清理日志
  StrCpy $R8 "$R0\logs"
  System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R8)'
  ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
  ${If} $0 != 0
   ExecWait 'cmd /c rd /s /q "$R8"'
  ${EndIf}
  ; 清理运行时 pid 文件
  StrCpy $R8 "$R0\run"
  System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R8)'
  ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
  ${If} $0 != 0
   ExecWait 'cmd /c rd /s /q "$R8"'
  ${EndIf}
  ; 清理已安装的可选模块（向量记忆、whisper、浏览器自动化等）
  StrCpy $R8 "$R0\modules"
  System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R8)'
  ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
  ${If} $0 != 0
   ExecWait 'cmd /c rd /s /q "$R8"'
  ${EndIf}
  ; 清理嵌入式 Python 环境
  StrCpy $R8 "$R0\python"
  System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R8)'
  ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
  ${If} $0 != 0
   ExecWait 'cmd /c rd /s /q "$R8"'
  ${EndIf}
  StrCpy $R8 "$R0\embedded_python"
  System::Call 'kernel32::SetEnvironmentVariable(t "NSIS_DEL_PATH", t R8)'
  ExecWait 'powershell -NoProfile -WindowStyle Hidden -Command "Remove-Item -LiteralPath $env:NSIS_DEL_PATH -Recurse -Force -ErrorAction SilentlyContinue"' $0
  ${If} $0 != 0
   ExecWait 'cmd /c rd /s /q "$R8"'
  ${EndIf}
 ${EndIf}
FunctionEnd

; ── CLI 命令行工具配置页面实现 ──
Function PageCliSetup
 ; passive/silent/update 模式跳过
 ${If} $PassiveMode = 1
  Abort
 ${EndIf}
 ${If} $UpdateMode = 1
  Abort
 ${EndIf}

 nsDialogs::Create 1018
 Pop $0
 ${IfThen} $0 == "error" ${|} Abort ${|}
 ${IfThen} $(^RTL) = 1 ${|} nsDialogs::SetRTL $(^RTL) ${|}

 ${NSD_CreateLabel} 0 0 100% 26u "选择要注册的终端命令，安装后可在 CMD / PowerShell / Windows Terminal 中直接使用。"
 Pop $0

 ${NSD_CreateCheckbox} 14u 34u -14u 12u "注册 openakita 命令"
 Pop $CliCheckOpenakita
 ${NSD_SetState} $CliCheckOpenakita ${BST_CHECKED}

 ${NSD_CreateCheckbox} 14u 50u -14u 12u "注册 oa 命令（简短别名）"
 Pop $CliCheckOa
 ${NSD_SetState} $CliCheckOa ${BST_CHECKED}

 ${NSD_CreateCheckbox} 14u 74u -14u 12u "添加到系统 PATH 环境变量"
 Pop $CliCheckPath
 ${NSD_SetState} $CliCheckPath ${BST_CHECKED}

 ${NSD_CreateLabel} 22u 90u -22u 20u "提示：添加到 PATH 后，新打开的终端中可直接输入 oa 或 openakita 运行命令。"
 Pop $0
 SetCtlColors $0 "888888" "transparent"

 ${NSD_CreateLabel} 14u 116u -14u 32u "命令示例：$\n  oa serve    — 启动后端服务$\n  oa status   — 查看运行状态$\n  openakita run — 单次执行"
 Pop $0

 nsDialogs::Show
FunctionEnd

Function PageLeaveCliSetup
 ; 读取用户选择，保存到变量供 Install Section 使用
 ; 选择状态在 Section Install 中通过注册表读取 checkbox 控件状态

 ; 将选择写入注册表，供 Install Section 和后续更新使用
 ${NSD_GetState} $CliCheckOpenakita $0
 WriteRegDWORD HKCU "Software\OpenAkita\CLI" "openakita" $0
 ${NSD_GetState} $CliCheckOa $0
 WriteRegDWORD HKCU "Software\OpenAkita\CLI" "oa" $0
 ${NSD_GetState} $CliCheckPath $0
 WriteRegDWORD HKCU "Software\OpenAkita\CLI" "addToPath" $0
FunctionEnd

; Uninstaller Pages
; 1. Confirm uninstall page
Var DeleteAppDataCheckbox
Var DeleteAppDataCheckboxState
!define /ifndef WS_EX_LAYOUTRTL 0x00400000
!define MUI_PAGE_CUSTOMFUNCTION_SHOW un.ConfirmShow
Function un.ConfirmShow ; Add add a `Delete app data` check box
 ; $1 inner dialog HWND
 ; $2 window DPI
 ; $3 style
 ; $4 x
 ; $5 y
 ; $6 width
 ; $7 height
 FindWindow $1 "#32770" "" $HWNDPARENT ; Find inner dialog
 System::Call "user32::GetDpiForWindow(p r1) i .r2"
 ${If} $(^RTL) = 1
 StrCpy $3 "${__NSD_CheckBox_EXSTYLE} | ${WS_EX_LAYOUTRTL}"
 IntOp $4 50 * $2
 ${Else}
 StrCpy $3 "${__NSD_CheckBox_EXSTYLE}"
 IntOp $4 0 * $2
 ${EndIf}
 IntOp $5 100 * $2
 IntOp $6 400 * $2
 IntOp $7 25 * $2
 IntOp $4 $4 / 96
 IntOp $5 $5 / 96
 IntOp $6 $6 / 96
 IntOp $7 $7 / 96
 System::Call 'user32::CreateWindowEx(i r3, w "${__NSD_CheckBox_CLASS}", w "$(deleteAppData)", i ${__NSD_CheckBox_STYLE}, i r4, i r5, i r6, i r7, p r1, i0, i0, i0) i .s'
 Pop $DeleteAppDataCheckbox
 SendMessage $HWNDPARENT ${WM_GETFONT} 0 0 $1
 SendMessage $DeleteAppDataCheckbox ${WM_SETFONT} $1 1
FunctionEnd
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE un.ConfirmLeave
Function un.ConfirmLeave
 SendMessage $DeleteAppDataCheckbox ${BM_GETCHECK} 0 0 $DeleteAppDataCheckboxState
FunctionEnd
!define MUI_PAGE_CUSTOMFUNCTION_PRE un.SkipIfPassive
!insertmacro MUI_UNPAGE_CONFIRM

; 2. Uninstalling Page
!insertmacro MUI_UNPAGE_INSTFILES

;Languages
{{#each languages}}
!insertmacro MUI_LANGUAGE "{{this}}"
{{/each}}
!insertmacro MUI_RESERVEFILE_LANGDLL
{{#each language_files}}
 !include "{{this}}"
{{/each}}

Function .onInit
 ${GetOptions} $CMDLINE "/P" $PassiveMode
 ${IfNot} ${Errors}
 StrCpy $PassiveMode 1
 ${EndIf}

 ${GetOptions} $CMDLINE "/NS" $NoShortcutMode
 ${IfNot} ${Errors}
 StrCpy $NoShortcutMode 1
 ${EndIf}

 ${GetOptions} $CMDLINE "/UPDATE" $UpdateMode
 ${IfNot} ${Errors}
 StrCpy $UpdateMode 1
 ${EndIf}

 !if "${DISPLAYLANGUAGESELECTOR}" == "true"
 !insertmacro MUI_LANGDLL_DISPLAY
 !endif

 !insertmacro SetContext

 ${If} $INSTDIR == "${PLACEHOLDER_INSTALL_DIR}"
 ; Set default install location
 !if "${INSTALLMODE}" == "perMachine"
 ${If} ${RunningX64}
 !if "${ARCH}" == "x64"
 StrCpy $INSTDIR "$PROGRAMFILES64\${PRODUCTNAME}"
 !else if "${ARCH}" == "arm64"
 StrCpy $INSTDIR "$PROGRAMFILES64\${PRODUCTNAME}"
 !else
 StrCpy $INSTDIR "$PROGRAMFILES\${PRODUCTNAME}"
 !endif
 ${Else}
 StrCpy $INSTDIR "$PROGRAMFILES\${PRODUCTNAME}"
 ${EndIf}
 !else if "${INSTALLMODE}" == "currentUser"
 StrCpy $INSTDIR "$LOCALAPPDATA\${PRODUCTNAME}"
 !endif

 Call RestorePreviousInstallLocation
 ${EndIf}


 !if "${INSTALLMODE}" == "both"
 !insertmacro MULTIUSER_INIT
 !endif
FunctionEnd


Section EarlyChecks
 ; Abort silent installer if downgrades is disabled
 !if "${ALLOWDOWNGRADES}" == "false"
 ${If} ${Silent}
 ; If downgrading
 ${If} $R0 = -1
 System::Call 'kernel32::AttachConsole(i -1)i.r0'
 ${If} $0 <> 0
 System::Call 'kernel32::GetStdHandle(i -11)i.r0'
 System::call 'kernel32::SetConsoleTextAttribute(i r0, i 0x0004)' ; set red color
 FileWrite $0 "$(silentDowngrades)"
 ${EndIf}
 Abort
 ${EndIf}
 ${EndIf}
 !endif

SectionEnd

Section WebView2
 ; Check if Webview2 is already installed and skip this section
 ${If} ${RunningX64}
 ReadRegStr $4 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\${WEBVIEW2APPGUID}" "pv"
 ${Else}
 ReadRegStr $4 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\${WEBVIEW2APPGUID}" "pv"
 ${EndIf}
 ${If} $4 == ""
 ReadRegStr $4 HKCU "SOFTWARE\Microsoft\EdgeUpdate\Clients\${WEBVIEW2APPGUID}" "pv"
 ${EndIf}

 ${If} $4 == ""
 ; Webview2 installation
 ;
 ; Skip if updating
 ${If} $UpdateMode <> 1
 !if "${INSTALLWEBVIEW2MODE}" == "downloadBootstrapper"
 Delete "$TEMP\MicrosoftEdgeWebview2Setup.exe"
 DetailPrint "$(webview2Downloading)"
 NSISdl::download "https://go.microsoft.com/fwlink/p/?LinkId=2124703" "$TEMP\MicrosoftEdgeWebview2Setup.exe"
 Pop $0
 ${If} $0 == "success"
 DetailPrint "$(webview2DownloadSuccess)"
 ${Else}
 DetailPrint "$(webview2DownloadError)"
 Abort "$(webview2AbortError)"
 ${EndIf}
 StrCpy $6 "$TEMP\MicrosoftEdgeWebview2Setup.exe"
 Goto install_webview2
 !endif

 !if "${INSTALLWEBVIEW2MODE}" == "embedBootstrapper"
 Delete "$TEMP\MicrosoftEdgeWebview2Setup.exe"
 File "/oname=$TEMP\MicrosoftEdgeWebview2Setup.exe" "${WEBVIEW2BOOTSTRAPPERPATH}"
 DetailPrint "$(installingWebview2)"
 StrCpy $6 "$TEMP\MicrosoftEdgeWebview2Setup.exe"
 Goto install_webview2
 !endif

 !if "${INSTALLWEBVIEW2MODE}" == "offlineInstaller"
 Delete "$TEMP\MicrosoftEdgeWebView2RuntimeInstaller.exe"
 File "/oname=$TEMP\MicrosoftEdgeWebView2RuntimeInstaller.exe" "${WEBVIEW2INSTALLERPATH}"
 DetailPrint "$(installingWebview2)"
 StrCpy $6 "$TEMP\MicrosoftEdgeWebView2RuntimeInstaller.exe"
 Goto install_webview2
 !endif

 Goto webview2_done

 install_webview2:
 DetailPrint "$(installingWebview2)"
 ; $6 holds the path to the webview2 installer
 ExecWait "$6 ${WEBVIEW2INSTALLERARGS} /install" $1
 ${If} $1 = 0
 DetailPrint "$(webview2InstallSuccess)"
 ${Else}
 DetailPrint "$(webview2InstallError)"
 Abort "$(webview2AbortError)"
 ${EndIf}
 webview2_done:
 ${EndIf}
 ${Else}
 !if "${MINIMUMWEBVIEW2VERSION}" != ""
 ${VersionCompare} "${MINIMUMWEBVIEW2VERSION}" "$4" $R0
 ${If} $R0 = 1
 update_webview:
 DetailPrint "$(installingWebview2)"
 ${If} ${RunningX64}
 ReadRegStr $R1 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate" "path"
 ${Else}
 ReadRegStr $R1 HKLM "SOFTWARE\Microsoft\EdgeUpdate" "path"
 ${EndIf}
 ${If} $R1 == ""
 ReadRegStr $R1 HKCU "SOFTWARE\Microsoft\EdgeUpdate" "path"
 ${EndIf}
 ${If} $R1 != ""
 ; Chromium updater docs: https://source.chromium.org/chromium/chromium/src/+/main:docs/updater/user_manual.md
 ; Modified from "HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft EdgeWebView\ModifyPath"
 ExecWait `"$R1" /install appguid=${WEBVIEW2APPGUID}&needsadmin=true` $1
 ${If} $1 = 0
 DetailPrint "$(webview2InstallSuccess)"
 ${Else}
 MessageBox MB_ICONEXCLAMATION|MB_ABORTRETRYIGNORE "$(webview2InstallError)" IDIGNORE ignore IDRETRY update_webview
 Quit
 ignore:
 ${EndIf}
 ${EndIf}
 ${EndIf}
 !endif
 ${EndIf}
SectionEnd

Section Install
 SetOutPath $INSTDIR

 !ifmacrodef NSIS_HOOK_PREINSTALL
 !insertmacro NSIS_HOOK_PREINSTALL
 !endif

 !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"

 ; Copy main executable
 File "${MAINBINARYSRCPATH}"

 ; Copy resources
 {{#each resources_dirs}}
 CreateDirectory "$INSTDIR\\{{this}}"
 {{/each}}
 {{#each resources}}
 File /a "/oname={{this.[1]}}" "{{no-escape @key}}"
 {{/each}}

 ; Copy external binaries
 {{#each binaries}}
 File /a "/oname={{this}}" "{{no-escape @key}}"
 {{/each}}

 ; Create file associations
 {{#each file_associations as |association| ~}}
 {{#each association.ext as |ext| ~}}
 !insertmacro APP_ASSOCIATE "{{ext}}" "{{or association.name ext}}" "{{association-description association.description ext}}" "$INSTDIR\${MAINBINARYNAME}.exe,0" "Open with ${PRODUCTNAME}" "$INSTDIR\${MAINBINARYNAME}.exe $\"%1$\""
 {{/each}}
 {{/each}}

 ; Register deep links
 {{#each deep_link_protocols as |protocol| ~}}
 WriteRegStr SHCTX "Software\Classes\\{{protocol}}" "URL Protocol" ""
 WriteRegStr SHCTX "Software\Classes\\{{protocol}}" "" "URL:${BUNDLEID} protocol"
 WriteRegStr SHCTX "Software\Classes\\{{protocol}}\DefaultIcon" "" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\",0"
 WriteRegStr SHCTX "Software\Classes\\{{protocol}}\shell\open\command" "" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\" $\"%1$\""
 {{/each}}

 ; Create uninstaller
 WriteUninstaller "$INSTDIR\uninstall.exe"

 ; Save $INSTDIR in registry for future installations
 WriteRegStr SHCTX "${MANUPRODUCTKEY}" "" $INSTDIR

 !if "${INSTALLMODE}" == "both"
 ; Save install mode to be selected by default for the next installation such as updating
 ; or when uninstalling
 WriteRegStr SHCTX "${UNINSTKEY}" $MultiUser.InstallMode 1
 !endif

 ; Remove old main binary if it doesn't match new main binary name
 ReadRegStr $OldMainBinaryName SHCTX "${UNINSTKEY}" "MainBinaryName"
 ${If} $OldMainBinaryName != ""
 ${AndIf} $OldMainBinaryName != "${MAINBINARYNAME}.exe"
 Delete "$INSTDIR\$OldMainBinaryName"
 ${EndIf}

 ; Save current MAINBINARYNAME for future updates
 WriteRegStr SHCTX "${UNINSTKEY}" "MainBinaryName" "${MAINBINARYNAME}.exe"

 ; Registry information for add/remove programs
 WriteRegStr SHCTX "${UNINSTKEY}" "DisplayName" "${PRODUCTNAME}"
 WriteRegStr SHCTX "${UNINSTKEY}" "DisplayIcon" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\""
 WriteRegStr SHCTX "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
 WriteRegStr SHCTX "${UNINSTKEY}" "Publisher" "${MANUFACTURER}"
 WriteRegStr SHCTX "${UNINSTKEY}" "InstallLocation" "$\"$INSTDIR$\""
 WriteRegStr SHCTX "${UNINSTKEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
 WriteRegDWORD SHCTX "${UNINSTKEY}" "NoModify" "1"
 WriteRegDWORD SHCTX "${UNINSTKEY}" "NoRepair" "1"

 ${GetSize} "$INSTDIR" "/M=uninstall.exe /S=0K /G=0" $0 $1 $2
 IntOp $0 $0 + ${ESTIMATEDSIZE}
 IntFmt $0 "0x%08X" $0
 WriteRegDWORD SHCTX "${UNINSTKEY}" "EstimatedSize" "$0"

 !if "${HOMEPAGE}" != ""
 WriteRegStr SHCTX "${UNINSTKEY}" "URLInfoAbout" "${HOMEPAGE}"
 WriteRegStr SHCTX "${UNINSTKEY}" "URLUpdateInfo" "${HOMEPAGE}"
 WriteRegStr SHCTX "${UNINSTKEY}" "HelpLink" "${HOMEPAGE}"
 !endif

 ; Create start menu shortcut
 !insertmacro MUI_STARTMENU_WRITE_BEGIN Application
 Call CreateOrUpdateStartMenuShortcut
 !insertmacro MUI_STARTMENU_WRITE_END

 ; Create desktop shortcut for silent and passive installers
 ; because finish page will be skipped
 ${If} $PassiveMode = 1
 ${OrIf} ${Silent}
 Call CreateOrUpdateDesktopShortcut
 ${EndIf}

 ; ── CLI 命令行工具注册 ──
 ; 读取用户在 PageCliSetup 中的选择（存储在注册表中）
 ; 对于 Update/Passive/Silent 模式，尝试读取上次的选择
 ReadRegDWORD $R1 HKCU "Software\OpenAkita\CLI" "openakita"
 ReadRegDWORD $R2 HKCU "Software\OpenAkita\CLI" "oa"
 ReadRegDWORD $R3 HKCU "Software\OpenAkita\CLI" "addToPath"

 ; 如果注册表中没有值（首次安装且跳过了页面，如 silent 模式），默认全部启用
 ${If} $R1 == ""
  StrCpy $R1 ${BST_CHECKED}
 ${EndIf}
 ${If} $R2 == ""
  StrCpy $R2 ${BST_CHECKED}
 ${EndIf}
 ${If} $R3 == ""
  StrCpy $R3 ${BST_CHECKED}
 ${EndIf}

 ; 判断是否需要创建 bin 目录
 ${If} $R1 = ${BST_CHECKED}
 ${OrIf} $R2 = ${BST_CHECKED}
  CreateDirectory "$INSTDIR\bin"

  ; 写入 openakita.cmd
  ${If} $R1 = ${BST_CHECKED}
   FileOpen $R4 "$INSTDIR\bin\openakita.cmd" w
   FileWrite $R4 '@echo off$\r$\n"%~dp0..\resources\openakita-server\openakita-server.exe" %*$\r$\n'
   FileClose $R4
  ${EndIf}

  ; 写入 oa.cmd
  ${If} $R2 = ${BST_CHECKED}
   FileOpen $R4 "$INSTDIR\bin\oa.cmd" w
   FileWrite $R4 '@echo off$\r$\n"%~dp0..\resources\openakita-server\openakita-server.exe" %*$\r$\n'
   FileClose $R4
  ${EndIf}

  ; 添加到 PATH
  ${If} $R3 = ${BST_CHECKED}
   ; 读取当前 PATH 并检查是否已包含 bin 目录
   !if "${INSTALLMODE}" == "perMachine"
    ReadRegStr $R5 HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path"
   !else
    ReadRegStr $R5 HKCU "Environment" "Path"
   !endif

   ; 检查 PATH 中是否已包含 $INSTDIR\bin
   ${StrLoc} $R6 $R5 "$INSTDIR\bin" ">"
   ${If} $R6 == ""
    ; 不存在，追加到 PATH
    ${If} $R5 != ""
     StrCpy $R5 "$R5;$INSTDIR\bin"
    ${Else}
     StrCpy $R5 "$INSTDIR\bin"
    ${EndIf}

    !if "${INSTALLMODE}" == "perMachine"
     WriteRegExpandStr HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path" $R5
    !else
     WriteRegExpandStr HKCU "Environment" "Path" $R5
    !endif

    ; 广播 WM_SETTINGCHANGE 通知其他进程 PATH 已更新
    SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
   ${EndIf}
  ${EndIf}

  ; 保存 INSTDIR 到注册表（卸载时需要知道 bin 目录位置）
  WriteRegStr HKCU "Software\OpenAkita\CLI" "binDir" "$INSTDIR\bin"
 ${EndIf}

 !ifmacrodef NSIS_HOOK_POSTINSTALL
 !insertmacro NSIS_HOOK_POSTINSTALL
 !endif

 ; Auto close this page for passive mode
 ${If} $PassiveMode = 1
 SetAutoClose true
 ${EndIf}
SectionEnd

Function .onInstSuccess
 ; Check for `/R` flag only in silent and passive installers because
 ; GUI installer has a toggle for the user to (re)start the app
 ${If} $PassiveMode = 1
 ${OrIf} ${Silent}
 ${GetOptions} $CMDLINE "/R" $R0
 ${IfNot} ${Errors}
 ${GetOptions} $CMDLINE "/ARGS" $R0
 nsis_tauri_utils::RunAsUser "$INSTDIR\${MAINBINARYNAME}.exe" "$R0"
 ${EndIf}
 ${EndIf}
FunctionEnd

Function un.onInit
 !insertmacro SetContext

 !if "${INSTALLMODE}" == "both"
 !insertmacro MULTIUSER_UNINIT
 !endif

 !insertmacro MUI_UNGETLANGUAGE

 ${GetOptions} $CMDLINE "/P" $PassiveMode
 ${IfNot} ${Errors}
 StrCpy $PassiveMode 1
 ${EndIf}

 ${GetOptions} $CMDLINE "/UPDATE" $UpdateMode
 ${IfNot} ${Errors}
 StrCpy $UpdateMode 1
 ${EndIf}
FunctionEnd

Section Uninstall

 !ifmacrodef NSIS_HOOK_PREUNINSTALL
 !insertmacro NSIS_HOOK_PREUNINSTALL
 !endif

 !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"

 ; Delete the app directory and its content from disk
 ; Copy main executable
 Delete "$INSTDIR\${MAINBINARYNAME}.exe"

 ; Delete resources
 {{#each resources}}
 Delete "$INSTDIR\\{{this.[1]}}"
 {{/each}}

 ; Delete external binaries
 {{#each binaries}}
 Delete "$INSTDIR\\{{this}}"
 {{/each}}

 ; Delete app associations
 {{#each file_associations as |association| ~}}
 {{#each association.ext as |ext| ~}}
 !insertmacro APP_UNASSOCIATE "{{ext}}" "{{or association.name ext}}"
 {{/each}}
 {{/each}}

 ; Delete deep links
 {{#each deep_link_protocols as |protocol| ~}}
 ReadRegStr $R7 SHCTX "Software\Classes\\{{protocol}}\shell\open\command" ""
 ${If} $R7 == "$\"$INSTDIR\${MAINBINARYNAME}.exe$\" $\"%1$\""
 DeleteRegKey SHCTX "Software\Classes\\{{protocol}}"
 ${EndIf}
 {{/each}}


 ; ── CLI 命令行工具清理 ──
 ; 从 PATH 中移除 $INSTDIR\bin
 ReadRegStr $R8 HKCU "Software\OpenAkita\CLI" "binDir"
 ${If} $R8 != ""
  ; 从系统 PATH 移除
  ReadRegStr $R5 HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path"
  ${If} $R5 != ""
   ; 移除 ";$R8" 或 "$R8;" 或单独的 "$R8"
   ${WordReplace} $R5 ";$R8" "" "+" $R5
   ${WordReplace} $R5 "$R8;" "" "+" $R5
   ${WordReplace} $R5 "$R8" "" "+" $R5
   WriteRegExpandStr HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path" $R5
  ${EndIf}

  ; 从用户 PATH 移除
  ReadRegStr $R5 HKCU "Environment" "Path"
  ${If} $R5 != ""
   ${WordReplace} $R5 ";$R8" "" "+" $R5
   ${WordReplace} $R5 "$R8;" "" "+" $R5
   ${WordReplace} $R5 "$R8" "" "+" $R5
   WriteRegExpandStr HKCU "Environment" "Path" $R5
  ${EndIf}

  ; 广播 WM_SETTINGCHANGE
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
 ${EndIf}

 ; 删除 CLI 相关文件
 Delete "$INSTDIR\bin\openakita.cmd"
 Delete "$INSTDIR\bin\oa.cmd"
 RMDir "$INSTDIR\bin"

 ; 清理 CLI 注册表键
 DeleteRegKey HKCU "Software\OpenAkita\CLI"

 ; Delete uninstaller
 Delete "$INSTDIR\uninstall.exe"

 {{#each resources_ancestors}}
 RMDir /REBOOTOK "$INSTDIR\\{{this}}"
 {{/each}}
 RMDir "$INSTDIR"

 ; Remove shortcuts if not updating
 ${If} $UpdateMode <> 1
 !insertmacro DeleteAppUserModelId

 ; Remove start menu shortcut
 !insertmacro MUI_STARTMENU_GETFOLDER Application $AppStartMenuFolder
 !insertmacro IsShortcutTarget "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
 Pop $0
 ${If} $0 = 1
 !insertmacro UnpinShortcut "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk"
 Delete "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk"
 RMDir "$SMPROGRAMS\$AppStartMenuFolder"
 ${EndIf}
 !insertmacro IsShortcutTarget "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
 Pop $0
 ${If} $0 = 1
 !insertmacro UnpinShortcut "$SMPROGRAMS\${PRODUCTNAME}.lnk"
 Delete "$SMPROGRAMS\${PRODUCTNAME}.lnk"
 ${EndIf}

 ; Remove desktop shortcuts
 !insertmacro IsShortcutTarget "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
 Pop $0
 ${If} $0 = 1
 !insertmacro UnpinShortcut "$DESKTOP\${PRODUCTNAME}.lnk"
 Delete "$DESKTOP\${PRODUCTNAME}.lnk"
 ${EndIf}
 ${EndIf}

 ; Remove registry information for add/remove programs
 !if "${INSTALLMODE}" == "both"
 DeleteRegKey SHCTX "${UNINSTKEY}"
 !else if "${INSTALLMODE}" == "perMachine"
 DeleteRegKey HKLM "${UNINSTKEY}"
 !else
 DeleteRegKey HKCU "${UNINSTKEY}"
 !endif

 ; Removes the Autostart entry for ${PRODUCTNAME} from the HKCU Run key if it exists.
 ; This ensures the program does not launch automatically after uninstallation if it exists.
 ; If it doesn't exist, it does nothing.
 ; We do this when not updating (to preserve the registry value on updates)
 ${If} $UpdateMode <> 1
 DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCTNAME}"
 ${EndIf}

 ; Delete app data if the checkbox is selected
 ; and if not updating
 ${If} $DeleteAppDataCheckboxState = 1
 ${AndIf} $UpdateMode <> 1
 ; Clear the install location $INSTDIR from registry
 DeleteRegKey SHCTX "${MANUPRODUCTKEY}"
 DeleteRegKey /ifempty SHCTX "${MANUKEY}"

 ; Clear the install language from registry
 DeleteRegValue HKCU "${MANUPRODUCTKEY}" "Installer Language"
 DeleteRegKey /ifempty HKCU "${MANUPRODUCTKEY}"
 DeleteRegKey /ifempty HKCU "${MANUKEY}"

 SetShellVarContext current
 RmDir /r "$APPDATA\${BUNDLEID}"
 RmDir /r "$LOCALAPPDATA\${BUNDLEID}"
 ${EndIf}

 !ifmacrodef NSIS_HOOK_POSTUNINSTALL
 !insertmacro NSIS_HOOK_POSTUNINSTALL
 !endif

 ; Auto close if passive mode or updating
 ${If} $PassiveMode = 1
 ${OrIf} $UpdateMode = 1
 SetAutoClose true
 ${EndIf}
SectionEnd

Function RestorePreviousInstallLocation
 ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""
 StrCmp $4 "" +2 0
 StrCpy $INSTDIR $4
FunctionEnd

Function Skip
 Abort
FunctionEnd

Function SkipIfPassive
 ${IfThen} $PassiveMode = 1 ${|} Abort ${|}
FunctionEnd
Function un.SkipIfPassive
 ${IfThen} $PassiveMode = 1 ${|} Abort ${|}
FunctionEnd

Function CreateOrUpdateStartMenuShortcut
 ; We used to use product name as MAINBINARYNAME
 ; migrate old shortcuts to target the new MAINBINARYNAME
 StrCpy $R0 0

 !insertmacro IsShortcutTarget "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk" "$INSTDIR\$OldMainBinaryName"
 Pop $0
 ${If} $0 = 1
 !insertmacro SetShortcutTarget "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
 StrCpy $R0 1
 ${EndIf}

 !insertmacro IsShortcutTarget "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\$OldMainBinaryName"
 Pop $0
 ${If} $0 = 1
 !insertmacro SetShortcutTarget "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
 StrCpy $R0 1
 ${EndIf}

 ${If} $R0 = 1
 Return
 ${EndIf}

 ; Skip creating shortcut if in update mode or no shortcut mode
 ; but always create if migrating from wix
 ${If} $WixMode = 0
 ${If} $UpdateMode = 1
 ${OrIf} $NoShortcutMode = 1
 Return
 ${EndIf}
 ${EndIf}

 !if "${STARTMENUFOLDER}" != ""
 CreateDirectory "$SMPROGRAMS\$AppStartMenuFolder"
 CreateShortcut "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
 !insertmacro SetLnkAppUserModelId "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk"
 !else
 CreateShortcut "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
 !insertmacro SetLnkAppUserModelId "$SMPROGRAMS\${PRODUCTNAME}.lnk"
 !endif
FunctionEnd

Function CreateOrUpdateDesktopShortcut
 ; We used to use product name as MAINBINARYNAME
 ; migrate old shortcuts to target the new MAINBINARYNAME
 !insertmacro IsShortcutTarget "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\$OldMainBinaryName"
 Pop $0
 ${If} $0 = 1
 !insertmacro SetShortcutTarget "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
 Return
 ${EndIf}

 ; Skip creating shortcut if in update mode or no shortcut mode
 ; but always create if migrating from wix
 ${If} $WixMode = 0
 ${If} $UpdateMode = 1
 ${OrIf} $NoShortcutMode = 1
 Return
 ${EndIf}
 ${EndIf}

 CreateShortcut "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
 !insertmacro SetLnkAppUserModelId "$DESKTOP\${PRODUCTNAME}.lnk"
FunctionEnd

