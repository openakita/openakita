'use strict';

var path = require('path');
var config = require('./config.js');

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

var path__namespace = /*#__PURE__*/_interopNamespaceDefault(path);

/** 插件扩展目录 */
const EXTENSIONS_DIR = config.getExtensionsDir();
/** 插件名称 */
const PLUGIN_NAME = 'wecom-openclaw-plugin';
/** Channel 名称 */
const CHANNEL_NAME = 'wecom';
/** 插件安装路径 */
const PLUGIN_PATH = path__namespace.join(EXTENSIONS_DIR, PLUGIN_NAME);
/** 默认 npm 包名 */
const DEFAULT_PACKAGE_NAME = '@wecom/wecom-openclaw-plugin';
/** 冲突的插件名称列表 */
const CONFLICT_PLUGINS = ['wecom', 'openclaw-wecom', 'wecom-openclaw'];
/** 冲突的插件路径列表 */
const CONFLICT_PLUGIN_PATHS = CONFLICT_PLUGINS.map(name => path__namespace.join(EXTENSIONS_DIR, name));
/** npm 源列表（按优先级排序，第一个失败后依次尝试后续源） */
const NPM_REGISTRIES = [
    'https://registry.npmjs.org/',
    'http://mirrors.cloud.tencent.com/npm/',
];

exports.CHANNEL_NAME = CHANNEL_NAME;
exports.CONFLICT_PLUGINS = CONFLICT_PLUGINS;
exports.CONFLICT_PLUGIN_PATHS = CONFLICT_PLUGIN_PATHS;
exports.DEFAULT_PACKAGE_NAME = DEFAULT_PACKAGE_NAME;
exports.EXTENSIONS_DIR = EXTENSIONS_DIR;
exports.NPM_REGISTRIES = NPM_REGISTRIES;
exports.PLUGIN_NAME = PLUGIN_NAME;
exports.PLUGIN_PATH = PLUGIN_PATH;
//# sourceMappingURL=constants.js.map
