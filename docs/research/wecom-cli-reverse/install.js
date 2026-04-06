'use strict';

var fs = require('fs-extra');
var path = require('path');
var chalk = require('chalk');
var system = require('../utils/system.js');
var config = require('../utils/config.js');
var prompts = require('../utils/prompts.js');
var qrcode = require('../utils/qrcode.js');
var update = require('./update.js');
var constants = require('../utils/constants.js');
var plugin = require('../utils/plugin.js');

function _interopNamespaceDefault(e) {
    var n = Object.create(null);
    if (e) {
        Object.keys(e).forEach(function (k) {
            if (k !== 'default') {
                var d = Object.getOwnPropertyDescriptor(e, k);
                Object.defineProperty(n, k, d.get ? d : {
                    enumerable: true,
                    get: function () { return e[k]; }
                });
            }
        });
    }
    n.default = e;
    return Object.freeze(n);
}

var fs__namespace = /*#__PURE__*/_interopNamespaceDefault(fs);
var path__namespace = /*#__PURE__*/_interopNamespaceDefault(path);

/**
 * 备份并移除 channels.wecom 配置
 * @returns 如果之前有完整配置（botId 和 secret 都有），返回备份；否则返回 null
 */
async function backupAndRemoveChannelConfig() {
    const config$1 = await config.readConfig();
    let backup = null;
    if (config$1.channels?.[constants.CHANNEL_NAME]) {
        const channelConfig = config$1.channels[constants.CHANNEL_NAME];
        // 只有 botId 和 secret 都有时才备份
        if (channelConfig.botId && channelConfig.secret) {
            backup = { ...channelConfig };
            console.log(chalk.blue(`已备份 channels.${constants.CHANNEL_NAME} 配置。`));
        }
        // 无论是否备份成功，都删除 channels.wecom 配置
        delete config$1.channels[constants.CHANNEL_NAME];
        console.log(chalk.blue(`已移除 channels.${constants.CHANNEL_NAME} 配置。`));
        await config.writeConfig(config$1);
    }
    return backup;
}
/**
 * 安装后：处理 channels.wecom 配置还原或新建
 * @param backup 之前备份的配置（可能为 null）
 * @param skipConfig 是否跳过配置引导
 */
async function handleChannelConfigAfterInstall(backup, skipConfig) {
    const config$1 = await config.readConfig();
    if (!config$1.channels)
        config$1.channels = {};
    if (backup) {
        // 之前有完整配置
        if (skipConfig) {
            // --skip-config：默认恢复
            config$1.channels[constants.CHANNEL_NAME] = backup;
            console.log(chalk.green(`已自动恢复 channels.${constants.CHANNEL_NAME} 配置。`));
        }
        else {
            // 未指定 --skip-config：引导用户选择是否恢复
            const restore = await prompts.promptRestoreChannelConfig(backup.botId);
            if (restore) {
                config$1.channels[constants.CHANNEL_NAME] = backup;
                console.log(chalk.green(`已恢复 channels.${constants.CHANNEL_NAME} 配置。`));
            }
            else {
                // 用户不恢复，走新建引导
                await configureNewChannel(config$1);
            }
        }
    }
    else {
        // 之前没有完整配置
        if (skipConfig) {
            // --skip-config：不出引导，跳过
            console.log(chalk.yellow(`跳过 channels.${constants.CHANNEL_NAME} 配置（无历史配置可恢复）。`));
        }
        else {
            // 未指定 --skip-config：出引导配置
            await configureNewChannel(config$1);
        }
    }
    // 更新 plugins.allow
    if (!config$1.plugins)
        config$1.plugins = {};
    if (!config$1.plugins.allow)
        config$1.plugins.allow = [];
    if (!config$1.plugins.allow.includes(constants.PLUGIN_NAME)) {
        config$1.plugins.allow.push(constants.PLUGIN_NAME);
    }
    // 更新 tools 配置：优先写入已有的 allow/alsoAllow，都没有则默认写入 alsoAllow
    if (!config$1.tools)
        config$1.tools = {};
    const hasAllow = Array.isArray(config$1.tools.allow);
    const hasAlsoAllow = Array.isArray(config$1.tools.alsoAllow);
    if (hasAllow) {
        if (!config$1.tools.allow.includes('wecom_mcp')) {
            config$1.tools.allow.push('wecom_mcp');
        }
    }
    if (hasAlsoAllow) {
        if (!config$1.tools.alsoAllow.includes('wecom_mcp')) {
            config$1.tools.alsoAllow.push('wecom_mcp');
        }
    }
    if (!hasAllow && !hasAlsoAllow) {
        config$1.tools.alsoAllow = ['wecom_mcp'];
    }
    await config.writeConfig(config$1);
}
/**
 * 引导用户配置新的 channel
 */
