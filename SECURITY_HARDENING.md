# Security Hardening — 2026-06-09

## 概述

对 cognitive-os 进行安全加固，移除硬编码敏感值、强化 API 鉴权、修复生产环境配置。**零业务功能变更，零回归。**

## 修改清单

### A. `render.yaml` — 移除硬编码密钥

| 变更 | 原因 |
|------|------|
| `DATABASE_URL` 含真实 PostgreSQL 连接串 → `sync: false` | 密码泄露风险，从 Render 环境变量注入 |
| `WEB_UI_PIN` 硬编码 `"564563"` → `sync: false` | 凭据不入仓库 |
| `TELEGRAM_BOT_TOKEN` → `sync: false` | 统一管理 |
| 新增 `INSPECTOR_ADMIN_TOKEN` (generateValue) | 新增的 Inspector API 鉴权 |
| 新增 `ALLOWED_ORIGINS` = `https://cognitive-os.onrender.com` | CORS 白名单 |
| 新增 `WEB_UI_COOKIE_SECURE` = `true` | 生产 HTTPS 下 cookie 安全标记 |

### B. `.env.example` — 补全环境变量模板

- 新增 `INSPECTOR_ADMIN_TOKEN`、`ALLOWED_ORIGINS`、`WEB_UI_COOKIE_SECURE` 说明
- `MOMO_SYNC_PROJECT_PATH`、`MOMO_CACHE_PATH`、`OBSIDIAN_VAULT_PATH` 默认值改为空
- `WEB_UI_PIN` 默认值改为空（强制部署时配置）
- 顶部增加必需环境变量注释区块

### C. `src/infrastructure/config.py` — 默认值去敏感化

| 字段 | 旧默认值 | 新默认值 |
|------|---------|---------|
| `momo_sync_project_path` | `C:/Users/admin/Documents/New project 2/momo-obsidian-sync` | `""` |
| `momo_cache_path` | `C:/Users/admin/Documents/New project 2/momo-obsidian-sync/cache/momo-data.json` | `""` |
| `obsidian_vault_path` | `C:/Users/admin/OneDrive/桐一日` | `""` |
| `web_ui_pin` | `"0000"` | `""` |
| **新增** `web_ui_cookie_secure` | — | `True` |
| **新增** `inspector_admin_token` | — | `""` |
| **新增** `allowed_origins` | — | `"http://localhost:5173,http://localhost:8081"` |

### D. `src/interface/api/app.py` — Inspector API 鉴权 + CORS 修复

**新增 Admin Token 鉴权：**
- 函数 `_require_inspector_admin(request)` — 从 `Authorization: Bearer <token>` 或 `X-Admin-Token` 读取
- ASGI middleware `_inspector_auth_middleware` — 保护以下路径前缀：
  - `/events` `/state` `/snapshots` `/dead-letter` `/traces` `/scheduler/jobs` `/stats` `/aggregates` `/dashboard`
- `inspector_admin_token` 为空 → 所有 Inspector API 返回 `403 inspector_api_disabled`
- OPTIONS 预检请求自动放行（CORS 兼容）
- `/api/web/auth/*`、`/app/*`、`/api/workout/*` **不受影响**

**CORS 修复：**
- `allow_origins=["*"]` → 从 `settings.allowed_origins` 读取（逗号分隔列表）
- 默认值 `http://localhost:5173,http://localhost:8081`（仅本地开发）

**`create_app()` 签名变更：**
- 新增 `settings=None` 参数
- `scripts/run.py` 和 `scripts/render_run.py` 同步更新

### E. `src/interface/api/web_routes.py` — Cookie Secure 可配置

```python
# 旧
secure=False,  # local dev; set True in production behind HTTPS

# 新
cookie_secure = bool(getattr(settings, "web_ui_cookie_secure", True))
secure=cookie_secure,
```

### F. `src/storage/db.py` + `event_store.py` + `snapshot_store.py` — Docstring 修正

| 文件 | 旧 | 新 |
|------|----|----|
| `db.py` | "Shared SQLite async database connection" | "Shared async database connection (supports SQLite and PostgreSQL via SQLAlchemy)" |
| `event_store.py` | "Append-only event log backed by SQLite" | "Append-only event log backed by the configured database (SQLite or PostgreSQL)" |
| `snapshot_store.py` | "durable snapshot persistence in SQLite" | "durable snapshot persistence (SQLite/PostgreSQL via SQLAlchemy)" |

### G. 新增 `tests/unit/test_security.py` — 20 个安全测试

| 测试类 | 数量 | 覆盖 |
|--------|------|------|
| `TestInspectorAdminAuth` | 12 | 无 token→403, Bearer/X-Admin-Token→200, 错误 token→403, 空配置→禁用 |
| `TestWebAuthSecurity` | 4 | dashboard 无 session→401, login 设 cookie, secure 可配, auth check→401 |
| `TestCorsConfig` | 2 | 不返回 `*` 通配符, localhost origin 允许 |
| `TestInspectorDoesNotBlockWebUi` | 2 | login 和 /app SPA 不被 Inspector 拦截 |

## 验证结果

```
ruff:    All checks passed (0 errors)
pytest:  20/20 new security tests passed
         153/153 existing web_ui tests passed
         880/886 total unit tests passed (6 个预存失败，与本次改动无关)
```

## 部署前检查

在 Render 上部署前，确保已设置以下环境变量：

- [ ] `DATABASE_URL` — PostgreSQL 连接串
- [ ] `WEB_UI_PIN` — Web 控制台解锁 PIN
- [ ] `WEB_UI_SESSION_SECRET` — 自动生成 ✓
- [ ] `INSPECTOR_ADMIN_TOKEN` — 自动生成 ✓（记下这个值，访问 Inspector 用）
- [ ] `ALLOWED_ORIGINS` — 已设 `https://cognitive-os.onrender.com`
- [ ] `WEB_UI_COOKIE_SECURE` — 已设 `true`
- [ ] `TELEGRAM_BOT_TOKEN` — 如需 Telegram Bot
