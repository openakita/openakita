from openakita.api.auth import WebAccessConfig
from openakita.core.auth.tokens import decode_jwt


def test_web_access_tokens_use_shared_claim_shape(tmp_path):
    cfg = WebAccessConfig(tmp_path)

    access = decode_jwt(cfg.create_access_token(), cfg.jwt_secret)
    refresh = decode_jwt(cfg.create_refresh_token(), cfg.jwt_secret)

    assert access is not None
    assert refresh is not None
    assert access["type"] == "access"
    assert refresh["type"] == "refresh"
    assert access["sub"] == "desktop_user"
    assert refresh["sub"] == "desktop_user"
    assert access["scope"] == ["web:access"]
    assert refresh["scope"] == ["web:refresh"]
    assert access["jti"]
    assert refresh["jti"]