async function configureNewChannel(config) {
    console.log(chalk.blue('正在配置 channels...'));
    if (!config.channels)
        config.channels = {};
    if (!config.channels[constants.CHANNEL_NAME]) {
        config.channels[constants.CHANNEL_NAME] = {
            enabled: true,
            botId: '',
            secret: '',
        };
    }
    // 判断是否需要重新配置 botId 和 secret
    let needBind = false;
    if (config.channels[constants.CHANNEL_NAME].botId) {
        const confirm = await prompts.promptConfirmBotId(config.channels[constants.CHANNEL_NAME].botId);
        if (!confirm) {
            needBind = true;
        }
    }
    else {
        needBind = true;
    }
    if (needBind) {
        const method = await prompts.promptBindMethod();
        if (method === 'qrcode') {
            const { botId, secret } = await qrcode.scanQRCodeForBotInfo();
            config.channels[constants.CHANNEL_NAME].botId = botId;
            config.channels[constants.CHANNEL_NAME].secret = secret;
        }
        else {
            config.channels[constants.CHANNEL_NAME].botId = await prompts.promptBotId();
            config.channels[constants.CHANNEL_NAME].secret = await prompts.promptSecret();
        }
    }
    else if (!config.channels[constants.CHANNEL_NAME].secret) {
        // 继续使用已有 botId，但 secret 缺失时补充
        config.channels[constants.CHANNEL_NAME].secret = await prompts.promptSecret();
    }
}
/**
 * install 命令
 *
 * 流程概览：
 * ┌─ force 模式 ─────────────────────────────────────┐
 * │ 1. 禁用冲突插件并清理 plugins 配置                   │
 * │ 2. 移除冲突插件目录和当前插件目录                      │
 * │ 3. 备份并移除 channels.wecom → 安装插件 → 处理配置还原 │
 * └──────────────────────────────────────────────────┘
 * ┌─ 已安装（更新模式）──────────────────────────────────┐
 * │ 直接执行更新，不处理 channels 配置                     │
 * └──────────────────────────────────────────────────┘
 * ┌─ 全新安装 ───────────────────────────────────────────┐
 * │ 1. 校验版本号 / 检查 openclaw 版本                     │
 * │ 2. 禁用冲突插件并移除冲突目录                           │
 * │ 3. 备份并移除 channels.wecom → 安装插件 → 处理配置还原  │
 * └──────────────────────────────────────────────────┘
 */
