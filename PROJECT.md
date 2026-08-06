# PROJECT.md — LearnTale (formerly EduSmart, in-app branding only — see "Brand rename" note below)

## Purpose
AI-powered educational storybook platform. A user (teacher/parent/student) uploads a lesson document (PDF/DOCX), picks a grade level, and the backend turns it into an illustrated, narrated interactive story with a 10+ question quiz. Stories can be played immediately, saved to an account, downloaded as an offline ZIP, or synced for offline playback via PWA.

## Brand rename (2026-08-05, in progress)
Product renamed from "EduSmart" to "LearnTale" in every user-facing string (page title, PWA manifest, logo/wordmark, share text, verification/reset emails). Deliberately **not yet changed**: the domain (still `edusmart.ign3el.com`), repo/deploy directory (`/www/wwwroot/edusmart`), Docker container/service names, MySQL DB/pool names, and client-side storage keys (`edusmart_*` localStorage keys, `EduSmartDB` IndexedDB) — the last group is live in real users' browsers, so renaming those keys would orphan existing saved offline stories/sessions/quiz progress. Domain is `learntale.app`, not purchased yet; cut over nginx/DNS/CORS/env vars only once it's bought and pointed here.

## Stack
| Layer | Tech |
|---|---|
| Frontend | React 18 + Vite, react-router-dom v7, framer-motion, react-three-fiber/drei (background only), axios |
| Backend | Python FastAPI (single `uvicorn main:app`, no multi-worker). Story generation runs through a durable SQLite-backed queue with a fixed worker pool - see Concurrency & Scaling |
| Story generation | **Gemini `gemini-3.5-flash-lite`** (primary), Groq `openai/gpt-oss-120b` (fallback on Gemini 503). See *LLM model split* below — this choice is load-bearing, not incidental |
| Page/image reading (vision) | **Gemini `gemini-3.1-flash-lite`** — deliberately a *different* model from story generation |
| Images | RunPod ComfyUI (FLUX.1-dev), with a monthly AED spend cap |
| TTS (English) | Self-hosted Kokoro-82M (`kokoro-tts:8880` container), reached via two parallel client wrappers |
| TTS (Arabic) | A hosted Piper-compatible endpoint (`TTS_API_URL`) — **only reachable from the admin TTS-test tool and the voice-preview endpoint, not from the actual story-generation pipeline** (see Known Issues) |
| Main DB | MySQL — users, saved stories |
| Job-tracking DB | SQLite (`db_data/job_state.db`, Docker named volume) — in-progress/generated story state |
| Mobile | PWA (service worker, install prompt, offline story storage via IndexedDB/localStorage) |

## User-chosen quiz size + capacity check (2026-08-03, `20260803_205230`)

Quiz length is a **user choice** (5/10/15/20, default 10), not a hardcoded rule,
and a short quiz is **never** a fatal validation error — the 2026-08-03 failure was
a complete, correct story destroyed because its quiz had 7 questions instead of 10.

- `QUIZ_SIZE_OPTIONS` / `DEFAULT_QUIZ_SIZE` / `normalize_quiz_size()` in
  `services/story_service.py`; the frontend list in `FileConfirmation.jsx` must
  match. `normalize_quiz_size` never raises — junk snaps to the nearest offered
  size, so a bad form field can't fail an upload whose file already crossed the wire.
- `MIN_VIABLE_QUIZ = 3` is the only fatal floor. Below it the *generation* went
  wrong; above it the *document* was thin, which is a notice, not an error.
- **Quiz size must never drive scene count.** The prompt says so explicitly and
  `tests/test_quiz_sizing.py` asserts the line is still there — the model
  otherwise aligns the two counts on its own.

### Capacity check (`estimate_question_capacity`)

Runs on the confirm screen over the text `/api/upload/extract-text` already
extracts for language detection. **Deliberately a free heuristic, not a model
call**: no quota, no latency, cannot itself fail. It only decides whether to *ask*
the user before a credit is spent; the model is still told to produce the exact
requested count, and a real shortfall comes back afterwards as `quiz_notice` on a
delivered story.

Returns `None` — meaning *no opinion*, and the UI must stay silent — when native
text is under `_CAPACITY_MIN_CHARS` (800). **This is the case that matters:** the
check reads pypdf/docx output while the generator *also* vision-reads the pages,
so a scanned PDF looks empty here and full to the model. Warning "this can only
make 3 questions" on a document that comfortably makes 20 is worse than saying
nothing. Bounded by characters *and* sentence count — padding must not buy
capacity. Measured: the chem chapter → 18, a 1.3k-char handout → 3.

## LLM model split (2026-08-03) — read before changing any model name

Two Gemini models, two jobs, on purpose. **Gemini's free tier meters RPM/RPD per
model**, so putting both jobs on one model makes them share a single
500-requests-a-day pool; splitting them gives each its own.

| Job | Model | Free-tier limits | Volume |
|---|---|---|---|
| Story + quiz | `gemini-3.5-flash-lite` | 15 RPM / 500 RPD / 250K TPM | ~1.5 calls per story |
| Page reading | `gemini-3.1-flash-lite` | 15 RPM / 500 RPD / 250K TPM | 2-6 calls per document |

Vision is the tighter constraint (~125 stories/day vs ~333), which is why it gets
a pool to itself. `_STORY_MODEL` and `_VISION_MODEL` in `services/story_service.py`
**must stay different models** — there is a test asserting this
(`tests/test_story_model_split.py`).

### Why story generation moved off Groq

Groq's free `on_demand` tier caps this account at **8000 tokens per minute**, and
Groq charges *prompt tokens + requested `max_tokens`* against that one budget.
That forced the document to be truncated to 6500 characters before the model saw
it — not for quality reasons, purely to fit the ceiling.

Measured on a real NCERT Class 10 chemistry chapter (13,614 chars extracted), the
cut silently discarded:

| Topic | Char position |
|---|---|
| washing soda | 6,573 |
| bleaching powder | 6,590 |
| chlor-alkali process | 6,769 |
| Plaster of Paris | 9,025 |

Every story from that document covered half the chapter and nothing reported it.
How close to the edge this was: adding ~60 tokens of prompt produced a 413 reading
`Limit 8000, Requested 8004` — **four tokens over**.

Gemini 3.5-flash-lite accepts ~1M input tokens against 250K/min. Same document,
untruncated: 13.7s, 8 scenes, 10 questions, including a chlor-alkali question the
Groq path could not physically have produced. Also faster than the 12.2s Groq run
(and 34.2s end-to-end including one grade-calibration regeneration).

**Groq remains the fallback** for Gemini 503 `UNAVAILABLE` ("experiencing high
demand"), which is observed in practice and is Google-side, not ours. The fallback
path still truncates to `_GROQ_MAX_DOC_CHARS` because its TPM ceiling has not
moved — a fallback story is therefore *less complete* than a primary one.

If TPM ever needs raising rather than routing around: Groq Dev Tier (paid) is the
lever. Do not shave `_GROQ_MAX_DOC_CHARS` further; there is almost nothing left.

## Deploy
Docker Compose (`docker-compose.yml` at repo root, one file for both `backend` and `frontend` services),
aaPanel nginx reverse proxy in front. Deploys go through `./deploy.sh` (tagged images + rollback) —
see Quick Commands. Rollback verified end-to-end 2026-07-25: `/api/health`'s `version` field flipped
to the older build and back, healthy both ways. As of 2026-08-04 both services deploy blue/green -
see "Zero-downtime deploys" below.

## Zero-downtime deploys (blue/green) (2026-08-04, backend `20260804_165255` / frontend `20260804_170702`)

**The problem this replaces:** every deploy used to destroy-then-create a single named container
(`docker compose up -d --no-deps <svc>` on a service that only ever had ONE instance). Backend
rebuilds 502'd every `/api` call for the ~30s the new container took to boot (frontend's nginx
resolves `backend` by Docker DNS, and that name pointed at nothing during the gap); frontend
rebuilds took the whole site down, not just the API, since aaPanel's host nginx proxies straight
to one fixed port. Any story mid-generation during a deploy was also just killed.

