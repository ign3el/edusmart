# PROJECT.md — EduSmart

## Purpose
AI-powered educational storybook platform. A user (teacher/parent/student) uploads a lesson document (PDF/DOCX), picks a grade level, and the backend turns it into an illustrated, narrated interactive story with a 10+ question quiz. Stories can be played immediately, saved to an account, downloaded as an offline ZIP, or synced for offline playback via PWA.

## Stack
| Layer | Tech |
|---|---|
| Frontend | React 18 + Vite, react-router-dom v7, framer-motion, react-three-fiber/drei (background only), axios |
| Backend | Python FastAPI (single `uvicorn main:app`, no multi-worker). Story generation runs through a durable SQLite-backed queue with a fixed worker pool - see Concurrency & Scaling |
| Story generation | Groq (`llama-3.3-70b-versatile`) — **not** Gemini, despite `google-genai` being installed and referenced in some dead code paths |
| Images | RunPod ComfyUI (FLUX.1-dev), with a monthly AED spend cap |
| TTS (English) | Self-hosted Kokoro-82M (`kokoro-tts:8880` container), reached via two parallel client wrappers |
| TTS (Arabic) | A hosted Piper-compatible endpoint (`TTS_API_URL`) — **only reachable from the admin TTS-test tool and the voice-preview endpoint, not from the actual story-generation pipeline** (see Known Issues) |
| Main DB | MySQL — users, saved stories |
| Job-tracking DB | SQLite (`db_data/job_state.db`, Docker named volume) — in-progress/generated story state |
| Mobile | PWA (service worker, install prompt, offline story storage via IndexedDB/localStorage) |

## Deploy
Docker Compose (`docker-compose.yml` at repo root, one file for both `backend` and `frontend` services),
aaPanel nginx reverse proxy in front. Deploys go through `./deploy.sh` (tagged images + rollback) —
see Quick Commands. Rollback verified end-to-end 2026-07-25: `/api/health`'s `version` field flipped
to the older build and back, healthy both ways.

## Domain & Port
- Domain: edusmart.ign3el.com (Cloudflare proxy)
- Containers: `edusmart-backend` (port 8000), `edusmart-frontend` (port 3004→80)
- Backend runs as **non-root** (`user: "1001:1001"`, matching the host `ubuntu` UID) as of 2026-07-19

## Quick Commands
> **Deploy with `./deploy.sh`, NOT bare `docker compose up -d --build`.** As of 2026-07-25 the
> backend no longer bind-mounts its source, so the running container *is* the built image.
> `deploy.sh` tags every build with a timestamp, keeps the last 5, waits for the healthcheck,
> and gives you a real rollback. A bare `docker compose up -d --build` still works but
> overwrites `:latest` with no tagged restore point — you lose the ability to go back.

| Action | Command |
|---|---|
| Deploy both services | `./deploy.sh` |
| Deploy one service | `./deploy.sh backend` / `./deploy.sh frontend` |
| List rollback points | `./deploy.sh --list` |
| Roll back | `./deploy.sh --rollback backend 20260725_201915` |
| Which build is live? | `curl -s https://edusmart.ign3el.com/api/health` → `"version"` field |
| Logs | `docker logs edusmart-backend --tail 50` |
| Job-state DB (live) | `docker exec edusmart-backend sqlite3 /app/db_data/job_state.db` |

**Backend code changes now REQUIRE a rebuild.** There is no `./backend:/app` mount any more —
editing a `.py` file on the host does nothing until you `./deploy.sh backend`. Data directories
(`outputs`, `uploads`, `saved_stories`, `generated_stories`) and the `backend_db` volume are
still mounted, so nothing persistent lives in the image.

**Anything the backend writes must go to a mounted path.** `db_data/` (named volume) is the
home for state files: `job_state.db`, `runpod_usage.json`, `hash_cache.json`. Writing anywhere
else inside `/app` now lands in the container's ephemeral layer and is destroyed on the next
deploy — silently. `hash_service.py` was doing exactly that (`backend/hash_cache.json`, which
only survived via the old mount, hence the odd `backend/backend/` directory); moved 2026-07-25.

**Always use `--no-deps` when deploying just one service.** `frontend` has `depends_on: backend` in `docker-compose.yml`, so a plain `docker compose up -d --build frontend` silently recreates `backend` too (config-hash check on the dependency) even though nothing backend-related changed - wiping its container logs in the process (confirmed 2026-07-20, cost us two rounds of backend-timing diagnosis on a live perf complaint). `--no-deps` restricts the up to only the named service.

---

## App Flow (frontend state machine)

`frontend/src/App.jsx`'s `MainApp` component is a single-page step machine (`step` state, no router for the main flow — only `/verify-email`, `/forgot-password`, `/reset-password` are real routes). Browser back button is wired to `step` via `history.pushState`.

```
home ─▶ upload ─▶ confirm ─▶ generating ─▶ playing
  │        │                                  │
  ├─▶ load (LoadStory: saved stories list)     ├─▶ Quiz (modal)
  ├─▶ offline (OfflineManager: local stories)  ├─▶ SaveStoryModal
  ├─▶ profile (UserProfile)                    └─▶ download ZIP
  └─▶ admin (AdminPanel, is_admin only)
```

1. **home** — three entry points: Create New Story / Load Online Story / Offline Manager.
2. **upload** (`FileUpload.jsx`) — drag-drop PDF/DOCX/DOC + grade level (1-7). On file select, `App.jsx`'s `handleFileUpload`:
   - POSTs the file to `/api/check-duplicate` (SHA-256 hash-based dedup via `hash_service.py`) — if a duplicate is found, shows `DuplicateStoryModal` (load existing vs. force new).
   - Otherwise uploads via raw `XMLHttpRequest` (for progress events) to `/api/upload`, gets back a `job_id`, and starts polling `/api/status/{job_id}` every 2s.
