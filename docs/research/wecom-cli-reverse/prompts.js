'use strict';

var inquirer = require('inquirer');

/**
 * 提示用户选择接入方式
 */
async function promptBindMethod() {
    const answer = await inquirer.prompt([
        {
            type: 'list',
            name: 'method',
            message: '请选择企微机器人接入方式:',
            choices: [
                { name: '扫码接入（推荐）', value: 'qrcode' },
                { name: '手动输入 Bot ID 和 Secret', value: 'manual' },
            ],
        },
    ]);
    return answer.method;
}
/**
 * 同时提示输入 botId 和 secret
 */
async function promptBotIdSecret() {
    return inquirer.prompt([
        {
            type: 'input',
            name: 'botId',
            message: '请输入企业微信机器人 Bot ID:',
            validate: (input) => (input ? true : 'Bot ID 不能为空'),
        },
        {
            type: 'password',
            name: 'secret',
            message: '请输入企业微信机器人 Secret:',
            mask: '*',
            validate: (input) => (input ? true : 'Secret 不能为空'),
        },
    ]);
}
/**
 * 确认是否使用已有的 botId
 */
async function promptConfirmBotId(currentBotId) {
    const answer = await inquirer.prompt([
        {
            type: 'confirm',
            name: 'confirm',
            message: `当前企业微信机器人 Bot ID 为 ${currentBotId}，是否继续使用？`,
            default: true,
        },
    ]);
    return answer.confirm;
}
/**
 * 单独提示输入 botId
 */
async function promptBotId() {
    const answer = await inquirer.prompt([
        {
            type: 'input',
            name: 'botId',
            message: '请输入企业微信机器人 Bot ID:',
            validate: (input) => (input ? true : 'Bot ID 不能为空'),
        },
    ]);
    return answer.botId;
}
/**
 * 确认是否还原之前的 channels.wecom 配置
 */
async function promptRestoreChannelConfig(botId) {
    const answer = await inquirer.prompt([
        {
            type: 'confirm',
            name: 'confirm',
            message: `检测到之前的 channels.wecom 配置（Bot ID: ${botId}），是否还原？`,
            default: true,
        },
    ]);
    return answer.confirm;
}
/**
 * 单独提示输入 secret
 */
async function promptSecret() {
    const answer = await inquirer.prompt([
        {
            type: 'password',
            name: 'secret',
            message: '请输入企业微信机器人 Secret:',
            mask: '*',
            validate: (input) => (input ? true : 'Secret 不能为空'),
        },
    ]);
    return answer.secret;
}

exports.promptBindMethod = promptBindMethod;
exports.promptBotId = promptBotId;
exports.promptBotIdSecret = promptBotIdSecret;
exports.promptConfirmBotId = promptConfirmBotId;
exports.promptRestoreChannelConfig = promptRestoreChannelConfig;
exports.promptSecret = promptSecret;
//# sourceMappingURL=prompts.js.map
