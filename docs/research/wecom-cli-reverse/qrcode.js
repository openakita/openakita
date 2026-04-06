'use strict';

var https = require('https');
var os = require('os');
var chalk = require('chalk');
var index = require('../_virtual/index.js');

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

var https__namespace = /*#__PURE__*/_interopNamespaceDefault(https);
var os__namespace = /*#__PURE__*/_interopNamespaceDefault(os);

function getPlatCode() {
    switch (os__namespace.platform()) {
        case 'darwin':
            return 1;
        case 'win32':
            return 2;
        case 'linux':
            return 3;
        default:
            return 0;
    }
}
const QR_GENERATE_URL = `https://work.weixin.qq.com/ai/qc/generate?source=wecom-cli&plat=${getPlatCode()}`;
const QR_QUERY_URL = 'https://work.weixin.qq.com/ai/qc/query_result';
const POLL_INTERVAL = 3000; // 轮询间隔 3 秒
const POLL_TIMEOUT = 180000; // 超时 3 分钟
/**
 * 发起 HTTPS GET 请求
 */
function httpsGet(url) {
    return new Promise((resolve, reject) => {
        https__namespace
            .get(url, (res) => {
            let data = '';
            res.on('data', (chunk) => (data += chunk));
            res.on('end', () => resolve(data));
        })
            .on('error', reject);
    });
}
/**
 * 获取二维码链接和轮询 scode
 */
async function fetchQRCode() {
    const raw = await httpsGet(QR_GENERATE_URL);
    const resp = JSON.parse(raw);
    if (!resp?.data?.scode || !resp?.data?.auth_url) {
        throw new Error('获取二维码失败，响应格式异常');
    }
    return { scode: resp.data.scode, auth_url: resp.data.auth_url };
}
/**
 * 在终端渲染二维码
 */
async function renderQRCode(url) {
    const qr = await index.toString(url, { type: 'terminal', small: true });
    console.log('');
    console.log(qr);
}
/**
 * 轮询扫码结果
 */
async function pollResult(scode) {
    const startTime = Date.now();
    const url = `${QR_QUERY_URL}?scode=${encodeURIComponent(scode)}`;
    while (Date.now() - startTime < POLL_TIMEOUT) {
        const raw = await httpsGet(url);
        const resp = JSON.parse(raw);
        const status = resp?.data?.status;
        if (status === 'success') {
            const botInfo = resp.data.bot_info;
            if (!botInfo?.botid || !botInfo?.secret) {
                throw new Error('扫码成功但未获取到 Bot 信息');
            }
            return { botid: botInfo.botid, secret: botInfo.secret };
        }
        // 继续等待
        await new Promise((r) => setTimeout(r, POLL_INTERVAL));
    }
    throw new Error('扫码超时（3 分钟），请重试');
}
/**
 * 扫码接入完整流程：获取二维码 → 终端展示 → 轮询结果 → 返回 botId 和 secret
 */
async function scanQRCodeForBotInfo() {
    console.log(chalk.blue('正在获取二维码...'));
    const { scode, auth_url } = await fetchQRCode();
    console.log(chalk.green('请使用企业微信扫描以下二维码：'));
    await renderQRCode(auth_url);
    console.log(chalk.blue('等待扫码中...'));
    const botInfo = await pollResult(scode);
    console.log(chalk.green('✔ 扫码成功！Bot ID 和 Secret 已自动获取。'));
    return { botId: botInfo.botid, secret: botInfo.secret };
}

exports.scanQRCodeForBotInfo = scanQRCodeForBotInfo;
//# sourceMappingURL=qrcode.js.map
