# JWXT automatic schedule sync

JWXT schedule sync first uses the local cookie cache configured by
`JWXT_COOKIES_PATH` (default: `data/jwxt_cookies.json`).

To create or refresh this file through a manual school login:

```powershell
python scripts/refresh_jwxt_state.py
```

The tool opens a visible browser. Complete the login in that browser, then
return to the terminal and press Enter. It verifies the browser cookies against
the JWXT schedule API before atomically replacing `data/jwxt_cookies.json`.
Cookie values, passwords, and tokens are not printed.

After a successful refresh, the Web system page's “同步课表” action uses the
same `JWXT_COOKIES_PATH` file automatically.

When the schedule request is redirected to the login page, the session is
treated as expired. If both `JWXT_USERNAME` and `JWXT_PASSWORD` are configured
in the local `.env`, Playwright attempts the existing username/password login
form, saves refreshed cookies, and retries the schedule request.

Captcha, SSO, and QR-code authentication are not bypassed. The Web/API sync
result reports a specific JWXT error code when user action is required.

Keep `.env` and `data/jwxt_cookies.json` local. Do not commit either file.
Logs and API responses report only configuration presence flags and sanitized
error codes; they do not include credential or cookie values.
