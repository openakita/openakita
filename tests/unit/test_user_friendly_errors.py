from openakita.utils.errors import format_user_friendly_error


def test_format_user_friendly_error_hides_plugin_cache_permission_path():
    raw = (
        "[WinError 5] 拒绝访问。: "
        "'C:\\Users\\Peilong_Hong\\.openakita\\modules\\happyhorse-video\\"
        "site-packages-py311-runtime\\certifi-2026.4.22.dist-info'"
    )

    msg = format_user_friendly_error(raw)

    assert "插件依赖缓存目录权限异常" in msg
    assert "C:\\Users" not in msg
    assert "certifi-2026.4.22.dist-info" not in msg


def test_format_user_friendly_error_hides_traceback_path():
    raw = (
        'Traceback (most recent call last):\n'
        '  File "D:\\OpenAkita\\src\\openakita\\foo.py", line 12, in run\n'
        "RuntimeError: boom"
    )

    msg = format_user_friendly_error(raw)

    assert msg == "⚠️ 处理出错，详细错误已记录到本地日志，请稍后重试。"
    assert "D:\\OpenAkita" not in msg


def test_format_user_friendly_error_simplifies_wework_credential_error():
    raw = (
        "连续 3 次认证失败 (错误: 853000 invalid bot_id or secret, "
        "hint: [1779005048242621460379807], from ip: 27.156.101.242, "
        "more info at https://open.work.weixin.qq.com/devtool/query?e=853000)。"
        "请检查企业微信 Bot ID / Secret 配置"
    )

    msg = format_user_friendly_error(raw)

    assert "企业微信 Bot ID / Secret 配置无效" in msg
    assert "1779005048242621460379807" not in msg
    assert "27.156.101.242" not in msg
    assert "open.work.weixin.qq.com" not in msg


def test_format_user_friendly_error_simplifies_telegram_token_error():
    raw = "Telegram Bot Token 验证失败，请到 @BotFather 重新获取 Token 后重试"

    msg = format_user_friendly_error(raw)

    assert "Telegram Bot Token 无效或未配置" in msg
    assert "@BotFather" in msg