3. **confirm** (`FileConfirmation.jsx`) — calls `/api/upload/extract-text` to detect document language, auto-picks a voice (`ar_teacher` for Arabic, `af_sarah` otherwise) via `TeacherCard.jsx`, lets the user set narration speed. Confirming calls `generateStory()`, which re-POSTs to `/api/upload` with `voice`/`speed`/`grade_level` and starts the same polling loop.
4. **generating** — progress bar driven by the poll loop; as soon as the first scene is ready the app jumps straight to **playing** and keeps polling/streaming in remaining scenes (progressive reveal, not wait-for-all).
5. **playing** (`StoryPlayer.jsx`) — scene image + narration audio + text, auto-advances on audio `ended`, scene dots, quiz button, save/download actions. Media URLs come from `buildFullUrl()` (`utils/urlHelpers.js`), which appends the JWT as a `?token=` query param for `/api/saved-stories/` and `/api/generated-stories/` paths (browsers can't attach `Authorization` headers to `<img>`/`<audio>` tags — see Security section).
6. **Quiz** (`Quiz.jsx`) — 10+ MCQs, scoring, retry, marks completion via `/api/story/{id}/complete-quiz`.
7. Save → `SaveStoryModal.jsx` → `POST /api/save-story/{job_id}` → persists into MySQL `user_stories`. Download → ZIP export (`/api/export-story/{id}` or `/api/export-job/{id}`). Offline → local IndexedDB/localStorage via `utils/storyStorage.js`.

Auth screens (`Login`/`Signup`/`ForgotPassword`/`ResetPassword`/`VerifyEmail`) gate everything — `AuthContext.jsx` holds the JWT in `localStorage['auth_token']` and auto-attaches it via an axios interceptor for anything going through `apiClient`.

---

## Backend Module Map

**Entry point**: `backend/main.py` (1880 lines) — FastAPI app, most routes live here directly (story upload/status/load/save/delete/export, media serving, quiz completion). Includes three routers:

| Router | Prefix | Responsibility |
|---|---|---|
| `routers/auth.py` | `/api/auth` | signup, login (JWT), email verify, password reset/change, rate-limited login |
| `routers/admin.py` | `/api/admin` | user management, story management (all users), job_state.db viewer, admin TTS test — every route gated by `is_admin` |
| `routers/upload.py` | `/api/upload` | text extraction (PDF/DOCX/TXT + language detection), TTS preview proxy |

**Story generation pipeline** (`services/story_service.py`, `StoryService` class, singleton `gemini` in main.py despite the name):
- `process_file_to_story()` — the real generation call. Extracts text from the uploaded file, sends a structured prompt to **Groq** (`response_format=json_object`), validates the JSON (`_validate_story_json`), tops up to ≥10 quiz questions if needed. Prompt is capped to 5-10 scenes, document text truncated to 15k chars, includes an injection-defense clause and a content-safety refusal path (`{"error": "content_unsuitable"}`) — added 2026-07-19.
- `generate_image()` / `generate_images_parallel()` — RunPod ComfyUI FLUX calls, up to 4 concurrent, with a monthly AED spend cap enforced via an `asyncio.Lock`-protected reserve-before-spend check against `services/runpod_usage.json`.
- `generate_progressive_tts()` / `_generate_and_cache_tts()` — batched TTS generation (batch_size=2) for scenes 1-N, caches audio to `outputs/audio_cache/`, writes progress to `outputs/status/{story_id}.json`.
- **Dead code inside this class** (harmless but never executes): `_ensure_minimum_questions()` and `generate_scene_priority()` both call `self.client.models.generate_content(...)` — `self.client`/`self.text_model` are never initialized anywhere (leftover from a pre-Groq Gemini implementation). `_ensure_minimum_questions` is unreachable in practice because it's only called after validation already guarantees ≥10 questions; `generate_scene_priority` is never called at all.

**TTS clients** (naming is legacy and confusing — all of these ultimately talk to the same self-hosted Kokoro container except Piper):
- `services/tts_service.py` (`kokoro_tts`, async class) — used for desktop narration.
- `services/chatterbox_client.py` (`chatterbox`) — despite the name, calls Kokoro's `/v1/audio/speech`; used for mobile-optimized narration. Docstring: "Self-hosted TTS service replacing Edge TTS" — a naming relic from an earlier TTS provider.
- `services/kokoro_client.py` — plain function used by admin TTS test, upload preview, and the progressive-TTS cache path.
- `routers/kokoro_client.py` — **dead duplicate** of the above, never imported.
- `services/piper_client.py` (`PiperClient`/`piper_tts`) — **fully dead**, never imported. Arabic TTS in practice goes through raw `requests` calls in `admin.py`/`upload.py` hitting `Config.TTS_API_URL` directly, and is not wired into the actual story-generation flow at all.

**Storage & job tracking**:
- `story_storage.py` (`storage_manager`) — manages `saved_stories/` and `generated_stories/` folder trees, dedup hashing, and two background schedulers: hourly TTL cleanup of expired `generated_stories/` folders, and a 48-hour MySQL+SQLite orphan-record cleanup. Both now run once ~1-2 min after startup and then on their normal interval (fixed 2026-07-19 — previously slept for the full interval *before* their first run, so frequent redeploys meant they could go their entire life without ever firing).
- `job_state.py` (`job_manager`, singleton) — SQLite wrapper for `db_data/job_state.db` (`stories`, `scenes`, `generation_queue` tables). This is the single live copy; **do not** create another one at a different path (see Known Issues history below). Runs in WAL mode; the queue methods at the bottom of the class are the only place `generation_queue` is touched.
- `services/job_queue.py` (`generation_queue`, `admit_generation`) — the fixed worker pool that drains `generation_queue`, plus admission control. Replaced FastAPI `BackgroundTasks` on 2026-07-26. The workflow is injected via `set_handler()` from `main.py` to avoid a circular import.
- `services/concurrency.py` (`image_governor`, `tts_governor`, `llm_governor`) — process-wide semaphores around every expensive external call, with utilisation counters exposed on `/api/health`.
- `hash_service.py` (`hash_service`) — SHA-256 dedup across `saved_stories/`+`generated_stories/`, cache file at `backend/hash_cache.json` (resolves to `/app/backend/hash_cache.json` in-container, hence the nested `backend/backend/` folder on host).
- `database.py` / `database_models.py` — MySQL connection pool + schema (`users`, `email_verifications`, `password_reset_tokens`, `user_stories`) + `UserOperations`/`StoryOperations`.

**Auth**: `auth.py` (top-level) — bcrypt hashing, JWT (`python-jose`, HS256, 7-day expiry). `JWT_SECRET` now fails loudly on startup if unset (no more silent fallback to a hardcoded key). The old `DEV_BYPASS_SECRET` full-auth-bypass mechanism was removed entirely (2026-07-19).

**Maintenance scripts** (top-level `.py` files, run manually via `docker exec`, not part of the live app): `create_admin.py`, `reset_admin_password.py`, `repair_story.py`, `diagnose_story.py`, `check_db_stories.py`, `check_models.py`, `probe_users.py`, `test_versioning.py`.

---

## Frontend Module Map

- **`App.jsx`** — the step machine described above; owns almost all top-level state (upload progress, poll timers, story data).
- **`context/AuthContext.jsx`** — JWT storage/refresh, login/signup/logout.
- **`services/api.js`** — shared axios instance with auth interceptor; also `services/updateService.js` (polls `/version.json` hourly, prompts in-app update) and `services/kokoro_client` equivalents aren't here (backend-only).
- **`utils/urlHelpers.js`** — `buildFullUrl()`, the single choke point that turns backend-relative media paths into full URLs and attaches the auth token query param.
- **`utils/storyStorage.js`** — IndexedDB (large stories) / localStorage (small) hybrid for offline story persistence.
- **Admin suite** (`is_admin` gated, both client-side in `App.jsx` and server-side on every `/api/admin/*` route): `AdminPanel.jsx` → tabs for `StoryManagement.jsx` (all stories, saved+generated), `UserManagement.jsx` (promote/demote/verify/delete users), `JobStatusViewer.jsx` (live `job_state.db` viewer, reads via `/api/admin/db/job_state/*`).

### Dead / unused frontend code (confirmed via import-graph check, harmless but worth knowing before the UI overhaul)
- `components/Auth.jsx` — unused wrapper; `App.jsx` renders `Login`/`Signup` directly instead.
- `components/AvatarSelector.jsx` + backend `/api/avatars` — the avatar-selection step was removed from the flow; `FileConfirmation` now goes straight to generation with a hardcoded default avatar.
- `components/AdminDashboard.jsx`, `components/AdminDbViewer.jsx`, `components/StoryList.jsx` — superseded by `StoryManagement`/`JobStatusViewer`; `StoryList` is still imported by `AdminPanel.jsx` but never rendered.
- `components/TtsLab.jsx` + `components/VoiceSettings.jsx` — standalone TTS testing UI, not linked from anywhere.
- `components/FloatingMenu.jsx`, `components/HomeCard.jsx`, `components/StoryActionsBar.jsx` (imported in `App.jsx` but never rendered), `components/AuroraBackground.jsx`, `components/FloatingBooks.jsx`.
- **A fully-built, entirely unused 3D scene subsystem**: `components/Scene3D.jsx` → `components/3d/StoryDepthScene.jsx` → `AuroraLayer.jsx`, `DiamondLogo.jsx`, `ParticlesLayer.jsx`, `PerlinFluid.jsx`, `SceneParticles.jsx`, `ShapesLayer.jsx` (plus fully orphaned `R3FProvider.jsx`, `SceneContent.jsx` with zero importers). None of this is wired into the live app — only the simpler, self-contained `components/3d/Scene3DBackground.jsx` (used as the lazy-loaded global background) is live. **This may be relevant scaffolding for the planned 3D/animation UI overhaul** rather than something to delete.
- `services/api.js`'s `getSceneAudioUrl()` — dead export; its own comment claims the axios interceptor adds auth to it, which isn't true for a plain URL string used in `<audio src>` (the interceptor only applies to axios-issued requests). Backend counterpart `GET /api/story/{id}/scene/{n}/audio` also appears unused by the frontend.

---

## Data Model

**MySQL** (`users`, `email_verifications`, `password_reset_tokens`, `user_stories`) — `user_stories.story_data` is a JSON blob of the full story (scenes, quiz, image/audio URLs). Ownership enforced via `user_id` FK; admins bypass ownership checks throughout.

**SQLite `job_state.db`** (`db_data/job_state.db`, Docker named volume `backend_db`) — `stories` (story_id, status, title, grade_level, total_scenes, completed_scenes, user_id, username, created_at) and `scenes` (per-scene image/audio status). Tracks in-progress and recently-generated stories that haven't necessarily been saved to MySQL yet.

---

## Background Schedulers
| Task | Interval | What it does |
|---|---|---|
| `cleanup_scheduler_task` | hourly | Deletes `generated_stories/{id}` folders older than 24h, **and** their `job_state.db` rows (fixed 2026-07-19 — previously only deleted the folder, leaving permanent orphaned DB rows) |
| `database_cleanup_scheduler_task` | every 48h | Purges orphaned MySQL `user_stories` rows (no matching folder), old email-verification/password-reset tokens, and old completed/failed `job_state.db` rows |
| `cleanup_outputs_cache` (inside the hourly task) | hourly | Deletes `outputs/audio_cache/*` and `outputs/status/*` untouched for 24h. Added 2026-07-25 — nothing had *ever* cleaned this folder; first run removed 112 files / 71.4MB, some dating to February. Also logs disk usage each pass and escalates to ERROR above 90%. |

Both now fire once shortly after startup rather than only after a full interval of continuous uptime.

---

## Security posture (as of 2026-07-19)
A full hostile-QA pass was done and fixes applied/verified live. Summary — see git history / `.bak` files for the pre-fix state if needed:
- **Fixed**: unauthenticated path traversal + full `.env`/source disclosure via `/api/saved-stories/*` and `/api/generated-stories/*` (critical, was live in production).
- **Fixed**: all story media (`saved-stories`, `generated-stories`, `outputs` audio-cache/status) now requires auth + per-story ownership (or admin), via a shared `_verify_story_access()` check in `main.py`. Media URLs authenticate via `?token=` query param since `<img>`/`<audio>` tags can't send headers.
- **Fixed**: `/api/uploads` (dead) and a shadow `StaticFiles` mount on `/api/generated-stories` (which had been silently bypassing the custom auth'd route) both removed.
- **Fixed**: admin `password_hash` leak via `SELECT *`, unauthenticated cost-abuse endpoints (`extract-text`, `tts-preview` — now require auth + size/length caps), RunPod spend-cap race condition, dev auth-bypass removed, `JWT_SECRET` fail-loud, last-admin delete/demote protection, container no longer runs as root.
- **Superseded**: the old note "no payment/subscription/entitlement layer exists" is **out of date**. A full Stripe layer now exists — `routers/billing.py` (checkout / webhook / portal / balance / plans / redeem-promo) plus `credits_balance`, `credit_transactions`, `subscription_plans`, `promo_codes`, `promo_redemptions`, `webhook_events`. Story generation is credit-gated by `check_and_reserve_credit()`, called before any job state is created.
- **Still open**: `/gitignore` line 71 (`admin_token.txt`) has a pre-existing typo (spaces between every character) so it isn't actually ignored.

## Admin: failed-job recovery (added 2026-07-25)
`JobStatusViewer` previously showed failed jobs with **no actions at all** (Cancel was gated on
`status === 'processing'`). Failed cards now have **Retry** and **Delete**.

`POST /api/admin/stories/{story_id}/retry` is a *repair*, not a re-run. It regenerates only the
scenes whose image or audio is missing, reusing what SQLite already stores: `scenes.text` for TTS
and `scenes.character_prompt` for the image prompt. It never regenerates story text — `uploads/`
is emptied after processing, so the source document is gone and a true re-run is impossible.
No credit is charged; the user's credit was already refunded when the job failed.
Not stored, and therefore defaulted on retry: `story_seed` and `voice`, so a repaired scene can
differ slightly in style from its neighbours. The Retry button is hidden when a job has no
recoverable scenes (died before any scene rows were written) — those can only be deleted.

---

## Security posture (2026-07-25 production-readiness pass)
Second hostile-QA pass. All fixes deployed and verified live (25/25 assertion smoke suite in `scratchpad/smoke.sh`).
- **Fixed (P0, was live)**: the host aaPanel nginx vhost had a `location /api/outputs/` block aliasing `backend/outputs/` straight off disk with `Access-Control-Allow-Origin *`. nginx matched it before the proxy, so FastAPI's route and `_verify_story_access()` never ran — any story's narration audio and status JSON was readable cross-origin with no credentials, given only a story UUID. Proven exploitable (942KB of another user's audio fetched anonymously) before removal. **Root cause**: `frontend/src/utils/urlHelpers.js` only appended `?token=` for `/api/(saved|generated)-stories/`, never `/api/outputs/` — the nginx alias was a workaround for that gap. The regex now covers `outputs` too; both changes must stay together.
- **Fixed (P0, was live)**: the login rate limiter was a **single global bucket**. `frontend/nginx.conf`'s `/api` block forwarded no client-IP headers, so `request.client.host` was the frontend container's IP for every visitor — five failed logins from anyone locked out the entire site for 60s, renewable indefinitely. Now: Cloudflare real-IP restoration in the host vhost (`set_real_ip_from` × 22 ranges + `real_ip_header CF-Connecting-IP`), client-IP headers forwarded by the frontend, and `client_ip()` in `routers/auth.py` keying on `X-Real-IP` (host-set from `$remote_addr`, so not client-spoofable). X-Forwarded-For is deliberately **not** trusted.
- **Fixed (P0)**: promo-code double-redeem race — `_validate_promo` was check-then-act across four separate transactions with no lock. Redemption now runs in one transaction holding `SELECT … FOR UPDATE` on the `promo_codes` row. Verified: a second concurrent transaction blocks 1.6s and then reads the committed counter.
- **Fixed**: `get_db_cursor` now rolls back on **any** exception, not just `mysql.connector.Error` — an HTTPException raised inside the `with` body used to skip both commit and rollback, returning a connection with an open transaction (and its locks) to the pool.
- **Fixed**: Stripe webhook idempotency was check-then-act across two transactions; now an atomic `INSERT IGNORE` claim on the `webhook_events` PK, released if the handler throws so retries still work. Added `invoice.payment_failed` → `past_due` (spending suspended via `SUSPENDED_STATUSES`) and `customer.subscription.updated` status sync; `invoice.paid` clears dunning. `metadata['user_id']` KeyError (→ 500 → 3 days of Stripe retries) and the `max_redemptions_per_user IS NULL` TypeError both fixed.
- **Fixed**: no CSP at either nginx layer — **and** `location /` defining its own `add_header` was silently discarding the server-level `X-Frame-Options`/`X-Content-Type-Options`/`Referrer-Policy` for the HTML document. All five headers are now set inside `location /` and verified present on the live response.
- **Fixed**: no healthcheck on either container and no health endpoint. `/api/health` probes MySQL + `job_state.db` + free disk (503 if degraded); both compose services now have `healthcheck:` blocks. `restart: unless-stopped` alone could not detect a hung-but-alive uvicorn.
- **Fixed**: no `.dockerignore` for either service — 25 `.bak` files were shipping inside the backend image, including pre-security-fix `main.py`/`auth.py`. Now 0 in the image (1.26GB → 1.14GB).
- **Fixed**: `client_max_body_size 50M` vs the app's 20MB cap (a 21–50MB upload was fully transferred, then rejected); duplicate `aiofiles` in requirements; unpinned `stripe>=10.0.0` pinned to `==15.3.1` (v11 removed `stripe.error.*`; `billing.py` now binds the exception classes defensively); `FRONTEND_URL` never passed to the container (worked only because the hardcoded default matched — it now falls back to `APP_URL`).
- **Resolved 2026-07-25** (this bullet used to say the opposite — kept for history): the backend no longer bind-mounts `./backend:/app`. The running container *is* the built image, `.dockerignore` is therefore effective, and `deploy.sh` provides tagged rollback points. Data directories and the `backend_db` volume are still mounted. See Quick Commands.

---

## Outbound mail abuse controls (2026-07-26)

**How this was found:** three "EduSmart - Verify Your Email Address" messages to
unfamiliar recipients showed up in the account holder's Gmail. They were the
**Sent** folder, not an inbox - Gmail files anything relayed through
`smtp.gmail.com` into Sent - and the recipients (`_saveguard`, `_queueprobe`)
were throwaway accounts created by test scripts that called the **live**
`/api/auth/signup`. No attacker, no loop. But it exposed a real hole.

**The hole:** `/api/auth/signup`, `/api/auth/forgot-password` and
`/api/auth/resend-verification` each make the server's own SMTP account send a
message to an address **the caller supplies**, with no credentials required.
Only `/token` and `/social/{provider}` were rate limited. A loop against any of
the three would exhaust Gmail's ~500/day send cap (killing signup for real
users), deliver unsolicited mail to strangers under the account holder's name,
and get `sahtesham@gmail.com` - a personal account, not an app account -
suspended. `/resend-verification`'s 3-minute cooldown is keyed on **user id**,
so cycling through addresses defeated it completely: one send each, unlimited.

**Fixed** (`routers/auth.py`, `services/email_service.py`):
- Per-IP, per-endpoint budgets via the existing `RateLimiter`, bucketed
  `mail:{signup|reset|resend}:{ip}` so a burst on one cannot lock out another
  for everyone sharing that IP. Guards run **before** any DB work, so a refused
  attempt creates nothing.
- Signup gets the looser budget deliberately: a school or household is one
  public IP, and several genuine signups in an hour is normal there.
- Blocked-domain rejections still consume budget - junk addresses buy no free
  attempts.
- `_reject_undeliverable()` refuses RFC 2606/6761 reserved domains at signup.
  `example.com` publishes a **null MX**, so every send to it bounced back into
  the sending account's own inbox. Also keeps junk rows out of `users`.
- `MAIL_SUPPRESS_DOMAINS` in `email_service.send_email()` drops recipients on a
  test domain before any backend is chosen and logs them instead. Test scripts
  target `@probe.edusmart.internal` and can no longer emit real mail.
  `EMAIL_BACKEND=console` cannot do this job - it is all-or-nothing and would
  silence real users too.

**The knobs** (all in `docker-compose.yml`, none tuned to this box):

| Env var | Default | Meaning |
|---|---|---|
| `SIGNUP_RATE_MAX_ATTEMPTS` | 10 | signups per IP per window |
| `MAIL_RATE_MAX_ATTEMPTS` | 5 | forgot-password / resend per IP per window |
| `MAIL_RATE_WINDOW_SECONDS` | 3600 | the window for both |
| `BLOCKED_EMAIL_DOMAINS` | example.*, test.com, localhost | refused at signup (plus suffixes `.test .invalid .example .localhost .local`) |
| `MAIL_SUPPRESS_DOMAINS` | probe.edusmart.internal | logged, never sent |

**Verified** (`scratchpad/probe_mail_abuse.py`, 9/9 PASS, deploy
`20260726_150648`): blocked domain -> 400 with no user row; probe domain -> 201
with `Suppressed (test domain), not sent` in the log and **zero** `Email sent`
lines for the whole run; forgot-password and resend each `[200 x5, 429]`; signup
`10 x allowed, 11th 429`. Public path confirmed:
`POST https://edusmart.ign3el.com/api/auth/signup` with an `@example.com`
address returns 400.

**Two things to know before changing this:**
1. `RateLimiter` is **in-process**. Under `uvicorn --workers N` (Tier 3) each
   worker keeps its own counters and the effective limit becomes N x the env
   value. Divide the env values by the worker count, or move the counters to
   Redis, when that lands.
2. `components/Signup.jsx` substring-matches the 400 `detail` for the words
   `email` and `username` and rewrites either into "already exists". Any new
   signup error string must avoid both words or it will display the wrong
   message. (The matcher itself is fragile and worth replacing with an error
   code, but that is a separate change.)

**Longer term:** move app mail off a personal Gmail to a dedicated sender
(Resend / SES on `ign3el.com`). Reputation damage then hits a domain that can be
replaced, not the account holder's own inbox.

---

## Story sharing & duplicate attribution (2026-07-26)

**The bug this fixes.** `/api/check-duplicate` and the duplicate branch of
`/api/upload` both hardcoded `"created_by": current_user["username"]` on every
return path. A duplicate was therefore always reported as having been made by
whoever was looking at it - which is why a story uploaded by one account showed
up as "created by" the next person who uploaded the same PDF. Worse, when the
owner-scoped DB lookup found nothing (because the story belonged to somebody
else), `check_duplicate` fell through to `saved_matches[0]` and returned a
**stranger's `story_id` and title**. Clicking "Load Existing Story" then 404'd,
because the read endpoints are owner-scoped - the leak was in the description,
not the data, but the id was real.

**Root cause.** The hash service matches bytes on disk. It has no idea who owns
a story, and the callers never asked MySQL. Attribution was invented at the
response layer.

### The model

`user_stories.is_public` (TINYINT, `NOT NULL DEFAULT 0`, indexed). Consent is
per story and opt-in: the migration defaults every pre-existing story to
private, because consent is something a user gives, not something a migration
assumes.

Resolution order when a hash matches (`StoryOperations.resolve_visible_duplicate`,
one SQL statement with a real `JOIN users`):

1. the viewer's own copy, if they have one;
2. otherwise the most recently saved copy whose owner ticked "share";
3. otherwise **no duplicate at all** - not even the title. The existence of a
   match reveals who else uploaded that document, so a private story owned by
   someone else is reported as no match rather than as an inaccessible one.

In-progress (`generated_stories`) matches are shown to their own owner only.
Consent is given at save time, so an unfinished story cannot have been shared.

### Read vs control

`_verify_story_access(story_id, user, allow_public=False)` and
`StoryOperations.get_story(story_id, user, allow_public=False)` both default to
owner-only. `allow_public=True` is passed **only** from read paths:

| Passed | Not passed |
|---|---|
| `/api/saved-stories/{id}/{file}` (media) | `/api/generated-stories/...` (in progress, never public) |
| `/api/outputs/...` (audio cache, status) | `/api/save-story/{job_id}` |
| `/api/story/{id}/status`, `/api/status/{id}` | `/api/export-job/{id}` (offline download stays owner-only) |
| `/api/story/{id}/scene/{n}` and `/scene/{n}/audio` | `delete_story`, `set_visibility` |
| `/api/story/{id}/tts-status`, `/api/load-story/{id}` | |

Sharing grants reading. It never grants editing, deleting or exporting.

### API

- `POST /api/save-story/{job_id}` gained `make_public: bool = Form(False)`.
- `PATCH /api/stories/{story_id}/visibility` with body `{"is_public": true|false}`
  - owner or admin only, 404 otherwise.
- `/api/list-stories` now returns `is_public` per row.

Unsharing is **not retroactive**: it removes the story from future duplicate
checks and closes the read endpoints, but it cannot reach into a session that
already loaded it. The UI says so rather than implying a recall it cannot do.

### Things to know before changing this

1. `set_visibility` checks ownership with a `SELECT` before the `UPDATE`. It
   must not use `cursor.rowcount`: MySQL reports *changed* rows, so re-sending
   the value a story already has affects 0 rows and would read as "not yours".
   The same trap was live in `verify_email_with_token` and is fixed in the same
   pass: opening a verification link twice (or a mail client prefetching it)
   updated 0 rows, so the user was told verification failed and the token was
   left undeleted, making every retry fail identically.
2. `database_models.py` is double-spaced - a blank line between every source
   line. Patch anchors there must be small and exact; several lines also carry
   trailing whitespace.
3. `SaveStoryModal.css` styles `.modal-content input` full-width with heavy
   padding. Any new checkbox needs the explicit reset that `.share-consent
   input[type="checkbox"]` carries, or it renders as a stretched bar.
4. The hash cache (`db_data/hash_cache.json`) stores match *lists* only, never
   ownership - ownership is resolved against MySQL on every call. Do not cache
   the resolved response, or a visibility change would take up to 24h to apply.

**Verified** (`scratchpad/probe_visibility.py`, 22/22 PASS, backend
`20260726_152141`, frontend `20260726_152437`): new saves default to private; a
private story is invisible to another user via both the resolver and
`/api/load-story` (404); after sharing, the other user gets 200 and the story is
attributed to `_visprobe_a`, not to the viewer; the non-owner cannot PATCH
visibility (404) or delete it; re-sharing an already-shared story still reports
success (the rowcount trap); after unsharing, the non-owner is locked out again
and the story disappears from their duplicate check. Shipped bundle greps clean
for `make_public`, `share-consent` and the new copy.

---

## Backlog pass (2026-07-26, backend `20260726_154126` / frontend `20260726_154207`)

Four loose ends closed after the sharing work. All verified by
`scratchpad/probe_backlog.py` (12/12 PASS).

**1. The visibility toggle had no UI.** `is_public` and
`PATCH /api/stories/{id}/visibility` shipped earlier the same day, but the only
way to set consent was the checkbox in the save dialog - a one-shot decision with
no way back. `LoadStory.jsx` now renders a Private/Shared pill on every saved
story card. Turning sharing **off** goes through a confirm that states plainly
that unsharing is not retroactive; turning it on does not. Local state is updated
only after the PATCH returns, so a failed call cannot leave a card claiming a
story is shared when it is not.

**2. `get_all_stories()` never selected `is_public`.** That is the admin branch
of `/api/list-stories`, so the new toggle would have rendered `undefined` -
permanently "Private" - for admins, on other people's stories. Column added; both
branches of the endpoint now return the same shape. **If you add a field to
`get_user_stories`, add it here too**; these two have drifted before.

**3. Sub-44px tap targets in `Auth.css`.** `.link-button` measured 115x22 and
`.auth-back-button` 109x23 - both `padding: 0`. Both now carry
`display: inline-flex` + `min-height: 44px` (inline-flex is what makes a
min-height apply to a `<button>` sitting inline inside a `<p>`). The surrounding
margins - `.auth-forgot-password` and `.auth-back-button`'s own `margin-bottom` -
were pulled in by the same ~22px so the forms do not visually shift.

**4. `Signup.jsx` branched on words inside the error message.** It ran
`detail.includes('email')` before checking the status, which meant the 409 branch
underneath was unreachable, "this email **or** username already exists" was
reported as "Email already exists" even when the email was fine, and any
unrelated 400 containing the word 'email' became a false duplicate warning. It
now branches on `err.response?.status === 409` first. **This lifts a constraint
on the backend**: `_reject_undeliverable()` in `routers/auth.py` had been worded
to avoid the words 'email' and 'username' purely to dodge that matcher, and its
docstring has been updated to say so. Other error strings are free again.

---

## Audio format: WAV -> MP3 (2026-07-26, backend `20260726_162554`)

### What was actually wrong

This was filed as "add MP3 compression". It was not. The app **already wrote
files named `scene_N.mp3`** - but two of the three TTS clients asked Kokoro for
`response_format: "wav"`, so the bytes inside were RIFF. The extension lied.

| client | asked for | used by | state |
|---|---|---|---|
| `services/chatterbox_client.py` | `mp3` | scene 0 | was already correct |
| `services/kokoro_client.py` | `wav` | scenes 1..N | **the bug** |
| `services/tts_service.py` | `wav` | imported by `main.py`, otherwise unused | **the bug** |

On disk that produced a mixture: `scene_0.*` genuine MP3 (`ID3`), `scene_1..N.*`
WAV (`RIFF`) - and it went **both** directions, i.e. there are also `.wav` files
containing MP3 bytes. Any code deciding a media type from the filename was
wrong for a large fraction of the library.

### The fix

**No ffmpeg, no transcode step.** Kokoro's OpenAI-compatible endpoint encodes
MP3 itself. Measured on the running stack: **233,986 B wav -> 79,148 B mp3 for
identical input, 66% smaller.** Transcoding locally would have burned the exact
CPU that is this box's bottleneck, so asking Kokoro for the right format in the
first place is strictly better.

1. `config.py` - new `TTS_AUDIO_FORMAT` (default `mp3`), surfaced in
   `docker-compose.yml` as `TTS_AUDIO_FORMAT=${TTS_AUDIO_FORMAT:-mp3}`. An env
   knob, not a constant: a future GPU/RunPod TTS backend may want a different
   container, and nothing here should assume today's infra.
2. `kokoro_client.py` / `tts_service.py` - use the knob instead of `"wav"`.
3. **`_audio_media_type()` in `main.py`** - decides the media type from the
   file's *magic bytes* (`RIFF`/`WAVE`, `ID3` or an MPEG frame sync, `OggS`),
   returning `""` when unreadable so callers keep their old fallback. This is
   the backward-compatibility half: every pre-existing mislabeled file keeps
   playing, because the name is no longer trusted.
4. Audio lookup order is now `[".mp3", ".wav"]` - mp3 first, because that is
   what we write; `.wav` stays for stories generated before 2026-07-26.
5. The offline ZIP export derived its entry name from a hardcoded `.wav`. It
   now follows the real file, because the offline player picks its decoder from
   that name and would otherwise refuse its own bundle.

**No frontend change was needed** - `OfflineManager.jsx` already maps the
extension through a `mimeTypes` table covering both formats, and every audio URL
comes from the backend payload.

### The trap this hit (read before touching file serving)

The first pass fixed **1 of 11** serving branches and was verified as broken by a
live wire test before the second pass landed. `/api/saved-stories/...` and
`/api/generated-stories/...` are two **near-identical routes**, each with five
`FileResponse` fallbacks (exact name, uuid-prefixed, old-format, and two glob
patterns), plus `serve_output_file`. A fix anchored on any one comment lands in
exactly one of them.

This is the *same failure mode* as the `get_all_stories` / `get_user_stories`
drift found earlier the same day: **two copies of one endpoint, and a fix
applied to one copy.** If you change how media is served, change it in all
eleven branches, and prove it with `inspect.getsource()` per function - not by
testing one URL that happens to route through the branch you edited.

The media type is now decided **where the response is constructed**
(`media_type=_audio_media_type(path) or None`), never patched onto the headers
afterwards, so there is one mechanism to reason about. `or None` lets
`FileResponse` keep its own extension guess for images, which were never
affected.

### Verified

`scratchpad/probe_mp3.py` 11/11 PASS; a live wire test confirming a legacy
RIFF-in-`.mp3` file is served `audio/wav` and an MP3-in-`.wav` file is served
`audio/mpeg`; `5/5`, `5/5`, `1/1` FileResponse branches sniffing per route with
zero bare `FileResponse(` left; `probe_backlog.py` and `probe_visibility.py`
re-run green.

Existing files were **not** rewritten - there is no migration script. They are
served correctly by content sniffing and will age out naturally. Only newly
generated audio is MP3.

---

## TTS moved to RunPod GPU (2026-07-26, backend `20260726_211345`)

### What changed

Kokoro TTS generation now defaults to a RunPod Flash serverless GPU endpoint
instead of the shared CPU container. `TTS_BACKEND=runpod` in `.env`.

The separate `runpod-kokoro-test/` project (own `PROJECT.md` there) holds the
Flash worker source (`kokoro_worker.py`) and deploys via `flash deploy`.
Endpoint: `RUNPOD_ENDPOINT_ID_KOKORO=3dvjkask9mgklk`, reuses the existing
`RUNPOD_KEY` already used for FLUX image generation.

**Why one integration point, not three.** `services/kokoro_client.py`'s
`generate_tts(text, voice, speed)` has three independent call sites
(`routers/admin.py`, `services/story_service.py`, `routers/upload.py`). The
backend switch lives inside that one function - not in any caller - so all
three get RunPod (or CPU, or the fallback) with zero changes on their end.
This is deliberate: the file-serving bug earlier the same day (see the MP3
section above) came from exactly the opposite mistake - the same logic
duplicated across near-identical call sites, fixed in one and not the others.

### Pieces

1. **`services/runpod_kokoro_client.py`** (new) - `/run` then poll
   `/status/{id}`, same pattern as the existing RunPod FLUX image client in
   `story_service.py`. Sniffs the returned audio's magic bytes
   (`RIFF`->wav, `ID3`/frame-sync->mp3) and raises `RunpodTTSError` if the
   bytes match neither - it does **not** trust the worker's own `format`
   field, because that field lied during testing (claimed mp3, shipped wav)
   on 2026-07-26. Same lesson as the MP3 migration section above, one layer
   up the stack.
2. **`services/kokoro_client.py`** - `generate_tts()` now checks
   `Config.TTS_BACKEND` first. `"runpod"` tries the RunPod client; on any
   `RunpodTTSError` it falls back to the original CPU Kokoro HTTP call if
   `Config.TTS_RUNPOD_FALLBACK_TO_CPU` (default `true`), otherwise raises.
   `"cpu"` (default) is the original code path, untouched.
3. **`config.py`** - `TTS_BACKEND` (`cpu`|`runpod`, default `cpu`) and
   `TTS_RUNPOD_FALLBACK_TO_CPU` (default `true`).
4. **`.env` / `docker-compose.yml`** - `RUNPOD_ENDPOINT_ID_KOKORO`,
   `TTS_BACKEND`, `TTS_RUNPOD_FALLBACK_TO_CPU`, `RUNPOD_TTS_TIMEOUT_S`
   (default 180s - cold starts on the 24GB tier run 20-40s typically, up to
   ~250s was observed on the now-abandoned 16GB Low-Supply tier).
5. **`services/concurrency.py`'s `MAX_CONCURRENT_TTS`** was not touched -
   its own comment already anticipated this exact migration ("raise it once
   Kokoro is on the GPU pod"). Left at the default (4) for now; the RunPod
   endpoint's own `workers=(0,2)` queues extra requests rather than erroring
   (confirmed below), so there was no need to raise it just to turn this on.
   Revisit both together under real multi-user load.

### Tested before flipping the switch (all via the real `generate_tts()` path)

- Cold call: 25.6s, real MP3 (`ID3`)
- Warm call: 2.8s, real MP3
- 3 concurrent scenes against `workers=(0,2)`: 2 finished at 5.2s, 3rd queued
  and finished at 7.6s total - no error, no dropped scene, ~4x faster than
  3 sequential CPU calls would be
- Bad endpoint ID -> falls back to CPU Kokoro, real MP3 returned
- Bad endpoint ID with `TTS_RUNPOD_FALLBACK_TO_CPU=false` -> raises cleanly
  instead of silently succeeding
- Post-deploy, no env overrides (the actual production path): 33,645 B MP3
  in 14.3s

### Known risk not yet exercised

Concurrency was tested at 3 scenes; a real multi-scene story (5-10 scenes)
generating in production hasn't been watched end-to-end yet. Watch the first
few real stories after this deploy, and watch RunPod's account-wide worker
quota - ComfyUI (3) + kokoro-tts (2) already equals the account's 5-worker
cap, so there is no headroom for a third GPU workload without asking RunPod
to raise it.

### GPU tier / execution timeout: console is ground truth, not this repo

The RunPod Flash SDK used by `runpod-kokoro-test/kokoro_worker.py` exposes no
Secure/Pro-vs-Community-cloud toggle, and `execution_timeout_ms` was observed
not to apply on an in-place `flash deploy` update. Both were set by hand in
the RunPod console (24GB non-Pro tier, 120s execution timeout) and are **not**
guaranteed to survive a future `flash deploy` of that project. If the kokoro
endpoint is ever redeployed, re-check both in the console afterward.

---

## Concurrency & Scaling  (Tier 1 + Tier 2, 2026-07-26)

**Design goal: accept 50-100 simultaneous users without errors, lost jobs or
unfairness, on hardware that will change.** Every limit below is an environment
variable declared in `docker-compose.yml`. Moving to a bigger box, or to the GPU
RunPod for Kokoro, is a variable change plus a restart - never a code change and
never a rebuild.

### How a story is generated now

```
POST /api/upload
  ├─ admit_generation(user_id)        429 if queue full or user at their cap
  ├─ check_and_reserve_credit()       402 if out of credits
  ├─ write temp folder + job_state row (status 'initializing')
  └─ generation_queue.submit(...)     row in generation_queue, returns position
                                      ↓
        GENERATION_WORKERS asyncio workers drain the queue (FIFO)
                                      ↓
        run_ai_workflow_progressive_mobile()  ← holds its worker slot until the
                                                 WHOLE story is done, not just
                                                 the first playable scene
              ├─ Groq story text        →  llm_governor
              ├─ RunPod images          →  image_governor  (+ per-story cap)
              └─ Kokoro / Piper TTS     →  tts_governor
```

The workflow returns only after `asyncio.gather`-ing the background image and
TTS tasks it spawned. That is deliberate: if it returned when Scene 0 was ready,
a worker would immediately claim the next job while this story was still running,
and 4 workers would mean 12+ stories genuinely in flight - the exact unbounded
fan-out the queue exists to prevent.

### The knobs

| Variable | Default | What it controls | When to raise it |
|---|---|---|---|
| `GENERATION_WORKERS` | 4 | **The headline dial.** How many stories generate at once, process-wide | More CPU, or after Kokoro moves to GPU |
| `MAX_QUEUE_DEPTH` | 200 | Waiting jobs allowed before `/api/upload` returns 429 | Only if you are happy making users wait longer |
| `MAX_JOBS_PER_USER` | 3 | Concurrent queued+running jobs per account | Rarely - this is anti-abuse |
| `GENERATION_TIMEOUT_SECONDS` | 1800 | Hard ceiling per story; a hung job is reclaimed, marked failed and refunded | If stories legitimately exceed 30 min |
| `QUEUE_POLL_SECONDS` | 2.0 | Idle worker re-check interval (only matters multi-process) | Rarely |
| `QUEUE_RETENTION_HOURS` | 48 | How long settled queue rows are kept for the admin viewer | Rarely |
| `MAX_CONCURRENT_IMAGES` | 6 | Process-wide RunPod image calls | Track the RunPod endpoint's max workers |
| `MAX_CONCURRENT_TTS` | 4 | Process-wide TTS calls | **This is the one to raise after the GPU move** |
| `MAX_CONCURRENT_LLM` | 8 | Process-wide Groq calls | If Groq TPM limit is raised |
| `MAX_IMAGES_PER_STORY` | 4 | Per-story fairness cap inside `generate_images_parallel` | With `MAX_CONCURRENT_IMAGES` |
| `MYSQL_POOL_SIZE` | 32 | MySQL pool. **Hard-capped at 32 in code** (mysql-connector's `CNX_POOL_MAXSIZE`) | See the multi-worker note below |
| `THREAD_POOL_WORKERS` | 128 | Default executor for every `asyncio.to_thread` | Bounds blocking I/O, not CPU - oversizing costs memory only |
| `SQLITE_BUSY_TIMEOUT_MS` | 30000 | How long a blocked SQLite writer waits | Busier host |

### Tier 1 - the four hard ceilings that were there before (all fixed)

| Limit | Before | After |
|---|---|---|
| `asyncio.to_thread` pool | **7 threads** (`min(32, cpu_count+4)`, cpu=3) - every TTS/Groq/file write shared them | `THREAD_POOL_WORKERS` (128), set in `startup_event` |
| MySQL pool | 5, and `get_connection()` raises `PoolError` **instantly** when exhausted - it has no wait queue | 32 + `_acquire_connection()` bounded retry with linear backoff |
| Pool construction | Unlocked check-then-act singleton | Double-checked locking + failure cooldown (see below) |
| SQLite job state | `journal_mode=delete` (EXCLUSIVE whole-DB lock per write, blocks readers) + `synchronous=FULL` | WAL + `busy_timeout` + `synchronous=NORMAL` |
| `routers/upload.py` tts_preview | Bare `generate_tts()` - a blocking 90s `requests.post` **on the event loop** | `await asyncio.to_thread(...)`, plus the TTS governor |

Measured: 60 concurrent MySQL ops 6/60 → **60/60 in 2.0s**; 64 `to_thread` calls
7 threads/5.0s → **64 threads/0.5s**; 100 concurrent `/api/health` (real MySQL +
SQLite per request) → **100/100, p95 282 ms**.

### The pool-construction bug (found by load-testing, was live in production)

`get_connection_pool()` was `if pool is None: pool = MySQLConnectionPool(...)`
with no lock, and **`MySQLConnectionPool.__init__` opens `pool_size` connections
eagerly**. Every concurrent caller that saw `None` built its own pool, so the
total was `callers x pool_size`. 60 concurrent requests attempted ~1900
connections against a server allowing 151: 54 of 60 failed with
`1040 Too many connections` and `Max_used_connections` peaked at **152/151**.

This pre-existed at `pool_size: 5` (60 x 5 = 300, also over). Raising the size
only made it loud enough to detect. It fires on the first concurrent requests
after **any** restart, and again whenever a transient error nulls the singleton
and every in-flight request retries at once. Fixed with double-checked locking
plus `MYSQL_POOL_RETRY_COOLDOWN` (2s), so an unreachable database produces one
construction attempt per interval instead of a connection storm.

### Durability and restart behaviour

- Jobs still **`queued`** at startup survive and are picked up. This is the whole
  point of writing the queue down.
- Jobs **`running`** at startup are marked failed and the credit refunded. They
  are deliberately *not* re-run: the workflow writes scene rows under
  deterministic ids (`{story_id}_scene_{n}`), so a second pass collides on the
  primary key, and any RunPod images already paid for would be paid for twice.
- **Ordering matters in `startup_event`:** `generation_queue.start()` runs
  *before* `reconcile_orphaned_jobs()` and issues its own refunds, because it
  marks those stories `failed`. Reverse the two and the reconciler still sees
  them as `processing` and refunds the same credit a second time.

### Fairness and observability

- FIFO is enforced by an `AUTOINCREMENT seq`, **not** `created_at` - timestamps
  have 1-second resolution, so a burst of uploads in the same second would have
  no defined order.
- `claim_next_job()` uses **`BEGIN IMMEDIATE`**. A deferred transaction only
  escalates to a write lock at the `UPDATE`, by which point two workers have both
  already read the same row as queued. This is what makes the queue safe across
  uvicorn worker processes. Verified: 8 concurrent claimers over 10 jobs, each
  claimed exactly once.
- `GET /api/health` reports live governor utilisation (`in_use`, `waiting`,
  `peak_wait_s`, `acquired_total`) and queue depth. **`peak_wait_s` and `waiting`
  are how you decide whether raising a limit would change anything** - a ceiling
  you cannot observe is a ceiling you cannot tune.
- `GET /api/status/{id}` returns `queue_position` while the job is waiting
  (queried only when `status == 'initializing'`, since every client polls this
  every 2 seconds).

### Frontend coupling (do not break these)

- `App.jsx`'s poll loop has **stall detection: 5 minutes with no forward motion
  aborts the story with "this seems stuck"**. A queued job has progress 0 and 0
  scenes by definition, so it looks exactly like a hang. `queue_position` is
  folded into the stall marker and the trip is skipped entirely while
  `position > 0`. **If the backend ever stops sending `queue_position`, healthy
  queued stories start getting killed by the frontend after 5 minutes.**
- Admission control returns **429** with a JSON object `detail`
  (`{error, message, ...}`). Both upload paths (XHR in `handleFileUpload`, fetch
  in `generateStory`) read `detail.message`. Never render `detail` directly -
  React cannot render an object child.

### Known limits / what is next

- **`uvicorn --workers 3` is blocked on the MySQL connection budget.** 3 workers
  x `MYSQL_POOL_SIZE=32` = 96 connections from this app alone, plus ~32 from the
  other containers sharing MySQL at `10.0.0.147` = ~128 of 151. Going
  multi-worker requires raising the server's `max_connections` **or** setting
  `MYSQL_POOL_SIZE=24`. WAL and `BEGIN IMMEDIATE` already make the queue itself
  multi-process safe.
- Code changes alone do **not** make 100 stories generate simultaneously - that
  is still gated by 3 vCPUs and CPU-bound Kokoro. What they do is make 100 users
  a queue-depth number instead of an outage, and make the GPU migration a dial
  turn rather than a rewrite.
- `generate_scene_media_progressive()` was **deleted** on 2026-07-26 (81 lines,
  zero callers). It was the last ungoverned TTS/image path - a tempting template
  that acquired none of the semaphores. If you need a per-scene helper, start
  from `generate_remaining_tts()`, which goes through the TTS governor.

---

## Notes
- API keys/secrets live in root `.env` (docker-compose reads from here, not `backend/.env`).
- Backend files owned by host `ubuntu` user (UID 1001); container now runs as that same UID.
- `backend/routers/` contains stray misplaced files unrelated to routing (`VoiceSelector.jsx`/`.css`, a per-directory `Dockerfile`/`requirements.txt`) — leftover from an earlier project structure, harmless but confusing if browsing that folder.
- See also: `edusmart/PROJECT_CONTEXT.md` (older detailed reference, may be stale relative to this file).

## Working on this repo (environment gotchas)

- **Source files are `www:www`; the shell user is `ubuntu` (uid 1001, not in
  group `www`).** Direct edits fail with EACCES. The workflow that works: write a
  Python patcher to a scratch dir, guard every replacement with
  `assert src.count(old) == 1`, back up with `shutil.copy2` + timestamp, and run
  it with `sudo python3`. Reads need no sudo.
- **`python3 -m py_compile` fails with "Permission denied" on `__pycache__`**
  (also `www`-owned). That is not a syntax error - set
  `PYTHONPYCACHEPREFIX=/some/scratch/dir` and it compiles fine.
- **Running a script inside the container needs `PYTHONPATH=/app`.** `-w /app`
  alone is not enough: Python puts the *script's* directory on `sys.path`, not
  the working directory.
- `sqlite3` CLI is **not installed** in the backend image. Inspect the job-state
  DB with `docker exec edusmart-backend python3 -c "import sqlite3; ..."`.
- `get_db_cursor()` defaults to `commit=False` - writes need `get_db_cursor(commit=True)`.
- The MySQL table for saved stories is `user_stories`, not `stories`
  (`stories` is the SQLite job-state table).
- Measuring the app's thread pool with `asyncio.run()` **cannot work** - it
  builds a fresh loop with the stock executor. Configure the loop the same way
  `startup_event` does, or read the startup log line.