**The fix:** each service is now TWO containers, `-blue` and `-green`, sharing one image, one
build, and (for backend) one data volume. Normally only one color is live; a deploy builds the
image, brings up whichever color is NOT currently running, health-checks THAT color directly
(bypassing nginx entirely), and only stops the old color once the new one is proven healthy. If
the new color fails its health check, the old one was never touched - no outage, just a failed
deploy to investigate. **Verified live, twice, with a continuous request-polling harness against
the real public domain through an actual production deploy: 0 dropped requests, steady ~0.5-0.9s
response times straight through the exact moment the old color stopped** (both backend and
frontend, 2026-08-04).

**Backend** (`backend-blue`/`backend-green`, container ports 8000/8001, Docker-DNS-routed):
- `frontend/nginx.conf`'s `/api` location does NOT use a plain nginx `upstream {}` block for
  this - that resolves server names ONCE at nginx startup/reload and **hard-fails the whole
  frontend container** if either color isn't currently running (confirmed locally: `nginx -t`
  errors "host not found in upstream"), which is routine under this scheme, not an edge case.
  Instead it uses `proxy_pass` to an nginx *variable* (`set $api_backend "http://backend-blue:8000";
  proxy_pass $api_backend;`) plus a `resolver 127.0.0.11` directive (Docker's embedded DNS) so
  resolution happens per-request, not at startup, and `error_page 502 503 504 = @api_fallback`
  retries `backend-green` transparently within the same client request. Verified all three states
  live: both up (blue serves), blue absent from boot (green serves, nginx still starts fine), blue
  stopped mid-operation (falls through to green with zero client-visible error).
- **Shared state across the two containers, verified safe for concurrent access:**
  - `job_state.db` (SQLite, `backend_db` volume) - `job_state.py`'s `claim_next_job()` already used
    `BEGIN IMMEDIATE` before this work, specifically so two processes can't both claim the same
    queued job. Not something this change had to add - it was already built for this.
  - RunPod spend counter (`runpod_usage.json`) and vision daily budget (`vision_usage.json`) -
    these WERE only protected by in-process locks (`asyncio.Lock` / `threading.Lock`), which do
    nothing across two separate containers. Added `fcntl.flock`-based cross-process locking
    (`services/story_service.py`'s `_cross_process_file_lock`, `services/vision_budget.py`'s
    `_cross_process_lock`) on a dedicated `.lock` file next to each counter. Proved the race was
    real first: an unlocked control script running two processes against the same counter lost
    updates and even corrupted the file outright (partial-write JSONDecodeError) under concurrent
    access; the flock'd version held at 1000/1000 increments across two real concurrent processes.
  - Per-color RAM/connection budgets (`MYSQL_POOL_SIZE`, `MAX_CONCURRENT_*`) are NOT shared - each
    color enforces its own ceiling independently, so a deploy overlap can transiently run up to 2x
    those numbers (e.g. up to 64 MySQL connections against the server's 151 cap). Comfortable
    today; re-check before raising these numbers on bigger hardware.
- Draining: before stopping the outgoing color, `deploy.sh` polls its `/api/health` `queue.running`
  count for up to 180s so an in-flight generation isn't killed by a routine deploy. On timeout it
  stops anyway - `job_queue.py`'s existing `CancelledError` handler marks the job failed rather
  than leaving it stuck, and the startup orphan-reconciler refunds the credit.

**Frontend** (`frontend-blue`/`frontend-green`, host ports 3004/3009): stateless, so no draining
and no shared-state concerns - a plain host-level `upstream {}` in a standalone nginx conf file is
safe here, since the members are literal `127.0.0.1:port` targets, not hostnames needing
resolution. Routed at the aaPanel host-nginx layer, NOT inside a container - see below.

**⚠️ aaPanel fragility (durable, but re-check after any panel UI edit to this site):** the upstream
pool definition lives in `/www/server/panel/vhost/nginx/edusmart-frontend-upstream.conf`, a
standalone file (not one aaPanel generates), picked up by the panel's own wildcard
`include /www/server/panel/vhost/nginx/*.conf;` - safe from being deleted by panel edits. The
vhost's `proxy_pass` target, however, lives inside aaPanel's UI-managed files
(`/www/server/panel/vhost/nginx/edusmart.ign3el.com.conf` AND the JSON it's regenerated from,
`/www/server/proxy_project/sites/edusmart.ign3el.com/edusmart.ign3el.com.json` - both were updated
to `http://edusmart_frontend_pool` so a panel-triggered regeneration stays correct too). If the
site's reverse-proxy settings are ever edited again through the aaPanel UI, **check that
`proxy_pass` still reads `http://edusmart_frontend_pool` afterward** - the panel can silently
revert it back to a literal `http://127.0.0.1:3004` depending on what triggers regeneration.
Reapply: `sudo sed -i 's|proxy_pass http://127.0.0.1:3004;|proxy_pass http://edusmart_frontend_pool;|'
/www/server/panel/vhost/nginx/edusmart.ign3el.com.conf && sudo nginx -t -c /www/server/nginx/conf/nginx.conf
&& sudo systemctl reload nginx` (test before reload, always).

**Ports on this shared VPS**: 3005 looked free but is `ai-kids-quiz-frontend-1`, an unrelated
project - frontend-green uses 3009 instead. `deploy.sh --status` shows which color is live on
which port for both services without needing `docker ps`.

**One-time migration cost, already paid**: cutting over from the old single-container scheme to
this one needed a real (~30-60s) gap on 2026-08-04, because the legacy `edusmart-backend`/
`edusmart-frontend` containers held the exact host ports the new `-blue` containers needed. Every
deploy from here on is zero-gap; that transition cost doesn't recur.

## Domain & Port
- Domain: edusmart.ign3el.com (Cloudflare proxy)
- Containers: `edusmart-backend-blue`/`edusmart-backend-green` (ports 8000/8001, only one normally
  running), `edusmart-frontend-blue`/`edusmart-frontend-green` (ports 3004/3009, same). See
  "Zero-downtime deploys" above.
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
| Which color is live? | `./deploy.sh --status` |
| List rollback points | `./deploy.sh --list` |
| Roll back | `./deploy.sh --rollback backend 20260725_201915` |
| Which build is live? | `curl -s https://edusmart.ign3el.com/api/health` → `"version"` field |
| Logs | `./deploy.sh --status` first, then `docker logs edusmart-backend-<color> --tail 50` |
| Job-state DB (live) | `docker exec edusmart-backend-<color> sqlite3 /app/db_data/job_state.db` (either color - it's the same shared volume) |

**Backend code changes now REQUIRE a rebuild.** There is no `./backend:/app` mount any more —
editing a `.py` file on the host does nothing until you `./deploy.sh backend`. Data directories
(`outputs`, `uploads`, `saved_stories`, `generated_stories`) and the `backend_db` volume are
still mounted, so nothing persistent lives in the image.

**Anything the backend writes must go to a mounted path.** `db_data/` (named volume) is the
home for state files: `job_state.db`, `runpod_usage.json`, `hash_cache.json`. Writing anywhere
else inside `/app` now lands in the container's ephemeral layer and is destroyed on the next
deploy — silently. `hash_service.py` was doing exactly that (`backend/hash_cache.json`, which
only survived via the old mount, hence the odd `backend/backend/` directory); moved 2026-07-25.

**Always use `--no-deps`.** Originally because `frontend` had `depends_on: backend`, so a plain
`docker compose up -d --build frontend` silently recreated `backend` too (config-hash check on the
dependency) even though nothing backend-related changed - wiping its container logs in the process
(confirmed 2026-07-20, cost us two rounds of backend-timing diagnosis on a live perf complaint).
That specific `depends_on` was removed 2026-08-04 as part of the blue/green work (see "Zero-downtime
deploys") - nginx already tolerates either color being down, so frontend no longer needs to wait on
backend at container-start time. `--no-deps` is kept anyway: `deploy.sh` already passes it
everywhere, and it's the right default the moment any service gains a new dependency later.

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
2. **upload** (`FileUpload.jsx`) — drag-drop PDF/DOCX/DOC + grade level (KG-1, KG-2, Grade 1-10 — id sent to the backend is `"KG1"`/`"KG2"`/`"1"`..`"10"`, matching `backend/services/grade_bands.py` exactly). On file select, `App.jsx`'s `handleFileUpload`:
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
- `process_file_to_story()` — the real generation call. Extracts text from the uploaded file, sends a structured prompt to **Groq** (`response_format=json_object`), validates the JSON (`_validate_story_json`), tops up to ≥10 quiz questions if needed via `_ensure_minimum_questions()` (a real, live Groq call through `self.groq_client` — an earlier version of this note called it dead code left over from a pre-Groq Gemini implementation; that's no longer true, it fires whenever the model under-delivers on the first pass). Prompt is capped to 5-10 scenes, document text truncated to 15k chars, includes an injection-defense clause and a content-safety refusal path (`{"error": "content_unsuitable"}`) — added 2026-07-19.
- **Grade-band calibration (2026-07-26, `services/grade_bands.py`)** — single source of truth for what "age-appropriate" means at each grade, read by every prompt-building site below instead of each hardcoding its own logic. `grade_level` sent from the frontend is now one of `"KG1"`, `"KG2"`, `"1"`..`"10"` (was previously a bare int like `"3"` with zero grade-tuning behind it beyond the literal digit landing in the LLM prompt). Grades are grouped into 4 tiers (`early`/`lower`/`middle`/`upper`) each with a vocabulary constraint, sentence-length target, narrative length, quiz Bloom's-level ceiling, image illustration-complexity style, and TTS narration speed. `resolve_grade_spec(grade_level)` falls back to the Grade-4/`lower` spec for any unrecognized value rather than raising. Wired into: `process_file_to_story`'s `unified_prompt` (story text + quiz), `_ensure_minimum_questions`'s top-up prompt, `_generate_image_unbounded`'s `style_guide` (previously only varied by `is_mobile`, never by grade), and `_generate_and_cache_tts`'s narration `speed` (previously hardcoded `1.0` regardless of grade). `routers/admin.py`'s story retry endpoint looks up the original story's `grade_level` from `job_state` so a repaired scene matches the original grade's style/pace instead of silently falling back to default.
- `generate_image()` / `generate_images_parallel()` — RunPod ComfyUI FLUX calls, up to 4 concurrent, with a monthly AED spend cap enforced via an `asyncio.Lock`-protected reserve-before-spend check against `services/runpod_usage.json`. Now grade-aware (see above) via an added `grade_level` param threaded through from `main.py`.
- `generate_progressive_tts()` / `_generate_and_cache_tts()` — batched TTS generation (batch_size=2) for scenes 1-N, caches audio to `outputs/audio_cache/`, writes progress to `outputs/status/{story_id}.json`. Narration speed is now grade-derived (see above); `services/chatterbox_client.py`'s `generate_audio()` also gained a `speed` param (default `0.9`, unchanged from before) for the same reason on the Scene-0 fast path.
- **Dead code inside this class** (harmless but never executes): `generate_scene_priority()` calls `self.client.models.generate_content(...)` — `self.client`/`self.text_model` are never initialized anywhere (leftover from a pre-Groq Gemini implementation) and the method is never called at all.

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

## Grade-band content calibration (2026-07-26, backend `20260726_220159` / frontend same)

Extended the grade selector from 7 options (`KG-1/Grade 1` combined .. `Grade 7`)
to 12 distinct grades: KG-1, KG-2, Grade 1 through Grade 10
(`FileUpload.jsx`, `FileConfirmation.jsx`). See the grade-band note under
"Story generation pipeline" above for what actually reads the new
`grade_level` id (`"KG1"`/`"KG2"`/`"1"`..`"10"`) and what changed in the
story/image/quiz/TTS prompts.

**Known, deliberately out of scope:** `FileConfirmation.jsx` still has a
"Narration Speed" slider (0.5x-2.0x) that gets captured into `speed` and sent
to `/api/upload`, and is stored in the story folder metadata - but nothing in
the generation pipeline ever reads it back out. TTS speed is now fully
grade-derived (see above) instead of the old hardcoded `1.0`, so the slider
was already inert before this change and remains inert after it - this pass
didn't touch that wiring. If the user-facing slider is meant to layer on top
of (or override) the grade default, that needs a product decision on how the
two should combine, then wiring `speed` through `run_ai_workflow_progressive_mobile`
into `generate_progressive_tts`/`_generate_and_cache_tts`/`chatterbox.generate_audio`
the same way `grade_level` was just wired through.

---

## Incident: every upload 500s on `generated_stories` EACCES (2026-07-26, frontend `20260726_230120`)

### Symptom
`POST /api/upload` returned 500 for every file. Looked file-specific (reported against
one chemistry PDF at Grade 10) but was total — the logs showed exactly one `/api/upload`
in 90 minutes and it failed. Retrying with a known-good PDF *looked* like it worked
because `check-duplicate`, `extract-text` and `tts-preview` all still returned 200; only
the final upload was broken.

### Root cause
```
File "/app/main.py", line 1083, in upload_story
  temp_dir = storage_manager.create_story_folder(...)
File "/app/story_storage.py", line 51, in create_story_folder
  os.makedirs(story_dir, exist_ok=True)
PermissionError: [Errno 13] Permission denied: 'generated_stories/<uuid>'
```
The backend container runs as `1001:1001`. `backend/generated_stories/` on the host was
`www:www` mode `0775` — uid 1001 is neither owner nor in gid 1002, so it had `r-x` only.

The other three bind mounts (`outputs`, `uploads`, `saved_stories`) were *also* owned by
`www`, and only worked because they happened to be mode `0777`. So the docker-compose
comment claiming `user: "1001:1001"` "matches ownership of all bind mounts above" was
false — the whole thing was standing on a world-writable-directory accident. The first
data dir that got recreated with sane permissions took production down.

### Fix
1. `chown 1001:1001` on all four bind mounts, so the compose comment is now actually true.
2. `deploy.sh` asserts it on every deploy: creates any missing data dir as `1001:1001 0775`
   and re-chowns any that drifted, printing what it fixed. The app cannot self-detect this
   (it only fails at request time, per-request), so the check belongs at deploy time.

### Verified
- `os.makedirs` inside the container on `generated_stories/` — the exact failing call — now succeeds.
- The reported PDF extracts fine (6 pages, 9,348 chars), so it was never the file.

### Do not
- Do not "fix" a future EACCES here by `chmod 777`. That is what hid this for weeks.
- Do not assume a bind mount is writable because the app has been up for days — nothing
  writes to a fresh story folder until someone uploads.

### Related frontend bug fixed in the same pass
`handleFileUpload()` never called `setError(null)`. Only `generateStory()` and `resetApp()`
cleared it, so a failed upload's red banner stayed pinned above the upload form through
every subsequent attempt — making a recovered system still look broken. Error state is now
cleared at the start of each new attempt.

---

## Upload flow: one path, confirm screen always reachable (2026-07-26, frontend `20260726_231756`)

### What was wrong
`FileUpload.jsx` fires `onUpload` the instant a file is dropped, and `App.jsx` wired that
straight to `handleFileUpload`, which ran the *entire* upload and called
`navigateTo('generating')`. The confirm/voice screen was fully built and rendered for
`step === 'confirm'`, but the only thing that ever routed there was the duplicate-detection
modal's `onCreateNew`. Testing repeatedly with the same file always tripped duplicate
detection, so the screen appeared every time and the bug stayed invisible until the first
genuinely new file was uploaded — which skipped voice selection entirely and generated with
whatever `voice` happened to be in state.

There were also **two complete upload implementations**: `handleFileUpload` (XHR, progress
bar, 429/402 handling) and `generateStory` (fetch, no progress, its own 429/402 handling,
and an `avatar_type` form field the backend does not even accept). `handleAvatarSelect` was
dead code with no caller. Two copies is precisely how one path silently lost a screen.

### Shape now
- `handleFileUpload(file)` — selects the file, runs `/api/check-duplicate`, then stops at
  `confirm`. **Picking a file selects it; it does not submit it.**
- `startUpload(selectedVoice)` — the single upload implementation.
- `handleConfirmFile(settings)` — the only caller of `startUpload`.
- `generateStory` and `handleAvatarSelect` deleted.

### Do not
- Do not read `voice` from state inside `startUpload`. The confirm screen calls `setVoice()`
  and `startUpload()` in the same tick, so the state update has not landed — the user's
  choice would be silently replaced by the previous value. That is why the voice is a
  parameter, not a state read.
- Do not send upload failures back to `step === 'upload'`. The file and voice are still
  valid; `confirm` makes the retry one tap instead of re-picking the file.

### Generation timing (measured, story `ad6170b5`, RunPod GPU)
| Event | Δ from upload |
|---|---|
| `POST /api/upload` accepted | 0s |
| Scene 0 image saved, player can render | **+34s** |
| Fully generated (9 scenes) | **82.8s** |

Per-scene TTS is **2.9-5.5s** — the GPU is not the bottleneck. The 34s before the second
screen is LLM story generation plus the first image, which are sequential and block the
first render. Anyone "optimising TTS" to fix perceived slowness is optimising the wrong
thing; the fix is rendering story text before the first image is ready.
**Done 2026-07-26** - see "Player opens on text, not on assets" below. The 26.5s scene 0
TTS in this table was also the CPU path, not the GPU; see "Scene 0 narration never used
the GPU".

## Profile storage card: counted a database that does not exist (2026-07-26)

`UserProfile.jsx` hand-rolled `indexedDB.open('EduSmartOfflineDB', 1)`. The app writes to
`'EduSmartDB'` (`utils/storyStorage.js`). The store lookup therefore always failed, the
count sat at 0 permanently, and — because `indexedDB.open()` creates a database that does
not exist — every visit to the profile page silently spawned an empty junk DB in the
browser.

Fixed by going through `storyStorage.listStories()` so one module owns the DB name, and by
splitting the misleading single "Saved Stories" number into two honest rows: **Offline on
This Device** (IndexedDB + localStorage) and **Saved to Your Account** (`/api/list-stories`,
de-duplicated by `story_id` the same way `LoadStory` does, so it matches the library count).

---

## Account deletion: `DELETE /api/auth/me` (2026-07-26, backend `20260726_235513`)

Both app stores reject an app that lets you create an account but not delete one. The
endpoint erases the user and everything they own, and is not reversible.

**A user's data lives in four places, and only one of them cascades.**

| Where | Cleaned up by |
|---|---|
| MySQL `users` | `DELETE` — FKs cascade to `user_stories`, `credit_transactions`, `promo_redemptions`, `email_verifications`, `password_reset_tokens` |
| **SQLite `job_state.db`** (`stories`, `scenes`, `generation_queue`) | `job_manager.delete_all_for_user()` — **explicit**, no FK spans MySQL→SQLite |
| Disk (`generated_stories/`, `saved_stories/`) | `storage_manager.delete_story()` per story_id |
| Stripe | `stripe.Subscription.cancel()` |

**Order is load-bearing** (`UserOperations.delete_account`): Stripe → files → SQLite →
**MySQL user row last**. A failure at any earlier step then leaves the account intact and
the whole thing retryable. Delete the user row first and a crash at step 2 destroys the
only handle on the remaining data — the story_ids are reachable only via `user_id`.

**Three guards, all of them load-bearing:**
1. **Re-authentication.** Password in the request body; social-only accounts type their
   own email instead. A bearer token alone must never be enough — a token can be lifted
   from a shared device, a password cannot.
2. **Last admin → 403.** Otherwise the admin panel becomes permanently unreachable.
3. **Live generation job → 409.** A worker mid-generation holds open paths under
   `generated_stories/<id>`; `rmtree` under it gives half-written stories and a traceback
   per remaining scene. Same rule as save/rename.

### Do not
- **Do not make guard 3 status-only.** `has_active_job()` also checks staleness against
  `GENERATION_TIMEOUT_SECONDS` — the same env var the queue uses, so they cannot drift.
  The first version didn't, and a story left `processing` by a dead worker would have
  blocked its owner from ever deleting their account. Caught by the probe, not by review.
- **Do not swallow a Stripe failure.** It raises, and nothing is deleted. Erasing the row
  while a subscription still bills is a charge nobody can trace or refund.
- **Do not use `stripe.Subscription.delete()`** — removed from the SDK long before the
  15.3.1 pinned in `requirements.txt`. It is `.cancel()`.
- **Do not count stories after deleting folders.** `storage_manager.delete_story()` drops
  the job_state row as a side effect, so a count taken afterwards only sees stragglers.

### Verified
`scratchpad/probe_delete_account.py`, run against **the public domain** so nginx and
Cloudflare are in the path (a `DELETE` with a request body is the part most likely to be
stripped in transit — it isn't). Creates a throwaway user directly in MySQL, never via
`/signup`, because that endpoint sends a real verification email.

All 7 checks pass: wrong password → 400 and account intact · live job → 409 · stale job
ignored · deletion → 200 · MySQL/SQLite/disk all empty afterwards · deleted user's token
→ 401.

## Scene 0 narration never used the GPU (2026-07-26)

`TTS_BACKEND=runpod` routed scenes 1..N to the RunPod Kokoro endpoint (**2.9–5.7s each**)
while **scene 0 — the only one blocking the player — ran on the CPU container at 26.5s**.

There are two TTS clients. `services/kokoro_client.py` is the dispatcher that reads
`Config.TTS_BACKEND`. `services/chatterbox_client.py` posts straight at `CHATTERBOX_URL`
and **has never consulted `TTS_BACKEND` at all**. `main.py` called the latter for scene 0.
Flipping the backend to GPU therefore silently skipped the one scene the user waits on.

Scene 0 now goes through `kokoro_client` like every other narration path, keeping its
`tts_governor` slot and its grade-derived speed, with the `ar_teacher` → Piper branch
mirrored from `story_service`. `main.py` no longer references `chatterbox` at all.

**The `~22.4s` in `✓ TTS generated via Kokoro: 367148 bytes (~22.4s)` was never a timing.**
It is `len(audio_bytes) / (16 * 1024)` — an estimated *playback duration* from the byte
count, printed in a format indistinguishable from elapsed time. The real elapsed figure is
`main.py`'s own `✓ Scene 0 TTS generated in 26.54s`. Do not diagnose against that log line.

## Player opens on text, not on assets (2026-07-26, `20260726_235040`)

Measured, 10-scene story: **LLM finishes at +7.6s**, scene 0 image at +26.7s, player
opened at **+34s**. The story text sat in SQLite for 26 seconds while the user watched a
spinner.

Cause was one filter in `/api/status/{job_id}`: a scene was only published once its image
**and** audio were both `completed`, and `App.jsx` only opens the player once it sees one
scene. `/api/status` now publishes every scene that has **text**, with `image_url` /
`audio_url` `null` until each asset lands.

### Do not
- **Do not let a text-only scene count as progress.** `completed_scene_count` and
  `progress` still mean *both assets present*. The client's stall detector watches that
  number for forward motion — count text and it would see a finished story the instant the
  LLM returns, then nothing for a minute, and fire a false "this story is stuck".
- **Do not treat a null url as an error in `StoryPlayer`.** Null means *not made yet*.
  Assigning `src=""` and calling `load()` fires a media error and leaves the audio element
  unusable for that scene.
- **Do not reset play state when an asset arrives.** The scene-change effect now re-runs
  mid-scene as urls fill in. Anything representing user intent (`userPausedRef`,
  `autoPlayedRef`, `pendingAdvanceRef`) is reset only on a genuine scene change, and the
  audio element is only re-`load()`ed when the audio url itself changed — otherwise the
  picture landing would rewind live narration to 0, or resume a story the user paused.

Play is disabled with "narration is still being recorded" until audio exists; the image
slot shows "Ollie is painting this picture…".

**Not measured end-to-end.** Verifying the new numbers needs a real authenticated upload,
which costs GPU credits. Expected: player at ~8s instead of 34s, narration a few seconds
later instead of 34s.

## Follow-up: "published" is not "playable" (2026-07-27, frontend `20260727_080546`)

Beta report: *"Scene x of y which was earlier didn't show again."* Correct — and the cause
was the change above, not a separate fault.

`StoryPlayer` decided readiness by counting array entries: `scenes.length < actualTotal`.
That was a valid proxy only while the backend published a scene *after* both its assets
existed. Now all N scenes' text is published within a second or two of the LLM returning,
so `scenes.length === actualTotal` almost immediately, the player concludes there is
nothing left to wait for, and the `N/M ready` chip in the header never renders. At the same
time the generating screen — which owns the dots and "X of Y pages ready" — is now on
screen for ~8s instead of ~34s. The progress feedback moved off screen exactly when it
started being needed.

Fixed by counting **assets, not entries**:

```js
const readyCount = scenes.filter(s => s.image_url && s.audio_url).length
const allScenesReady = scenes.length >= actualTotal && readyCount >= actualTotal
```

Second symptom, same root cause: when playback catches up with generation, `handleEnded`
used to advance on *"does the next scene exist"*. It now exists immediately with null urls,
so the player turned the page to a silent scene with a disabled play button and no message
— indistinguishable from the story having stopped. Advance now waits on the next scene's
`audio_url`, showing "Ollie is still recording the next page…" meanwhile; the watcher
effect moves on the moment narration lands. (Playback never actually deadlocked —
`autoPlayedRef` is false on a new scene, so audio auto-starts when its url arrives — but
there was no feedback during the gap.)

### Do not
- **Do not use `scenes.length` as a readiness signal anywhere in the player.** It now means
  "how many scenes has the LLM written", which is a fixed number reached almost at once.
  Anything the user waits for must be derived from `image_url` / `audio_url`.
- **Do not read `scenes` from the `ended` listener's closure.** That listener is registered
  per scene and fires minutes later, by which time more urls have landed. It reads
  `scenesRef.current`. The old closure is why the pre-2026-07-20 fixed-delay retry got
  stuck re-checking a stale count forever.
- **Do not "fix" the scene dots' `pending` state to mean not-fully-rendered.** `i >=
  scenes.length` there gates *navigation*, and jumping to a still-rendering scene is now
  legitimate — you get the text and a skeleton. It is obsolete, not wrong.

Unchanged and still correct: `storyFullyReady` in `App.jsx` gates Save / Download-offline on
the backend's `completed_scene_count`, which has always meant both assets present.

### Next button gated on narration (frontend `20260727_081207`)

Next is now disabled while the following scene has no `audio_url`, with the tooltip "The
next page is still being recorded" — better than letting a child tap forward onto a silent
page and conclude the app broke.

The gate is **released the moment generation ends**, and that is the whole design:

```js
const generationDone = totalScenes > 0 && completedSceneCount >= totalScenes
const nextNotReady   = !generationDone && !!nextScene && !nextScene.audio_url
```

- **Do not gate this on `allScenesReady` or on `readyCount`.** Both are derived from the
  urls in the payload, so a story that finishes with one scene's audio permanently null
  would leave Next disabled *forever* — a child stranded mid-story with no way out and no
  explanation. `completedSceneCount` is the safe input precisely because `App.jsx` forces it
  to `total_scenes` when polling stops, whatever the story actually ended up containing.
- **Do not extend the same gate to Previous or to the scene dots.** Backwards and jumping
  are always safe; only forward motion can outrun generation.

## Quiz duplicates & image grade calibration (2026-07-27, backend `20260727_094936`)

Two beta reports, two separate root causes, both in `services/story_service.py`.

### Repeated quiz questions

Real example, story `61f8ccd6` (grade 10, "Designing a Balanced Diet"):

```
1  What is the main purpose of a balanced diet?
5  What is the main purpose of a balanced meal plan for a child?
7  What is the main takeaway from our discussion about balanced diets and meal planning?
10 What is the final takeaway from our discussion about balanced diets and meal planning?
```

**They are not byte-identical** — exact-string uniqueness was 10/10 across four sampled
stories. Any dedupe based on string equality finds nothing here.

Cause: the quiz is written in the same pass as the scenes and immediately after them in the
JSON, so the model produces roughly one question per scene. Recap scenes therefore produce
recap questions. Nothing in the prompt required questions to cover *distinct* concepts, and
nothing forbade questions about the narration rather than the document.

Fixes: the prompt now requires one question per distinct concept, bans one-question-per-scene,
and bans meta questions outright. `_drop_near_duplicate_questions()` is a safety net that
removes meta questions by phrase match and restatements by token overlap.

### Do not
- **Do not move the near-duplicate threshold (0.7) without re-measuring these cases.** A
  shared sentence frame is not duplication:

  | overlap | pair | correct action |
  |---|---|---|
  | 0.75 | "…main takeaway from our discussion…" vs "…final takeaway from our discussion…" | drop |
  | 0.60 | "role of **proteins** in a balanced diet" vs "role of **carbohydrates**…" | **keep** |
  | 0.43 | "main purpose of a balanced diet" vs "main purpose of a balanced meal plan for a child" | drop, but unreachable |

  It shipped at 0.6 and the first Groq-only regeneration immediately deleted two perfectly
  good nutrient questions, because "What is the role of X in a balanced diet?" repeats.
  Raised to 0.7 the same day. The 0.43 row is the other end: semantic duplicates below the
  frame-similarity floor cannot be reached by token overlap at any safe threshold, and are
  the prompt's job, not the filter's.
- **Do not let de-duplication fail a generation.** The validator hard-requires 10 questions.
  If the top-up call errors or comes back short, `process_file_to_story` pads the weakest
  questions back in. A repeated question is a blemish; a failed story costs the user a credit.
- **Do not `quiz.extend()` the top-up batch.** It now merges through the same filter and
  truncates — the model regularly returns more than the exact count it was asked for, and the
  extras are the weakest. The top-up path fires precisely when the document was too thin to
  yield 10 questions, which is when restating an earlier question is most tempting.
- The top-up JSON template was also missing `why_correct`, which `_validate_story_json`
  lists as required. Added.

### Grade-10 images looked grade 4-5

`grade_spec["image_style"]` existed and *was* applied — but the story LLM that authors each
`image_prompt` never saw it. The GRADE-LEVEL TARGET block passed vocabulary, sentence style
and quiz cognitive level only, while the output schema hardcoded, for every grade:

```
"image_prompt": "Detailed 3D animated educational scene suitable for visual storytelling"
```

So FLUX received an abstract "editorial style" clause followed by
`MAIN VISUAL: <a 3D animated cartoon scene>`. The concrete, last-positioned instruction won.
A grade-10 story was literally asking for a cartoon.

Fixes: `image_style` is now in the GRADE-LEVEL TARGET block as "Visual register", the schema
line no longer hardcodes a look, and `enhanced_prompt` repeats the style guide *after*
`MAIN VISUAL` so it is the last thing read rather than the first thing forgotten.

- **Do not reintroduce a fixed art-style string in the story prompt.** Style belongs in
  `grade_bands.TIER_SPECS` only. Two prompts describing the look is what caused this, and the
  more specific one always wins.

### Verification: Groq-only regeneration (backend `20260727_100654`)

Text-only reruns of the *same source PDF* that produced the bad quiz, one Groq call per
grade, no images and no TTS. Script: `groq_quiz_test.py` (scratch, not committed) calling
`process_file_to_story()` directly.

| | before (`61f8ccd6`) | grade 10 rerun | grade 5 rerun |
|---|---|---|---|
| meta questions | 3 | 0 | 0 |
| duplicate pairs | 2 | 0 | 0 |
| highest pairwise overlap | 0.75 | 0.33 | 0.45 |
| `why_correct` missing | — | none | none |
| filter had to intervene | — | no | no |

The filter firing **zero** times on both reruns is the result that matters: the prompt is now
doing the work, and the heuristic is idle backup rather than load-bearing.

Visual register differentiates as intended — grade 10: *"…in a mature and editorial style"*;
grade 5: *"A colorful illustration of a plate…"*, *"a child playing football, with a thought
bubble…"*. No "3D animated" in either.

**Still imperfect, honestly:** grade 10 keeps producing some bare-recall questions ("What is
the primary function of proteins in our body?") alongside the good analytical ones ("What is
the most likely reason a child who eats enough food daily still feels weak and gets sick
often?"). The cognitive floor is raised, not enforced. These are also single samples — LLM
output varies run to run. Image *rendering* remains unverified; that needs a real upload.

### TTS: Chatterbox is not deployed and never has been

`docker-compose.yml`: `CHATTERBOX_URL=http://kokoro-tts:8880`. `chatterbox_client.py` is a
legacy name pointing at the local **Kokoro** CPU container — that is the whole explanation for
the old 26.5s scene 0 (same model, CPU instead of GPU). No Resemble-AI Chatterbox model has
ever run in this project. If it is ever adopted, add it as a third `TTS_BACKEND` value with
its own RunPod endpoint and measure GPU-seconds against `RUNPOD_MONTHLY_CAP_AED` first —
Kokoro is 82M params, Chatterbox is 0.5B, and scene 0 latency is what gates the player
opening at ~8s.

## Admin observability suite (2026-08-04, backend `20260804_225548` / frontend `20260804_230141`)

Added to the admin panel: a **System** tab (active config, blue/green deploy status, backup
status, health/concurrency/RunPod cost, rate-limit snapshot), **Feature Flags** (live DB-backed
toggles + the site-wide announcement banner manager), **Content Review** (failed generations with
a human-readable reason), and **Audit Log** (who did what admin action, when). Also a public,
no-auth `AnnouncementBanner` mounted above every route in `App.jsx`.

**Deliberately excluded: no button anywhere triggers a deploy, rollback, or blue/green
switchover.** An admin JWT compromise must stay a data-access problem, not become host-level
RCE via a Docker socket. Those actions remain SSH + `./deploy.sh` only.

- **Deploy/backup status is file-based, not Docker-based.** `deploy.sh` and `scripts/backup.sh`
  write `status/deploy_status.json` / `status/backup_status.json` in the project root after
  every run; that directory is bind-mounted **read-only** (`./status:/app/status:ro`) into both
  backend colors. The backend has no Docker socket and must never get one - this is how it
  learns deploy/backup state without being able to trigger either. Verified the mount is
  genuinely read-only from inside the container (`touch` inside `/app/status` → EROFS).
- **Feature flags** live in a new `app_config` MySQL table (same "DB not code" pattern as
  `subscription_plans`) - `services/app_config.py` has a 30s-TTL cached `get_flag()` reader so a
  hot request path checking a flag doesn't add a DB round trip. Two flags shipped:
  `image_generation_enabled` (checked in `StoryService.generate_image()` - skips image gen,
  story proceeds text-only, same contract as every other "return None" failure path there) and
  `groq_fallback_enabled` (checked only on the gemini-primary → groq-fallback path in
  `_call_story_model`, not on the `LLM_BACKEND=groq` primary path - those are different things).
- **Audit log** (`admin_audit_log` table, `services/audit_log.py`) is explicit `record()` calls
  at 9 existing write sites in `admin.py` (grant-credits, user update/delete, story
  delete/cancel/retry, plan update, promo code create/update) plus the new config-flag and
  announcement routes - not a decorator/middleware wrapper, so each site stays independently
  reviewable on a router that already handles payments and credits. A logging failure never
  fails the action it's describing (swallows and logs its own exception).
- **`app_config.set_flag()` and the announcement update route use SELECT-then-UPDATE, not
  `cursor.rowcount == 0`, for existence checks** - MySQL reports rows *changed*, not rows
  *matched*, so setting a flag to the value it already has would otherwise look identical to
  "no such key" (same class of bug as the `complete-quiz` 404 fixed in the earlier hardening
  pass - see [[feedback-mysql-rowcount-is-changed-rows]] if reading from memory).
- New routers: `system.py`, `config_flags.py`, `announcements.py` (kept out of the already-large
  `admin.py`, matching how `billing.py`/`upload.py` are separate). `runpod_usage.py` is a
  read-only `snapshot()` mirroring `vision_budget.snapshot()`'s shape exactly, reading the same
  `db_data/runpod_usage.json` the existing spend-guard writes - does not touch that write path.
- Verified end-to-end for real, not just "should work": real `./deploy.sh backend`/`frontend`
  runs produced and merged `deploy_status.json` correctly; a real (sudo-triggered, non-cron)
  `backup.sh` run produced a matching `backup_status.json` including a genuine R2 upload; every
  new route curled with and without a real admin JWT (401 vs 200); a config-flag flip and an
  announcement create/deactivate both round-tripped through the public endpoint; a real
  grant-credits and a real promo-code create/update both landed correctly in `admin_audit_log`;
  a full authenticated headless-Chromium click-through of all four new tabs produced zero JS
  errors (the only console errors present are pre-existing WebGL/Three.js failures from this
  GPU-less VPS, unrelated to this work).

## Public share links (2026-08-05, backend `20260805_040727` / frontend `20260805_040255`)

**Why:** a story could only ever be opened by a signed-in user, so there was no way to show one
to a prospective customer. `/s/<token>` is a revocable public URL that plays a saved story with
no account — the demo asset for outreach, and a real feature for teachers sharing with parents.

**`is_public` is deliberately NOT reused.** It means "other *signed-in* users may find this"
(discoverability, used by duplicate detection); every read route behind it still requires a JWT.
A share token is a stronger, separate consent — "anyone holding this URL may read this" — so it
is its own nullable, unique `user_stories.share_token` column, revocable independently. Reusing
the flag would have retroactively published every story whose owner only agreed to the first thing.

- `backend/routers/share.py` — owner routes (`GET`/`POST`/`DELETE /api/story/{id}/share`, JWT) and
  public routes (`GET /api/share/{token}`, `GET /api/share/{token}/media/{filename}`, **no auth**).
  Token is `secrets.token_urlsafe(32)` via the existing `generate_secure_token()`. Issuing is
  idempotent so pressing Share twice doesn't orphan a link already pasted into a message;
  `{"rotate": true}` is the leaked-link escape.
- `backend/services/story_media.py` (new) — the five-pattern scene-filename lookup and the CORS
  header block, lifted out of `main.py`'s `/api/saved-stories/...` handler (where the CORS block
  was copy-pasted once per branch). Both the authenticated and the public media routes now share
  it, so a story that plays for its owner also plays for a visitor.
- Frontend: `/s/:token` route sits **above** the `/*` catch-all and outside `MainApp`'s auth gate;
  `SharedStory.jsx` publishes its bar height as `--app-header-h`, which `StoryPlayer` already
  subtracts from `100dvh` (measured 56 + 788 = 844 on a 390x844 viewport, no overflow).
  `StoryPlayer` gained a `shareMode` prop that drops the Home/Share buttons and passes
  `storyId={null}` to `Quiz` — Quiz uses that id to POST completion, which an anonymous visitor
  cannot do.

**Three real bugs this shook out, all verified fixed in production:**

1. **Every 404 on this site was reaching the browser as a 502.** `/www/server/nginx/conf/proxy.conf`
   had a **global** `proxy_next_upstream ... http_404`, telling nginx to treat a 404 as a *failed
   upstream* and retry the next pool member. Under blue/green only one member is ever up, so every
   legitimate 404 exhausted the pool and became a 502 — confirmed on `/api/load-story/<missing>`,
   which long predates this work. Backend said 404, container nginx said 404, host nginx said 502.
   Fixed by removing `http_404` from that line (backup alongside the original; `nginx -t` +
   reload; all other sites on the box spot-checked healthy afterwards). **This is an aaPanel-shared
   file — re-check it after any panel upgrade.**
2. **A share link leaked the source document.** The media route served anything in the story
   folder, including `metadata.json` (which records the uploaded lesson's path, grade level and
   internal ids) and the raw extracted `test_story.txt`. Now an allow-list regex: scene assets only
   (`scene_N.ext` / `<uuid>_scene_N.ext`), everything else 404s.
3. **Revoking a link did not revoke access.** Cloudflare cached shared scene images under its
   default extension rules (`cf-cache-status: HIT`, `age: 778`, 4h TTL) and kept serving them after
   revocation while the origin correctly returned 404. `media_response()` now sends
   `Cache-Control: private, max-age=300` — `private` keeps revocable content out of shared caches
   while the visitor's own browser still caches it for a sitting. Verified: `HIT` → `BYPASS`, and
   media 404s immediately after revoke.

Also fixed: `quiz` is persisted as a JSON *string* about as often as a list (the authenticated path
papers over this in `App.jsx`), so the share payload normalises it server-side — otherwise the
shared page rendered a quiz by iterating over the characters of a string.

**Verified:** anonymous fetch returns the story with zero owner PII (allow-list payload, not
key-deletion); bogus/revoked tokens 404; `/api/load-story` and `/api/saved-stories` still 401
without a JWT; headless anonymous Chromium at 390x844 renders story, image, controls and quiz
button with no JS errors, no failed requests and no horizontal scroll; the revoked-link state
renders "Story unavailable" with a single CTA rather than a raw error.

## 3D scene plane: stop cropping the picture (2026-08-05, frontend `20260805_095139`)

**Symptom:** the player cut the edges off every scene image and showed a smeared duplicate around
the borders, with the 3D effect on. Applies to BOTH the in-app player and the public share page —
they render the same `StoryPlayer`, so this was never two bugs.

**Root cause, in `frontend/src/components/3d/StorySceneImagePlane.jsx`:**

`CameraRig` breathes camera distance (30 ±1.5) and fov (60 ±2.2°) every frame, but the plane was
sized from `useThree().viewport`, which is a snapshot recomputed only on **resize**. So the plane's
size was wrong for most of the breathing cycle. The previous fix for that was a blanket `1.2×`
oversize on a **cover-fit** plane — guaranteeing coverage by throwing away **17% of the picture's
width and 37% of its height**. On a square scene image in a 4:3 frame that means the subject's head.

**Fix:** measure the live camera each frame (`frustumHalfExtents`, using `camera.position.length()`
because CameraRig also offsets x/y) and **contain-fit** against it. With the real frame known there
is nothing to guess, so no oversize factor is needed and nothing is cropped. `FG_MARGIN = 0.9` is
tilt headroom, not a crop guard: a plane rotated by `FG_TILT` swings its near edge toward the
camera, where the frustum is narrower, and would be clipped by the canvas at exactly 100%.

Two artifacts that only became visible once the foreground stopped covering the whole frame, both
found by screenshotting the deployed page rather than by reasoning:

- The blurred backdrop was sized from the **foreground** (`BG_SCALE * fgW`). Contain-fit makes the
  foreground smaller than the frame, so the backdrop stopped short and put its own hard border on
  screen. It is now cover-fit against the **frame**, and it no longer inherits the page-turn
  shift/yaw (which slid it far enough to uncover a corner mid-turn).
- `BACKDROP_OVERSCAN = 3.6` is not slack. At low magnification the margin reads as a legible second
  copy of the picture — the dragon's tail appearing again beside the dragon, i.e. the same
  "duplicated border" complaint in another form. There is no blur pass here; **magnification is the
  blur**.
- The contact glow is an untextured additive plane, so every edge of it is a hard seam — it only
  looks like a glow because the picture covers three of them. Any overhang past `fgW` ran its top
  edge out into the side margin as a bright horizontal line.

**Follow-up (same day, frontend `20260805_100923`) — the frame was the wrong shape all along.**
Contain-fit in a 4:3 box left a square picture pillarboxed and visibly small. Every generator path
writes **512×512** (verified on disk across stories), so the box is now square:
`width: min(100%, 52dvh, 520px)` + `aspect-ratio: 1/1`. Sizing by `width: min()` rather than by
`max-height` is the point — the old rule declared `aspect-ratio: 4/3` **and** a `max-height`, and on
a phone max-height won, so the box was never even the ratio it claimed to be. Measured after:
338×338 at 390×844, 468×468 at 1440×900, nothing pushed below the fold in either.

The fixed 0.9 inset went with it. It was tilt headroom for a worst case that is rarely happening,
paid for at every resting frame; `tiltFitScale()` now solves for the *current* tilt, so the picture
is full-bleed at rest and gives back a few percent only while actually moving. `breathe` had to be
inverted to pulse **down** from that fit — anything above 1.0 now clips.

**Do not** reintroduce cover-fit to "avoid letterboxing", and do not put a `max-height` back on
`.scene-image-container` — it silently re-widens the box and the crop comes back with it.

## "Audio unavailable" on every share-link open (2026-08-05, frontend `20260805_100923`)

Blocked autoplay is not a broken file. Mobile Chrome rejects `play()` with **NotAllowedError** until
the page has had a user gesture — and a share link is the one entry point with no gesture before the
player, because the visitor arrives straight on it instead of tapping through the app. All three
`play().catch()` sites set `audioError` unconditionally, so every prospective customer opened the
demo to a red "Audio unavailable".

It never showed in-app, which is why it survived: you always tap something before reaching the
player there. It is also why the earlier headless audio probe cleared it as an artifact — that probe
ran with `--autoplay-policy=no-user-gesture-required`, which is exactly the condition that does not
hold for a real visitor. **A flag that suppresses the failure mode cannot be used to rule it out.**

`handlePlayRejection` now separates the two: `NotAllowedError` → a neutral "Tap play to start the
story" hint that times out after 4.5s (it shares the error banner's fixed slot, which sits over the
header); anything else → the real error. The `<audio>` element's own `error` event still sets
`audioError` directly, which is the genuine load-failure path and is untouched.

**Verified:** headless Chromium at 390×844 with WebGL forced on
(`--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader` — plain `--disable-gpu` falls
back to the flat `<img>` path and silently proves nothing about the 3D layer). Banner on load reads
`player-error player-hint :: Tap play to start the story`, and is `null` when re-probed at 20s, so
the hint self-dismisses. No JS errors.

## Scene plane: full-bleed picture and clean edges (2026-08-05, frontend `20260805_121351`)

Third and final round on the same frame. Two independent defects, both reported off a phone
screenshot: the picture still sat visibly inset in its frame, and its borders went wavy in motion.

**The inset was a geometry impossibility, not a tuning miss.** `tiltFitScale()` was solving for
"no pixel of the tilted plane ever leaves the frustum". A tilting rectangle can never fit exactly
inside a same-shaped window, so that constraint always returns < 1 — and the previous note's claim
that it is "full-bleed at rest" was only ever true on desktop. On mobile there is no pointer, so
`useTiltRef` drives a **permanent auto-drift** (`sin(t*0.35)*0.6`, `cos(t*0.27)*0.4`) that never
returns to zero. Replayed over a full drift cycle, the fit bottomed out at **0.9207** and averaged
well under 1 — an ~8% margin for the picture's entire life, matching the ~0.906 measured off the
user's screenshot.

Two changes make the fit sit at exactly 1.0:
- `TILT_BLEED = 0.06` — the tilted near edge may overhang the frame by 6% before the picture is
  scaled down. `Math.min(1, …)` still caps it, so bleed buys the right to *fill* the frame, never to
  grow past it. Worst case ~5% of **one** edge is clipped, at the extreme of a 20-second sweep.
- **fov 60 → 38, camera z 30 → 48** (`CameraRig.jsx`). fov 60 is a wide-angle lens: a frame-filling
  plane keystones violently the instant it tilts, its near edge ballooning outward. That error is
  what forced the shrink in the first place, *and* it is the "distortion at the borders" complaint.
  Distance and fov move together (`halfExtent = dist * tan(fov/2)`), so the framing is unchanged —
  only the wide-angle stretch is. This cut the required bleed from ~9% to ~5%.

**Every amplitude scaled by 48/30 when the camera moved back.** CameraRig's x/y/z/fov swings and
`StorySceneImagePlane`'s depth constants (`BG_Z`, `FLY_IN_Z`, `GLOW_Z_OFFSET`) are absolute world
units tuned at distance 30; left alone they shrink to a third of their former effect at 48 and the
rig looks frozen and flat. If the camera distance is ever changed again, scale all of them.

**The wavy borders were `antialias: !isMobile` + `dpr: isMobile ? 1 : [1,2]`.** A blanket perf
reflex. This canvas is one slowly rotating textured quad whose four edges are hard geometry: with
MSAA off at dpr 1, those edges stair-step, and because the plane is always moving the steps *crawl*
along the edge every frame. The canvas is ~340 CSS px square — under half a megapixel at dpr 2, on
three quads. Both are now unconditional, with dpr capped at `[1, 2]` so a 3×-DPR screen doesn't pay
for resolution past the point the edge already reads clean. The `isMobile` state and its resize
listener existed only to feed those two props and were removed with them.

**Regression loop:** `fit_loop.py` in the session scratchpad parses the constants straight out of
both source files and replays the sizing math across a full drift cycle. Red before (`min fill
0.9207`), green after (`min fill 1.0000`, `max bleed 0.0508`). Reach for it before touching
`FG_TILT`, the drift amplitudes, the fov, or `TILT_BLEED` — all four are coupled.

**Verified in production** (`fill_probe.py`, same scratchpad — measures the picture's real edges
inside the container by luminance step, from screenshot pixels):

| | container | drawing buffer | fill w × h |
|---|---|---|---|
| 390×844 | 338×338 | **671×671** (dpr 2) | 0.990 × 0.991 |
| 1440×900 | 468×468 | **932×932** (dpr 2) | 0.990 × 0.996 |

The buffer being 2× the CSS box is the antialias/dpr fix showing up as a number. Sampling mid-fly-in
reads lower (~0.84) — that is the entry transition from `FLY_IN_Z`, not the resting state.

**Do not** set `TILT_BLEED` back to 0 to "stop the clipping" — that is the same trade the 0.9 inset
and the tilt-exact fit both lost. **Do not** re-disable antialias or drop dpr on mobile for
"performance"; measure this canvas first.

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

