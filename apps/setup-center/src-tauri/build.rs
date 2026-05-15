fn main() {
    // 开发/CI 友好：如果缺少 Windows icon.ico，则自动生成一个极简占位图标，
    // 避免 `tauri-build` 在 Windows 上直接失败。
    //
    // 注意：这里生成的只是占位图标。正式发布建议用 `tauri icon` 生成完整图标集。
    ensure_placeholder_windows_icon();

    ensure_resource_dir();
    ensure_gitignored_placeholders();

    let attrs = tauri_build::Attributes::new();

    // On Windows, embed a custom application manifest declaring asInvoker +
    // Windows 10/11 supportedOS so the Program Compatibility Assistant does
    // not fall back to installer heuristics on our main GUI binary.
    #[cfg(target_os = "windows")]
    let attrs = attrs.windows_attributes(
        tauri_build::WindowsAttributes::new().app_manifest(WINDOWS_APP_MANIFEST),
    );

    tauri_build::try_build(attrs).expect("failed to run Tauri build script");
}

#[cfg(target_os = "windows")]
const WINDOWS_APP_MANIFEST: &str = r#"
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency>
    <dependentAssembly>
      <assemblyIdentity
        type="win32"
        name="Microsoft.Windows.Common-Controls"
        version="6.0.0.0"
        processorArchitecture="*"
        publicKeyToken="6595b64144ccf1df"
        language="*"
      />
    </dependentAssembly>
  </dependency>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false" />
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <!-- Windows 10 and Windows 11 -->
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
</assembly>
"#;

fn ensure_resource_dir() {
    let dir = std::path::Path::new("resources").join("openakita-server");
    if !dir.exists() {
        let _ = std::fs::create_dir_all(&dir);
    }
}

/// include_str!() 引用的 gitignored 文件，clone 后不存在会导致编译失败
fn ensure_gitignored_placeholders() {
    let persona_path = std::path::Path::new("..").join("..").join("..").join("identity").join("personas").join("user_custom.md");
    if !persona_path.exists() {
        let _ = std::fs::create_dir_all(persona_path.parent().unwrap());
        let _ = std::fs::write(&persona_path, "# User Custom Persona (placeholder)\n");
    }
}

fn ensure_placeholder_windows_icon() {
    use base64::Engine;
    use flate2::read::GzDecoder;
    use std::io::Read;

    // Only needed for Windows targets, but keep it harmless on others.
    let icons_dir = std::path::Path::new("icons");
    let icon_path = icons_dir.join("icon.ico");
    if std::env::var("OPENAKITA_SETUP_CENTER_SKIP_ICON").ok().as_deref() == Some("1") {
        return;
    }
    // 如果仓库/项目已经提供了 icon.ico（例如通过 `tauri icon` 生成），不要覆盖它。
    if icon_path.exists() {
        return;
    }

    // 占位 ICO（16x16 透明），用 gzip+base64 存储以避免超长字符串被截断。
    // Source: KEINOS/blank_favicon_ico (gzip base64)
    const ICO_GZ_B64: &str =
        "H4sIAAAAAAAAA2NgYARCAQEGIKnAkMHCwCDGwMCgAcRAIaAIRBwX+P///ygexaN4xGIGijAASeibMX4EAAA=";

    let Ok(gz_bytes) = base64::engine::general_purpose::STANDARD.decode(ICO_GZ_B64) else {
        return;
    };

    let mut decoder = GzDecoder::new(&gz_bytes[..]);
    let mut bytes = Vec::new();
    if decoder.read_to_end(&mut bytes).is_err() {
        return;
    }

    let _ = std::fs::create_dir_all(icons_dir);
    let _ = std::fs::write(icon_path, bytes);
}