async function installCommand(options) {
    console.log(chalk.blue('开始安装...'));
    // =============================================
    // 分支一：force 模式 — 清理一切，重新安装
    // =============================================
    if (options.force) {
        console.log(chalk.yellow('强制安装模式，正在清理已有安装...'));
        // 步骤 1：禁用冲突插件并清理 plugins 配置
        console.log(chalk.blue('正在禁用冲突插件并清理配置...'));
        const currentConfig = await config.readConfig();
        const cleanedConfig = await plugin.disableConflictPlugins(currentConfig);
        // 移除插件本身的 plugins 配置（保留 channels，由后面统一处理）
        plugin.removePluginConfig(cleanedConfig, constants.PLUGIN_NAME);
        await config.writeConfig(cleanedConfig);
        // 步骤 2：移除冲突插件目录和当前插件目录
        for (const conflictPath of constants.CONFLICT_PLUGIN_PATHS) {
            if (await fs__namespace.pathExists(conflictPath)) {
                console.log(chalk.blue(`正在移除冲突的插件目录: ${conflictPath}...`));
                await fs__namespace.remove(conflictPath);
            }
        }
        if (await fs__namespace.pathExists(constants.PLUGIN_PATH)) {
            console.log(chalk.blue(`正在移除插件目录: ${constants.PLUGIN_PATH}...`));
            await fs__namespace.remove(constants.PLUGIN_PATH);
        }
        // 步骤 3：备份并移除 channels.wecom → 安装插件 → 处理配置还原
        const channelBackup = await backupAndRemoveChannelConfig();
        console.log(chalk.blue('正在安装官方 WeCom 插件...'));
        if (!plugin.installPluginFromRegistry(options.version, options.packageName)) {
            process.exit(1);
        }
        await handleChannelConfigAfterInstall(channelBackup, !!options.skipConfig);
        // =============================================
        // 分支二：已安装 — 走更新流程
        // =============================================
    }
    else if (await fs__namespace.pathExists(constants.PLUGIN_PATH)) {
        console.log(chalk.yellow('插件已安装，开始更新流程...'));
        const newVersion = await update.updateCommand(undefined, true);
        console.log(chalk.green(newVersion ? `更新完成！新版本: ${newVersion}` : '更新完成！'));
        return;
        // =============================================
        // 分支三：全新安装
        // =============================================
    }
    else {
        // 步骤 1：校验版本号参数
        if (options.version && !system.validateVersion(options.version)) {
            console.error(chalk.red('错误: 版本号格式不合法，只允许数字、点、连字符和字母。'));
            process.exit(1);
        }
        // 步骤 2：检查 openclaw 版本
        try {
            const openclawCmd = system.getPlatformCommand('openclaw');
            const version = system.runCommandQuiet(`${openclawCmd} --version`);
            if (plugin.compareVersions(version, '2026.2.13') < 0) {
                console.error(chalk.red(`错误: OpenClaw 版本不匹配，需要 >= 2026.2.13，当前版本 ${version}，请升级： openclaw update。`));
                process.exit(1);
            }
        }
        catch (error) {
            const isWin = process.platform === 'win32';
            console.error(chalk.red('错误: OpenClaw 未安装或未添加到 PATH 环境变量。 https://openclaw.ai/'));
            if (isWin) {
                console.error(chalk.yellow('提示: Windows 用户请确认已将 OpenClaw 安装目录添加到系统 PATH 中，安装后可能需要重启终端。'));
            }
            process.exit(1);
        }
        // 步骤 3：禁用冲突插件
        console.log(chalk.blue('正在禁用冲突插件...'));
        await plugin.disableConflictPlugins();
        // 步骤 4：移除冲突目录
        for (const conflictPath of constants.CONFLICT_PLUGIN_PATHS) {
            if (await fs__namespace.pathExists(conflictPath)) {
                console.log(chalk.blue(`正在移除冲突的插件目录: ${conflictPath}...`));
                await fs__namespace.remove(conflictPath);
            }
        }
        // 步骤 5：备份并移除 channels.wecom → 安装插件 → 处理配置还原
        const channelBackup = await backupAndRemoveChannelConfig();
        console.log(chalk.blue('正在安装官方 WeCom 插件...'));
        if (!plugin.installPluginFromRegistry(options.version, options.packageName)) {
            process.exit(1);
        }
        await handleChannelConfigAfterInstall(channelBackup, !!options.skipConfig);
    }
    // =============================================
    // 公共步骤：验证安装结果 & 重启网关
    // =============================================
    console.log(chalk.blue('正在验证安装...'));
    const finalConfig = await config.readConfig();
    const checks = [
        finalConfig.plugins?.allow?.includes(constants.PLUGIN_NAME),
        await fs__namespace.pathExists(path__namespace.join(constants.PLUGIN_PATH, 'node_modules')),
    ];
    if (checks.every(Boolean)) {
        console.log(chalk.green('安装完成！'));
        // 重启网关
        console.log(chalk.blue('正在重启网关...'));
        try {
            const openclawCmd = system.getPlatformCommand('openclaw');
            system.runCommand(`${openclawCmd} gateway restart`);
            console.log(chalk.green('网关重启成功！'));
        }
        catch (error) {
            console.warn(chalk.yellow('网关重启失败，请手动执行: openclaw gateway restart'));
        }
    }
    else {
        console.warn(chalk.yellow('安装已完成，但部分检查未通过。请运行 "doctor" 命令进行诊断。'));
    }
}

exports.installCommand = installCommand;
//# sourceMappingURL=install.js.map
