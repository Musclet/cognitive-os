# JWXT automatic schedule sync

JWXT schedule sync first uses the local cookie cache configured by
`JWXT_COOKIES_PATH` (default: `data/jwxt_cookies.json`).

When the schedule request is redirected to the login page, the session is
treated as expired. If both `JWXT_USERNAME` and `JWXT_PASSWORD` are configured
in the local `.env`, Playwright attempts the existing username/password login
form, saves refreshed cookies, and retries the schedule request.

Captcha, SSO, and QR-code authentication are not bypassed. The Web/API sync
result reports a specific JWXT error code when user action is required.

Keep `.env` and `data/jwxt_cookies.json` local. Do not commit either file.
Logs and API responses report only configuration presence flags and sanitized
error codes; they do not include credential or cookie values.
