# Render Cloud Mode

Cognitive OS uses one shared cloud state so the phone and desktop see the
same data:

- Render free Web service: React/PWA + FastAPI + connector runtime.
- Neon Postgres: durable event log and the production source of truth.
- GitHub Actions: wakes the Web service and requests one sync every day at
  07:00 China time.

The free Render filesystem is ephemeral. It is used only to materialize
credential JSON from environment variables for the current process.

## 1. Neon database

Create a Neon Postgres database and set Render Web `DATABASE_URL` to an async
SQLAlchemy URL:

```text
postgresql+asyncpg://USER:PASSWORD@HOST/DATABASE?ssl=require
```

Do not place the URL in Git. The application creates the `event_log` and
snapshot tables automatically.

### Migrate the existing local event log

Set the Neon connection string only in the current terminal:

```powershell
$env:NEON_DATABASE_URL="postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require"
python scripts/migrate_events_to_postgres.py
python scripts/migrate_events_to_postgres.py --apply
```

The first command is dry-run. The apply command:

- creates a consistent SQLite backup under
  `D:\CognitiveOSRuntime\New project 8\migration-backups`;
- preserves event IDs, original sequence numbers, timestamps, payloads, and
  metadata;
- skips event IDs already present;
- stops on sequence conflicts;
- prints counts only, never event or credential contents.

Keep the local database and backup for at least seven days.

## 2. Render Web environment

Configure these secrets on the `cognitive-os` Web service:

```text
DATABASE_URL
WEB_UI_PIN
CLOUD_SYNC_TOKEN
JWXT_COOKIES_JSON
CHAOXING_STATE_JSON
GOOGLE_CALENDAR_CREDENTIALS_JSON
GOOGLE_CALENDAR_TOKEN_JSON
```

Use a long random value for `CLOUD_SYNC_TOKEN`. Configure the same value on
the Cron service. Keep these non-secret settings:

```text
DATA_DIR=/tmp/cognitive-os
JWXT_MOCK=false
CHAOXING_MOCK=false
GOOGLE_CALENDAR_MOCK=false
GOOGLE_CALENDAR_WRITE_REQUIRES_ACCEPTANCE=true
GOOGLE_CALENDAR_SCHEDULE_WRITE_ENABLED=false
RENDER_ADMIN_IMPORT_ENABLED=false
CLOUD_SYNC_SOURCE_TIMEOUT_SECONDS=180
```

Google Calendar writes still require an accepted proposal. The cloud sync
only performs connector reads.

## 3. Daily GitHub Actions sync

`.github/workflows/cloud-sync.yml` defines:

```text
schedule: 0 23 * * *
```

GitHub Actions schedules use UTC, so 23:00 UTC is 07:00 the next day in China.
Set the repository Actions secret `CLOUD_SYNC_TOKEN` to the same value used by
the Render Web service.

The Cron command calls:

```text
POST /api/internal/cloud-sync
X-Cloud-Sync-Token: <secret>
```

The endpoint runs JWXT, Chaoxing, and Google Calendar sequentially through
the normal EventBus/Pipeline. A partial failure does not discard the other
successful sources, but the workflow exits non-zero so GitHub marks the run as
failed.

## 4. Refresh expired authentication

Authentication refresh remains local:

```powershell
python scripts/refresh_jwxt_state.py
python scripts/refresh_chaoxing_state.py
python scripts/google_calendar_login.py
```

After refresh, update the corresponding JSON environment variable in Render
and redeploy. Never commit or paste these values into logs or issues.

## 5. Phone and desktop access

Use the same URL everywhere:

```text
https://cognitive-os.onrender.com/app/
```

The Windows launcher opens this cloud URL by default. To run the local
development stack, set this in local `.env`:

```text
COGNITIVE_OS_LAUNCH_MODE=local
```

The free Web service may need roughly one minute to wake after inactivity.
The daily Actions workflow still wakes it independently at 07:00.

## 6. Acceptance checks

1. Open the Web URL over phone cellular data and log in.
2. On System, confirm database type is `postgresql` and cloud sync is
   configured.
3. Click “全部刷新”; verify explicit status for课表、作业、日历.
4. Confirm Tasks and Time update without opening the local computer.
5. In GitHub Actions, confirm one daily cloud-sync run at 23:00 UTC.
6. Confirm Google Calendar external writes still require Accept.
