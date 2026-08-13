# OpenAkita Account PKCE integration

OpenAkita Desktop uses the public OIDC client `openakita-desktop`. It never has
a client secret. The backend opens a temporary loopback listener on
`127.0.0.1:1455`, generates a fresh state and S256 PKCE verifier, and returns
the Account authorization URL to Setup Center.

```text
GET  /api/account/capability
POST /api/account/login/start
GET  /api/account/login/status/{attempt_id}
GET  /api/account/status
POST /api/account/entitlements/refresh
POST /api/account/logout
```

The registered redirect URI is
`http://127.0.0.1:1455/auth/callback`. Refresh tokens are stored only in the OS
credential store through `keyring`; there is no plaintext file fallback.
Access tokens stay in process memory. A successful login fetches `/oauth/userinfo`
and `/api/v1/me/entitlements`, then persists only the identity and entitlement
read model in `data/account_identity.db`.

Feature gates must evaluate the cached entitlement status and expiry at read
time. A central `suspended` event revokes Account sessions in that database and
all Account-backed operations fail immediately. The existing local web password
remains a separate break-glass path and is not revoked by Account suspension.

Setup Center's sidebar account menu drives these endpoints, polls the loopback
attempt, shows the cached account/entitlement status, and lets the user refresh
entitlements. Logout clears the local OS credential without opening another
browser tab.

Account integration is a distribution-level capability with three modes:

```text
OPENAKITA_ACCOUNT_MODE=openakita  # official hosted service (default)
OPENAKITA_ACCOUNT_MODE=custom     # OEM identity service
OPENAKITA_ACCOUNT_MODE=disabled   # no account routes, credentials, or account UI
```

The official mode uses the hosted service by default:

```text
OPENAKITA_ACCOUNT_BASE_URL=https://account.fzstack.com
OPENAKITA_ACCOUNT_CLIENT_ID=openakita-desktop
```

Override `OPENAKITA_ACCOUNT_BASE_URL` only when testing against a local Account
service. Custom mode requires an explicit `OPENAKITA_ACCOUNT_BASE_URL` and
`OPENAKITA_ACCOUNT_CLIENT_ID`; `OPENAKITA_ACCOUNT_DISPLAY_NAME` and
`OPENAKITA_ACCOUNT_PROVIDER` customize the provider identity shown to users.
Provider credentials use separate OS-vault slots, so an OEM token is never sent
to the official service or another custom provider.

When account mode is disabled, only `GET /api/account/capability` remains
mounted so the frontend can render an account-free application menu. OAuth,
status, entitlement, logout, and status-propagation routes are absent, and
startup clears locally stored account refresh tokens. Core local OpenAkita
features continue to work without an account.

An account-enabled always-on server may additionally expose the signed D26 receiver at
`POST /api/internal/openakita/users/status`. Desktop-only processes must not be
configured as Account Outbox targets because they have no stable ingress.
