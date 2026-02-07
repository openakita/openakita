fn main() {
    // 开发/CI 友好：如果缺少 Windows icon.ico，则自动生成一个极简占位图标，
    // 避免 `tauri-build` 在 Windows 上直接失败。
    //
    // 注意：这里生成的只是占位图标。正式发布建议用 `tauri icon` 生成完整图标集。
    ensure_placeholder_windows_icon();

    tauri_build::build()
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

