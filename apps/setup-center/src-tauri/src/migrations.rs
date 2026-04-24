//! 配置文件版本迁移框架
//!
//! 每次发版如果配置结构发生变化，在此添加迁移函数。
//! 应用启动时自动执行，链式升级：v1 → v2 → v3 → ... → 当前版本。

use serde_json::Value;
use std::fs;
use std::path::Path;

/// 当前配置文件版本。每次添加迁移时递增此值。
pub const CURRENT_CONFIG_VERSION: u32 = 1;

type MigrationFn = fn(state: &mut Value, root: &Path) -> Result<(), String>;

/// 返回所有已注册的迁移。
/// 元组格式: (目标版本号, 迁移函数)
fn get_migrations() -> Vec<(u32, MigrationFn)> {
    vec![
        // 示例（下一个版本需要迁移时取消注释并实现）：
        // (2, migrate_v1_to_v2),
    ]
}

/// 运行所有必要的迁移，从 current_version 升级到 CURRENT_CONFIG_VERSION。
///
/// - 迁移前自动备份 state.json
/// - 迁移是单向的（不支持降级）
/// - 如果没有需要执行的迁移，直接返回 Ok
pub fn run_migrations(state_path: &Path, root: &Path) -> Result<(), String> {
    if !state_path.exists() {
        return Ok(());
    }

    let content = fs::read_to_string(state_path)
        .map_err(|e| format!("read state.json failed: {e}"))?;
    let mut state: Value = serde_json::from_str(&content)
        .map_err(|e| format!("parse state.json failed: {e}"))?;

    let current_version = state
        .get("configVersion")
        .and_then(|v| v.as_u64())
        .unwrap_or(1) as u32;

    if current_version >= CURRENT_CONFIG_VERSION {
        // 确保 configVersion 字段存在
        if state.get("configVersion").is_none() {
            state["configVersion"] = serde_json::json!(CURRENT_CONFIG_VERSION);
            let data = serde_json::to_string_pretty(&state)
                .map_err(|e| format!("serialize state.json failed: {e}"))?;
            fs::write(state_path, data)
                .map_err(|e| format!("write state.json failed: {e}"))?;
        }
        return Ok(());
    }

    // 备份当前 state.json
    let backup_name = format!(
        "state.json.backup-v{}",
        current_version
    );
    let backup_path = root.join(&backup_name);
    if let Err(e) = fs::copy(state_path, &backup_path) {
        eprintln!("Warning: could not backup state.json: {e}");
    } else {
        eprintln!("Config backup: {backup_name}");
    }

    // 执行迁移链
    for (target_version, migrate_fn) in get_migrations() {
        if current_version < target_version {
            eprintln!("Running migration: v{} → v{}", current_version, target_version);
            migrate_fn(&mut state, root)?;
            state["configVersion"] = serde_json::json!(target_version);
        }
    }

    // 确保 configVersion 至少为 CURRENT_CONFIG_VERSION
    state["configVersion"] = serde_json::json!(CURRENT_CONFIG_VERSION);

    // 写回
    let data = serde_json::to_string_pretty(&state)
        .map_err(|e| format!("serialize state.json failed: {e}"))?;
    fs::write(state_path, data)
        .map_err(|e| format!("write state.json failed: {e}"))?;

    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════
// 迁移函数区域 — 每个版本的迁移函数放在下面
// ═══════════════════════════════════════════════════════════════════════

// 示例迁移函数（留作参考，下一次需要迁移时照此模式添加）：
//
// fn migrate_v1_to_v2(state: &mut Value, root: &Path) -> Result<(), String> {
//     // 例如：重命名字段、添加新字段、迁移工作区配置等
//     if let Some(obj) = state.as_object_mut() {
//         // 添加新字段的默认值
//         obj.entry("newField").or_insert(serde_json::json!("default"));
//     }
//     Ok(())
// }

