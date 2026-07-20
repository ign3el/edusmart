# PROJECT.md — EduSmart

## Purpose
AI-powered educational storybook platform. A user (teacher/parent/student) uploads a lesson document (PDF/DOCX), picks a grade level, and the backend turns it into an illustrated, narrated interactive story with a 10+ question quiz. Stories can be played immediately, saved to an account, downloaded as an offline ZIP, or synced for offline playback via PWA.

## Stack
| Layer | Tech |
|---|---|
| Frontend | React 18 + Vite, react-router-dom v7, framer-motion, react-three-fiber/drei (background only), axios |
| Backend | Python FastAPI (single `uvicorn main:app`, no multi-worker) |
| Story generation | Groq (`llama-3.3-70b-versatile`) — **not** Gemini, despite `google-genai` being installed and referenced in some dead code paths |
| Images | RunPod ComfyUI (FLUX.1-dev), with a monthly AED spend cap |
| TTS (English) | Self-hosted Kokoro-82M (`kokoro-tts:8880` container), reached via two parallel client wrappers |
| TTS (Arabic) | A hosted Piper-compatible endpoint (`TTS_API_URL`) — **only reachable from the admin TTS-test tool and the voice-preview endpoint, not from the actual story-generation pipeline** (see Known Issues) |
| Main DB | MySQL — users, saved stories |
| Job-tracking DB | SQLite (`db_data/job_state.db`, Docker named volume) — in-progress/generated story state |
| Mobile | PWA (service worker, install prompt, offline story storage via IndexedDB/localStorage) |

## Deploy
Docker Compose (`docker-compose.yml` at repo root, one file for both `backend` and `frontend` services), aaPanel nginx reverse proxy in front.

## Domain & Port
- Domain: edusmart.ign3el.com (Cloudflare proxy)
- Containers: `edusmart-backend` (port 8000), `edusmart-frontend` (port 3004→80)
- Backend runs as **non-root** (`user: "1001:1001"`, matching the host `ubuntu` UID) as of 2026-07-19

## Quick Commands
| Action | Command |
|---|---|
| Deploy backend (after any code change) | `cd backend && docker compose up -d --build` |
| Deploy frontend | `cd frontend && docker compose -f ../docker-compose.yml up -d --build --no-deps frontend` |
| Logs | `docker logs edusmart-backend --tail 50` |
| Job-state DB (live) | `docker exec edusmart-backend sqlite3 /app/db_data/job_state.db` |

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
- `job_state.py` (`job_manager`, singleton) — SQLite wrapper for `db_data/job_state.db` (`stories`, `scenes` tables). This is the single live copy; **do not** create another one at a different path (see Known Issues history below).
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

Both now fire once shortly after startup rather than only after a full interval of continuous uptime.

---

## Security posture (as of 2026-07-19)
A full hostile-QA pass was done and fixes applied/verified live. Summary — see git history / `.bak` files for the pre-fix state if needed:
- **Fixed**: unauthenticated path traversal + full `.env`/source disclosure via `/api/saved-stories/*` and `/api/generated-stories/*` (critical, was live in production).
- **Fixed**: all story media (`saved-stories`, `generated-stories`, `outputs` audio-cache/status) now requires auth + per-story ownership (or admin), via a shared `_verify_story_access()` check in `main.py`. Media URLs authenticate via `?token=` query param since `<img>`/`<audio>` tags can't send headers.
- **Fixed**: `/api/uploads` (dead) and a shadow `StaticFiles` mount on `/api/generated-stories` (which had been silently bypassing the custom auth'd route) both removed.
- **Fixed**: admin `password_hash` leak via `SELECT *`, unauthenticated cost-abuse endpoints (`extract-text`, `tts-preview` — now require auth + size/length caps), RunPod spend-cap race condition, dev auth-bypass removed, `JWT_SECRET` fail-loud, last-admin delete/demote protection, container no longer runs as root.
- **Still open / deliberately not auto-fixed**: no payment/subscription/entitlement layer exists at all (`is_premium` field on `User` is unused) — required before monetization. `/gitignore` line 71 (`admin_token.txt`) has a pre-existing typo (spaces between every character) so it isn't actually ignored.

---

## Notes
- API keys/secrets live in root `.env` (docker-compose reads from here, not `backend/.env`).
- Backend files owned by host `ubuntu` user (UID 1001); container now runs as that same UID.
- `backend/routers/` contains stray misplaced files unrelated to routing (`VoiceSelector.jsx`/`.css`, a per-directory `Dockerfile`/`requirements.txt`) — leftover from an earlier project structure, harmless but confusing if browsing that folder.
- See also: `edusmart/PROJECT_CONTEXT.md` (older detailed reference, may be stale relative to this file).
