# EduSmart Webapp — Full Fix & Overhaul Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Fix all 51 identified issues across backend and frontend, then overhaul the 3D/animation experience for a production-grade mobile-first educational platform.

**Architecture:** Three phases — (1) critical security/bug fixes, (2) performance & UX hardening, (3) 3D/animation overhaul using visual-layer-composition patterns. Each phase is independently deployable.

**Tech Stack:** Python/FastAPI, React 18 + Vite, Three.js/R3F, Framer Motion, MySQL

---

## PHASE 1: Critical Security & Bug Fixes 🔴

> These are blocking issues — broken builds, exposed secrets, auth bypasses. Deploy immediately after fixing.

---

### Task 1.1: Extract Hardcoded TTS API Key to Environment

**Objective:** Remove `TTS_API_KEY = "TTS_AHTE_2026!"` from 3 source files and load from `.env`

**Files:**
- Modify: `backend/routers/upload.py` (lines 20-21)
- Modify: `backend/services/kokoro_client.py` (lines 23-24)
- Modify: `backend/routers/admin.py` (lines 55-56)
- Modify: `backend/.env` (add TTS_API_KEY, TTS_API_URL)
- Modify: `backend/config.py` (add TTS settings)

**Steps:**

1. Add to `backend/.env`:
```
TTS_API_KEY=TTS_AHTE_2026!
TTS_API_URL=https://tts.ign3el.com/v1/audio/speech
```

2. Add to `backend/config.py` inside class Config:
```python
# TTS Configuration
TTS_API_KEY = os.getenv("TTS_API_KEY", "")
TTS_API_URL = os.getenv("TTS_API_URL", "")
```

3. In `backend/routers/upload.py`, replace lines 20-21:
```python
# BEFORE (REMOVE):
TTS_API_KEY = "TTS_AHTE_2026!"
TTS_API_URL = "https://tts.ign3el.com/v1/audio/speech"

# AFTER:
from config import Config
TTS_API_KEY = Config.TTS_API_KEY
TTS_API_URL = Config.TTS_API_URL
```

4. In `backend/services/kokoro_client.py`, replace lines 23-24:
```python
# BEFORE (REMOVE):
endpoint = "https://tts.ign3el.com/v1/audio/speech"
api_key = "TTS_AHTE_2026!"

# AFTER:
from config import Config
endpoint = Config.TTS_API_URL
api_key = Config.TTS_API_KEY
```

5. In `backend/routers/admin.py`, replace lines 55-56 (same pattern):
```python
from config import Config
# Use Config.TTS_API_KEY and Config.TTS_API_URL
```

6. Add `TTS_API_KEY` and `TTS_API_URL` to `docker-compose.yml` environment section:
```yaml
- TTS_API_KEY=${TTS_API_KEY:-}
- TTS_API_URL=${TTS_API_URL:-}
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/backend
grep -rn "TTS_AHTE_2026" .  # Should return 0 results
python -c "from config import Config; print(bool(Config.TTS_API_KEY))"  # Should print True
```

**Commit:** `fix: extract hardcoded TTS API keys to environment config`

---

### Task 1.2: Remove Dev Auth Bypass in Production

**Objective:** Ensure `ENV=development` auth bypass cannot accidentally activate in production

**Files:**
- Modify: `backend/routers/auth.py` (lines 18-33, 88-95)

**Steps:**

1. In `backend/routers/auth.py`, add a safety check. The DEV_USER block should ONLY work when both `ENV=development` AND a secondary flag is set:

```python
# BEFORE:
DEV_USER = {"id": 999, "username": "dev_user", "email": "dev@test.com", "is_admin": True, "is_verified": True}

# AFTER:
import os
import secrets

_dev_bypass_secret = os.getenv("DEV_BYPASS_SECRET", "")
DEV_USER = {"id": 999, "username": "dev_user", "email": "dev@test.com", "is_admin": True, "is_verified": True}

def _is_dev_mode():
    """Only allow dev bypass if ENV=development AND DEV_BYPASS_SECRET is set."""
    return (
        os.getenv("ENV") == "development"
        and bool(_dev_bypass_secret)
    )
```

2. Replace all `if os.getenv("ENV") == "development"` checks in auth functions with `if _is_dev_mode()`.

3. Add `DEV_BYPASS_SECRET=changeme_only_for_dev` to `.env.example` (never commit real value).

**Verification:**
```bash
cd /www/wwwroot/edusmart/backend
grep -n "ENV.*development" routers/auth.py  # Should show _is_dev_mode() calls only
```

**Commit:** `fix: add dual-flag guard for dev auth bypass`

---

### Task 1.3: Fix `bounded_generate` Return Value Structure

**Objective:** Fix the image generation tuple-return bug that breaks downstream image saving

**Files:**
- Modify: `backend/services/story_service.py` (lines 747-784)

**Steps:**

1. In `story_service.py`, find `generate_images_parallel` → `bounded_generate`. The function returns `(scene_num, img_bytes)` tuple but callers expect raw bytes.

2. Fix the return and collection:
```python
# INSIDE bounded_generate:
async with semaphore:
    img_bytes = await generate_one_image(scene)
    return scene_num, img_bytes  # Return tuple

# FIX the collection loop (around line 780-784):
results = await asyncio.gather(*tasks)
images = {}
for result in results:
    if result is not None:
        scene_num, img_bytes = result  # Unpack tuple
        images[scene_num] = img_bytes  # Store as Dict[int, bytes]
```

3. Remove the dead second `async with semaphore:` block (lines ~761-767) that never executes.

**Verification:**
```bash
cd /www/wwwroot/edusmart/backend
python -c "
from services.story_service import StoryService
import inspect
src = inspect.getsource(StoryService.generate_images_parallel)
assert 'scene_num, img_bytes = result' in src or 'images[scene_num] = img_bytes' in src
print('PASS: Return structure fixed')
"
```

**Commit:** `fix: correct bounded_generate return value structure for image saving`

---

### Task 1.4: Fix `_generate_priority_scene` Function Name Error

**Objective:** Fix NameError where function is called but doesn't exist

**Files:**
- Modify: `backend/services/story_service.py` (line 824)

**Steps:**

1. Find line 824 where `self._call_with_exponential_backoff(_generate_priority_scene)` is called.

2. Check what function actually exists. The function defined above is `_generate_scene` (line 816). Either:
   - Rename the call to `_generate_scene`, OR
   - Rename the function definition to `_generate_priority_scene` and update all references.

3. Most likely fix — the function `_generate_priority_scene` should exist but is missing. Add it:
```python
async def _generate_priority_scene(self, scene, ...):
    """Generate image for priority scene (first scene shown immediately)."""
    return await self._generate_scene(scene, ...)
```

Or if `_generate_scene` already handles it, just fix the call:
```python
# BEFORE (line 824):
self._call_with_exponential_backoff(_generate_priority_scene)

# AFTER:
self._call_with_exponential_backoff(_generate_scene)
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/backend
grep -n "_generate_priority_scene" services/story_service.py  # Should be defined or removed
python -c "import services.story_service"  # Should import without NameError
```

**Commit:** `fix: resolve _generate_priority_scene NameError`

---

### Task 1.5: Remove Duplicate Endpoint Registrations

**Objective:** Remove the duplicate `/api/story/{story_id}/tts-status` and `/api/story/{story_id}/scene/{scene_num}/audio` endpoint registrations

**Files:**
- Modify: `backend/main.py` (lines 1641-1820)

**Steps:**

1. Read lines 1641-1761 and 1757-1820. Identify the two pairs of duplicate endpoints.

2. Keep the FIRST registration of each (lines ~1641-1703 for tts-status, lines ~1647-1703 for audio).

3. DELETE the second registration entirely (lines ~1757-1820). Also remove the redundant `from fastapi import HTTPException` at line 1820.

4. Verify the remaining endpoints have proper auth:
```python
@app.get("/api/story/{story_id}/tts-status")
async def get_tts_status(story_id: str, current_user = Depends(get_current_user)):
    # ... (add Depends if missing)
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/backend
grep -c "tts-status" main.py  # Should be 1 (definition only, not 2)
grep -c "scene.*audio" main.py  # Should be 1
```

**Commit:** `fix: remove duplicate endpoint registrations in main.py`

---

### Task 1.6: Add Authentication to Unprotected Endpoints

**Objective:** Protect 4 endpoints that currently allow unauthenticated access

**Files:**
- Modify: `backend/main.py`

**Steps:**

1. Add `current_user = Depends(get_current_user)` parameter to:
   - `get_scene_audio` (~line 1647)
   - `get_story_status` (~line 992)
   - `get_status` (~line 1274)
   - `export_job` (~line 1525)

2. For each, add the dependency:
```python
# BEFORE:
async def get_scene_audio(story_id: str, scene_num: int):

# AFTER:
async def get_scene_audio(story_id: str, scene_num: int, current_user = Depends(get_current_user)):
```

3. Verify `get_current_user` is imported at top of main.py (it is, from `routers.auth`).

**Verification:**
```bash
cd /www/wwwroot/edusmart/backend
# These endpoints should now return 401 without a token:
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/story/test/status
# Expected: 401
```

**Commit:** `fix: add auth dependency to unprotected API endpoints`

---

### Task 1.7: Sanitize File Paths and Headers

**Objective:** Prevent path traversal via filenames and header injection via special characters

**Files:**
- Modify: `backend/main.py` (lines 858-861, 1580, 1635)

**Steps:**

1. At line ~858, sanitize the uploaded filename:
```python
# BEFORE:
temp_file_path = os.path.join(temp_dir, filename)

# AFTER:
import re
safe_filename = re.sub(r'[^\w\-.]', '_', filename)  # Strip dangerous chars
safe_filename = safe_filename.lstrip('.')  # Prevent hidden files
temp_file_path = os.path.join(temp_dir, safe_filename)
```

2. At lines ~1580 and ~1635, sanitize the Content-Disposition header:
```python
# BEFORE:
safe_title = status['title'].replace(' ', '_')

# AFTER:
safe_title = re.sub(r'[^\w\-]', '_', status.get('title', 'story')).strip('_')
safe_title = safe_title[:100]  # Limit length
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/backend
grep -n "filename" main.py | grep -i "join"  # Should show sanitized pattern
```

**Commit:** `fix: sanitize file paths and Content-Disposition headers`

---

### Task 1.8: Fix SQL Injection Pattern

**Objective:** Replace string-formatted SQL IN clause with parameterized query

**Files:**
- Modify: `backend/main.py` (lines 703-706)

**Steps:**

1. Find the `WHERE story_id IN ({placeholders})` pattern.

2. Replace with parameterized query:
```python
# BEFORE:
placeholders = ', '.join(['%s'] * len(story_ids))
cursor.execute(f"SELECT * FROM stories WHERE story_id IN ({placeholders})", story_ids)

# AFTER (preferred):
# Use a tuple for the IN clause — MySQL connector handles this:
cursor.execute("SELECT * FROM stories WHERE story_id IN (" + ", ".join(["%s"] * len(story_ids)) + ")", tuple(story_ids))
```

Note: The key fix is wrapping `story_ids` in `tuple()` — some MySQL connectors don't accept lists.

**Verification:**
```bash
cd /www/wwwroot/edusmart/backend
grep -n "IN (" main.py  # Should show parameterized pattern
```

**Commit:** `fix: use parameterized queries for IN clause`

---

## PHASE 2: Performance & UX Hardening 🟡

> Mobile-first performance, accessibility, and cleanup. Deploy after Phase 1.

---

### Task 2.1: Add Top-Level ErrorBoundary to App

**Objective:** Prevent blank white screen on any unhandled render error

**Files:**
- Modify: `frontend/src/App.jsx`

**Steps:**

1. Import ErrorBoundary at top of App.jsx:
```jsx
import ErrorBoundary from './components/ErrorBoundary'
```

2. Wrap the main content in ErrorBoundary:
```jsx
// In the MainApp return, wrap the outer div:
<ErrorBoundary>
  <div className="app">
    {/* ... existing content ... */}
  </div>
</ErrorBoundary>
```

3. Also wrap 3D components specifically in App.jsx:
```jsx
{step !== 'playing' && (
  <ErrorBoundary fallback={<div className="bg-fallback" />}>
    <Scene3DBackground className="global-3d-background" />
  </ErrorBoundary>
)}
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
npm run build  # Should succeed
# Manually test: add `throw new Error('test')` to a component — should show error boundary, not blank screen
```

**Commit:** `fix: wrap app and 3D components in ErrorBoundary`

---

### Task 2.2: Remove useState in useFrame Loops (Performance Fix)

**Objective:** Stop React from re-rendering 60x/second for unused values

**Files:**
- Modify: `frontend/src/components/Particles3D.jsx`
- Modify: `frontend/src/components/3d/Scene3DBackground.jsx` (internal Particles)

**Steps:**

1. In `Particles3D.jsx`, remove the dead offset state entirely:
```jsx
// BEFORE:
const [offset, setOffset] = useState(0)
useFrame(() => setOffset(o => o + speed * 0.01))

// AFTER:
// Delete both lines — offset is never used in render
```

2. Remove `useState` from the import if no longer used:
```jsx
import { useMemo } from 'react'  // Remove useState
```

3. In `Scene3DBackground.jsx`, find the internal `Particles` function and do the same fix — remove the `offset` state + `useFrame` state setter.

4. In `AuroraBackground.jsx` (if `AuroraPlane` has the same pattern), remove `useFrame(() => setPhase(...))` — `phase` is computed but never used in render.

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
grep -rn "setOffset" src/components/  # Should return 0 results
grep -rn "setPhase" src/components/    # Should return 0 results
npm run build  # Should succeed
```

**Commit:** `perf: remove unused useState in useFrame loops for 60fps perf gain`

---

### Task 2.3: Fix FloatingShapes Rotation Animation

**Objective:** Make floating geometric shapes actually rotate using useFrame

**Files:**
- Modify: `frontend/src/components/3d/Scene3DBackground.jsx` (FloatingShapes function)

**Steps:**

1. Extract the shape mesh into its own component with `useFrame`:
```jsx
function FloatingShape({ position, rotSpeed, scale, type, color }) {
  const meshRef = useRef()
  
  useFrame((state) => {
    if (meshRef.current) {
      meshRef.current.rotation.y = state.clock.elapsedTime * rotSpeed
      meshRef.current.rotation.x = Math.sin(state.clock.elapsedTime * rotSpeed * 0.5) * 0.3
    }
  })

  return (
    <mesh ref={meshRef} scale={scale}>
      {type === 0 && <boxGeometry args={[1, 1, 1]} />}
      {type === 1 && <sphereGeometry args={[0.8, 16, 16]} />}
      {type === 2 && <torusGeometry args={[0.6, 0.2, 16, 32]} />}
      <meshPhysicalMaterial color={color} transparent opacity={0.15} transmission={0.3} roughness={0.1} metalness={0.2} clearcoat={1} />
    </mesh>
  )
}
```

2. Update `FloatingShapes` to use it:
```jsx
{shapes.map(({ id, x, y, z, rotSpeed, scale, type }) => (
  <Float key={id} rotationIntensity={0.5} floatIntensity={2} position={[x, y, z]}>
    <FloatingShape
      rotSpeed={rotSpeed}
      scale={scale}
      type={type}
      color={['#6366f1', '#06b6d4', '#10b981'][id % 3]}
    />
  </Float>
))}
```

3. Add `useRef` to imports: `import { useRef, useMemo, useState, useEffect } from 'react'`

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
npm run build  # Should succeed
# Visual check: shapes should slowly rotate on the page
```

**Commit:** `fix: animate FloatingShapes rotation via useFrame`

---

### Task 2.4: Fix ProgressiveStoryPlayer Auth & API Pattern

**Objective:** Replace raw `fetch()` with `apiClient` for proper auth and env handling

**Files:**
- Modify: `frontend/src/components/ProgressiveStoryPlayer.jsx`

**Steps:**

1. Replace the API_URL constant and raw fetch calls:
```jsx
// BEFORE:
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
// ... fetch(`${API_URL}/api/story/${storyId}/status`)

// AFTER:
import apiClient from '../services/api'
// ...
const response = await apiClient.get(`/api/story/${storyId}/status`)
const data = response.data
```

2. Update all fetch calls in the component to use `apiClient.get()` / `apiClient.post()`.

3. Remove the `API_URL` constant entirely.

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
grep -n "localhost:8000" src/components/ProgressiveStoryPlayer.jsx  # Should return 0
grep -n "fetch(" src/components/ProgressiveStoryPlayer.jsx  # Should return 0
npm run build
```

**Commit:** `fix: use apiClient in ProgressiveStoryPlayer for auth and env handling`

---

### Task 2.5: Add ARIA Labels to All Icon-Only Buttons

**Objective:** Make player controls and icon buttons accessible to screen readers

**Files:**
- Modify: `frontend/src/components/StoryPlayer.jsx`
- Modify: `frontend/src/components/ProgressiveStoryPlayer.jsx`

**Steps:**

1. Find all icon-only buttons (FiSkipBack, FiPlay, FiSkipForward, etc.)

2. Add `aria-label` to each:
```jsx
// BEFORE:
<button onClick={onPrev}><FiSkipBack /></button>

// AFTER:
<button onClick={onPrev} aria-label="Previous scene"><FiSkipBack /></button>
```

3. Key buttons to label:
- Previous scene / Next scene
- Play / Pause
- Save story
- Download
- Menu toggle
- All FloatingMenu icon buttons

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
grep -c "aria-label" src/components/StoryPlayer.jsx  # Should be 3+
grep -c "aria-label" src/components/ProgressiveStoryPlayer.jsx  # Should be 3+
```

**Commit:** `a11y: add aria-labels to all icon-only buttons`

---

### Task 2.6: Fix Stuck playingStoryId in StoryList

**Objective:** Prevent Play button from being stuck on "Loading..." forever

**Files:**
- Modify: `frontend/src/components/StoryList.jsx`

**Steps:**

1. Add error handling and reset logic:
```jsx
// In the Play button handler, wrap in try/catch/finally:
const handlePlay = async (storyId) => {
  setPlayingStoryId(storyId)
  try {
    await onPlayStory(storyId)
  } catch (err) {
    console.error('Failed to play story:', err)
    // Reset so button is usable again
  } finally {
    setPlayingStoryId(null)
  }
}
```

2. Also add a timeout reset as safety net:
```jsx
// If navigation succeeds, playingStoryId won't matter because the component unmounts
// But if it fails, the finally block resets it
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
grep -A5 "handlePlay\|onPlayStory" src/components/StoryList.jsx  # Should show try/catch/finally
```

**Commit:** `fix: reset playingStoryId on error to prevent stuck UI`

---

### Task 2.7: Fix AudioContext Memory Leak in TtsLab

**Objective:** Properly close AudioContext on component unmount

**Files:**
- Modify: `frontend/src/components/TtsLab.jsx`

**Steps:**

1. Add cleanup to the component:
```jsx
// Add useEffect cleanup:
useEffect(() => {
  return () => {
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.close()
    }
  }
}, [])
```

2. Ensure AudioContext is created lazily (only on first use):
```jsx
const getAudioContext = () => {
  if (!audioContextRef.current || audioContextRef.current.state === 'closed') {
    audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)()
  }
  return audioContextRef.current
}
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
grep -A3 "return () =>" src/components/TtsLab.jsx  # Should show AudioContext close
```

**Commit:** `fix: close AudioContext on TtsLab unmount to prevent memory leak`

---

### Task 2.8: Remove Dead Code & Unused Dependencies

**Objective:** Clean up dead code, unused imports, and unused npm packages

**Files:**
- Delete: `frontend/src/components/FloatingMenu3D.jsx`
- Modify: `frontend/src/components/Scene3D.jsx` (remove CSS animation reference to `pulse` if not defined)
- Modify: `frontend/package.json` (remove unused deps)

**Steps:**

1. Delete `FloatingMenu3D.jsx`:
```bash
rm frontend/src/components/FloatingMenu3D.jsx
```

2. Check if it's imported anywhere — if so, remove those imports.

3. Remove unused npm packages from `package.json`:
```bash
cd frontend
npm uninstall zustand @react-three/postprocessing @types/react @types/react-dom
```

4. Clean up 15+ `console.log` statements in `App.jsx` — replace with a debug flag:
```jsx
const DEBUG = import.meta.env.DEV
// Then: if (DEBUG) console.log(...)
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
ls src/components/FloatingMenu3D.jsx 2>/dev/null  # Should not exist
grep -c "console.log" src/App.jsx  # Should be 0 or wrapped in DEV check
npm ls zustand 2>/dev/null  # Should show "not found"
npm run build  # Should succeed
```

**Commit:** `chore: remove dead code, unused deps, and production console.logs`

---

### Task 2.9: Lazy Load 3D Background

**Objective:** Don't load heavy Three.js scene on auth pages — defer until needed

**Files:**
- Modify: `frontend/src/App.jsx`

**Steps:**

1. Lazy import the 3D background:
```jsx
const Scene3DBackground = lazy(() => import('./components/3d/Scene3DBackground'))
```

2. Wrap in Suspense:
```jsx
{step !== 'playing' && (
  <Suspense fallback={null}>
    <ErrorBoundary fallback={<div className="bg-fallback" />}>
      <Scene3DBackground className="global-3d-background" />
    </ErrorBoundary>
  </Suspense>
)}
```

3. Remove the eager import at top of file:
```jsx
// REMOVE:
import Scene3DBackground from './components/3d/Scene3DBackground'
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
grep -n "Scene3DBackground" src/App.jsx  # Should show lazy() import
npm run build  # Check bundle splits — Scene3DBackground should be a separate chunk
```

**Commit:** `perf: lazy-load 3D background to reduce initial bundle`

---

### Task 2.10: Fix Upload Progress to Show Real XHR Progress

**Objective:** Replace fake setInterval progress with actual upload progress

**Files:**
- Modify: `frontend/src/App.jsx` (handleFileUpload function)

**Steps:**

1. Replace the simulated progress with XMLHttpRequest for real progress:
```jsx
const handleFileUpload = async (file) => {
  setUploadedFile(file)
  setUploadFileName(file.name)
  setShowUploadProgress(true)
  setUploadProgress(0)

  // Check for duplicates first (keep existing logic)
  try {
    const formData = new FormData()
    formData.append('file', file)
    const response = await apiClient.post('/api/check-duplicate', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    if (response.data.is_duplicate) {
      setShowUploadProgress(false)
      setDuplicateInfo(response.data)
      setShowDuplicateModal(true)
      return
    }
    setFileHash(response.data.file_hash)
  } catch (err) {
    setShowUploadProgress(false)
    setError('Failed to check for duplicates: ' + err.message)
    return
  }

  // Real upload progress via XMLHttpRequest
  const uploadFormData = new FormData()
  uploadFormData.append('file', file)
  uploadFormData.append('grade_level', gradeLevel)
  uploadFormData.append('voice', voice)
  uploadFormData.append('speed', speed)
  if (fileHash) uploadFormData.append('file_hash', fileHash)
  uploadFormData.append('force_new', 'false')
  uploadFormData.append('user_agent', navigator.userAgent)

  const xhr = new XMLHttpRequest()
  xhr.upload.addEventListener('progress', (e) => {
    if (e.lengthComputable) {
      setUploadProgress(Math.round((e.loaded / e.total) * 90))
    }
  })

  xhr.onload = () => {
    setUploadProgress(100)
    setTimeout(() => {
      setShowUploadProgress(false)
      if (xhr.status === 200) {
        const { job_id } = JSON.parse(xhr.responseText)
        setCurrentJobId(job_id)
        startPolling(job_id) // Extract polling into separate function
      } else {
        setError('Upload failed')
        navigateTo('upload')
      }
    }, 500)
  }

  xhr.onerror = () => {
    setShowUploadProgress(false)
    setError('Network error during upload')
    navigateTo('upload')
  }

  xhr.open('POST', `${API_URL}/api/upload`)
  xhr.setRequestHeader('Authorization', `Bearer ${localStorage.getItem('auth_token')}`)
  xhr.send(uploadFormData)
}
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
grep -c "setInterval" src/App.jsx  # Should be reduced (no fake progress intervals)
npm run build
```

**Commit:** `feat: real upload progress via XHR instead of fake setInterval`

---

## PHASE 3: 3D / Animation Overhaul 🎬

> Transform the visual experience. Requires R3F compatibility fixes first.

---

### Task 3.1: Fix R3F / Three.js Dependency Compatibility

**Objective:** Resolve the `@react-three/postprocessing@2` + R3F v8 black canvas issue

**Files:**
- Modify: `frontend/package.json`

**Steps:**

1. Option A (recommended) — Remove postprocessing since it's unused:
```bash
cd frontend
npm uninstall @react-three/postprocessing
```

2. Verify R3F + drei work without it:
```bash
npm run build
npm run dev
# Check: 3D background should render without black canvas
```

3. If postprocessing IS needed later, upgrade to:
```bash
npm install @react-three/fiber@^9 @react-three/drei@^10 @react-three/postprocessing@^3 three@^0.170
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
npm ls @react-three/fiber @react-three/drei  # Should show compatible versions
npm run dev  # 3D background should render properly
```

**Commit:** `fix: remove unused postprocessing to prevent R3F black canvas`

---

### Task 3.2: Create Shared R3F Canvas Provider

**Objective:** Create a single, shared Canvas context instead of multiple independent Canvas instances

**Files:**
- Create: `frontend/src/components/3d/R3FProvider.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/3d/Scene3DBackground.jsx`

**Steps:**

1. Create a provider that wraps the entire app with a single Canvas:
```jsx
// frontend/src/components/3d/R3FProvider.jsx
import { Canvas } from '@react-three/fiber'
import { Suspense } from 'react'

export function R3FProvider({ children }) {
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none' }}>
      <Canvas
        camera={{ position: [0, 0, 30], fov: 60 }}
        gl={{ antialias: true, alpha: true }}
        style={{ width: '100%', height: '100%' }}
      >
        <Suspense fallback={null}>
          {children}
        </Suspense>
      </Canvas>
    </div>
  )
}
```

2. Move scene contents (Stars, Particles, Aurora, Shapes) into separate "scene layers":
```jsx
// frontend/src/components/3d/SceneContent.jsx
import { Stars } from '@react-three/drei'
import { ParticlesLayer } from './ParticlesLayer'
import { AuroraLayer } from './AuroraLayer'
import { ShapesLayer } from './ShapesLayer'

export function SceneContent() {
  return (
    <>
      <color attach="background" args={['#0b0f1a']} />
      <fog attach="fog" args={['#0b0f1a', 10, 100]} />
      <Stars radius={100} depth={100} count={2000} saturation={0.2} factor={4} size={0.5} color="#6366f1" />
      <ParticlesLayer />
      <AuroraLayer />
      <ShapesLayer />
      <ambientLight intensity={0.3} color="#6366f1" />
      <directionalLight position={[10, 10, 5]} intensity={0.5} color="#6366f1" />
    </>
  )
}
```

3. In `App.jsx`, wrap the app:
```jsx
<R3FProvider>
  <SceneContent />
</R3FProvider>
{children}
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
ls src/components/3d/SceneContent.jsx  # Should exist
npm run build  # Should succeed
npm run dev  # Single Canvas, all 3D elements rendered together
```

**Commit:** `feat: shared R3F Canvas provider for unified 3D scene management`

---

### Task 3.3: Create Perlin Noise Fluid Background

**Objective:** Replace flat color background with animated Perlin noise fluid — the signature visual element

**Files:**
- Create: `frontend/src/components/3d/PerlinFluid.jsx`
- Modify: `frontend/src/components/3d/SceneContent.jsx`

**Steps:**

1. Create a custom shader for Perlin noise fluid:
```jsx
// frontend/src/components/3d/PerlinFluid.jsx
import { useRef, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'

const vertexShader = `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`

const fragmentShader = `
  uniform float uTime;
  uniform vec3 uColor1;
  uniform vec3 uColor2;
  varying vec2 vUv;
  
  // Simplex noise functions...
  // (include full Perlin noise implementation)
  
  void main() {
    float noise = snoise(vec3(vUv * 3.0, uTime * 0.1));
    noise = noise * 0.5 + 0.5; // Remap to 0-1
    
    vec3 color = mix(uColor1, uColor2, noise);
    float alpha = 0.08 + noise * 0.05;
    
    gl_FragColor = vec4(color, alpha);
  }
`

export function PerlinFluid() {
  const meshRef = useRef()
  const uniforms = useMemo(() => ({
    uTime: { value: 0 },
    uColor1: { value: new THREE.Color('#6366f1') },
    uColor2: { value: new THREE.Color('#06b6d4') },
  }), [])

  useFrame((state) => {
    if (meshRef.current) {
      uniforms.uTime.value = state.clock.elapsedTime
    }
  })

  return (
    <mesh ref={meshRef} position={[0, 0, -20]} scale={80}>
      <planeGeometry args={[1, 1, 1, 1]} />
      <shaderMaterial
        vertexShader={vertexShader}
        fragmentShader={fragmentShader}
        uniforms={uniforms}
        transparent
        depthWrite={false}
      />
    </mesh>
  )
}
```

2. Add to SceneContent:
```jsx
<PerlinFluid />
```

3. The `prefersReducedMotion` check should skip this and fall back to a static gradient.

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
ls src/components/3d/PerlinFluid.jsx  # Should exist
npm run build  # Shader compiles
npm run dev  # Should see animated fluid background
```

**Commit:** `feat: Perlin noise fluid background shader`

---

### Task 3.4: Create Diamond Logo 3D Component

**Objective:** Add the brand's ◆ diamond as a floating 3D element in the scene

**Files:**
- Create: `frontend/src/components/3d/DiamondLogo.jsx`
- Modify: `frontend/src/components/3d/SceneContent.jsx`

**Steps:**

1. Create a wireframe/solid diamond (octahedron) with glow:
```jsx
// frontend/src/components/3d/DiamondLogo.jsx
import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Float } from '@react-three/drei'

export function DiamondLogo({ position = [0, 3, 5], scale = 1 }) {
  const meshRef = useRef()
  const glowRef = useRef()

  useFrame((state) => {
    if (meshRef.current) {
      meshRef.current.rotation.y = state.clock.elapsedTime * 0.3
      meshRef.current.rotation.z = Math.sin(state.clock.elapsedTime * 0.5) * 0.1
    }
    if (glowRef.current) {
      glowRef.current.material.opacity = 0.1 + Math.sin(state.clock.elapsedTime * 2) * 0.05
    }
  })

  return (
    <Float speed={1.5} rotationIntensity={0.2} floatIntensity={0.5}>
      <group position={position} scale={scale}>
        {/* Main diamond */}
        <mesh ref={meshRef}>
          <octahedronGeometry args={[1, 0]} />
          <meshPhysicalMaterial
            color="#6366f1"
            metalness={0.8}
            roughness={0.1}
            transmission={0.3}
            clearcoat={1}
            emissive="#6366f1"
            emissiveIntensity={0.2}
          />
        </mesh>
        {/* Glow sphere */}
        <mesh ref={glowRef} scale={1.5}>
          <sphereGeometry args={[1, 16, 16]} />
          <meshBasicMaterial
            color="#6366f1"
            transparent
            opacity={0.1}
            side={2}
            depthWrite={false}
          />
        </mesh>
      </group>
    </Float>
  )
}
```

2. Add to SceneContent (visible on auth/home pages):
```jsx
<DiamondLogo />
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
ls src/components/3d/DiamondLogo.jsx  # Should exist
npm run build
npm run dev  # Should see floating ◆ diamond
```

**Commit:** `feat: 3D diamond logo component with float animation`

---

### Task 3.5: Create Story Player 3D Scene Depth

**Objective:** Upgrade the story player from flat plane to immersive 3D with depth layers

**Files:**
- Modify: `frontend/src/components/Scene3D.jsx`
- Create: `frontend/src/components/3d/StoryDepthScene.jsx`

**Steps:**

1. Create a depth-layered scene component:
```jsx
// frontend/src/components/3d/StoryDepthScene.jsx
import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html, Float } from '@react-three/drei'
import * as THREE from 'three'

export function StoryDepthScene({ imageUrl, text, sceneIndex }) {
  const groupRef = useRef()
  
  useFrame((state) => {
    if (groupRef.current) {
      // Gentle breathing motion
      groupRef.current.position.y = Math.sin(state.clock.elapsedTime * 0.5) * 0.05
    }
  })

  return (
    <group ref={groupRef}>
      {/* Background depth layer — blurred scene image */}
      <mesh position={[0, 0, -2]}>
        <planeGeometry args={[18, 10]} />
        {imageUrl ? (
          <meshBasicMaterial color="#0b0f1a" transparent opacity={0.8} />
        ) : (
          <meshBasicMaterial color="#1a1a3e" />
        )}
      </mesh>

      {/* Mid layer — scene image with depth */}
      <Float speed={0.5} rotationIntensity={0} floatIntensity={0.2}>
        <mesh position={[0, 0, 0]}>
          <planeGeometry args={[14, 8]} />
          <meshStandardMaterial
            color="#6366f1"
            transparent
            opacity={imageUrl ? 0.05 : 0.15}
            side={THREE.DoubleSide}
          />
        </mesh>
      </Float>

      {/* Foreground — HTML image overlay */}
      {imageUrl && (
        <Html transform position={[0, 0, 0.1]} style={{ pointerEvents: 'none', width: '100%', height: '100%' }}>
          <img
            src={imageUrl}
            alt={`Scene ${sceneIndex + 1}`}
            style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '12px' }}
          />
        </Html>
      )}

      {/* Ambient particles around the scene */}
      <SceneParticles count={50} />
    </group>
  )
}

function SceneParticles({ count = 50 }) {
  const ref = useRef()
  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3)
    for (let i = 0; i < count * 3; i += 3) {
      arr[i] = (Math.random() - 0.5) * 16
      arr[i + 1] = (Math.random() - 0.5) * 10
      arr[i + 2] = (Math.random() - 0.5) * 4 - 1
    }
    return arr
  }, [count])

  useFrame((state) => {
    if (ref.current) {
      ref.current.rotation.z = state.clock.elapsedTime * 0.01
    }
  })

  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" array={positions} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial size={0.05} transparent opacity={0.4} color="#6366f1" sizeAttenuation depthWrite={false} />
    </points>
  )
}
```

2. Replace Scene3D's content with StoryDepthScene.

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
npm run build
npm run dev  # Story player should show depth-layered 3D scene
```

**Commit:** `feat: 3D depth-layered story player scene with ambient particles`

---

### Task 3.6: Add Scroll-Reveal Stagger Animations to Home Page

**Objective:** Transform the home page from static buttons to staggered reveal with depth

**Files:**
- Modify: `frontend/src/App.jsx` (home section)
- Create: `frontend/src/components/HomeCard.jsx`

**Steps:**

1. Create a reusable animated card component:
```jsx
// frontend/src/components/HomeCard.jsx
import { motion } from 'framer-motion'

export function HomeCard({ emoji, title, description, onClick, index = 0 }) {
  return (
    <motion.button
      className="home-btn"
      onClick={onClick}
      initial={{ opacity: 0, y: 40, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{
        duration: 0.6,
        delay: 0.2 + index * 0.15,
        ease: [0.22, 1, 0.36, 1],
      }}
      whileHover={{ scale: 1.02, y: -4 }}
      whileTap={{ scale: 0.98 }}
    >
      <div className="emoji">{emoji}</div>
      <strong>{title}</strong>
      <span>{description}</span>
    </motion.button>
  )
}
```

2. Update the home section in App.jsx:
```jsx
{step === 'home' && (
  <motion.div key="home" className="home-wrapper">
    <div className="home-content-overlay">
      <motion.div
        className="home-pill"
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
      >
        ✨ AI-Powered Storymaker
      </motion.div>
      <motion.h1
        className="home-title"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, delay: 0.1 }}
      >
        Turn Lessons into Adventures
      </motion.h1>
      <motion.p
        className="home-subtitle"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5, delay: 0.2 }}
      >
        Upload a PDF, choose your grade level, and let AI create an immersive story.
      </motion.p>
      <div className="home-buttons">
        <HomeCard
          emoji="✨"
          title="Create New Story"
          description="Upload a lesson file and let AI turn it into a story"
          onClick={() => navigateTo('upload')}
          index={0}
        />
        <HomeCard
          emoji="📚"
          title="Load Online Story"
          description="Pull down a saved adventure from the cloud"
          onClick={() => navigateTo('load')}
          index={1}
        />
        <HomeCard
          emoji="📱"
          title="Offline Manager"
          description="Manage locally stored stories without internet"
          onClick={() => navigateTo('offline')}
          index={2}
        />
      </div>
    </div>
  </motion.div>
)}
```

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
ls src/components/HomeCard.jsx  # Should exist
npm run build
npm run dev  # Cards should stagger-animate in on load
```

**Commit:** `feat: staggered reveal animations for home page cards`

---

### Task 3.7: Mobile Performance Optimization Pass

**Objective:** Ensure all 3D elements perform well on mobile devices

**Files:**
- Modify: `frontend/src/components/3d/Scene3DBackground.jsx`
- Modify: `frontend/src/components/3d/PerlinFluid.jsx`
- Modify: `frontend/src/components/3d/SceneContent.jsx`

**Steps:**

1. Add mobile detection and reduce particle counts:
```jsx
const isMobile = typeof window !== 'undefined' && window.innerWidth < 768

// In SceneContent:
<Stars count={isMobile ? 500 : 2000} ... />
<ParticlesLayer count={isMobile ? 100 : 300} />
<ShapesLayer count={isMobile ? 6 : 12} />
```

2. Reduce shader complexity on mobile:
```jsx
// In PerlinFluid, use lower resolution on mobile:
<planeGeometry args={[1, 1, isMobile ? 1 : 2, isMobile ? 1 : 2]} />
```

3. Add frame rate limiter for mobile:
```jsx
// In R3FProvider:
<Canvas
  frameloop={isMobile ? 'demand' : 'always'}
  ...
>
```

4. Test with Chrome DevTools throttling (4x CPU slowdown, fast 3G).

**Verification:**
```bash
cd /www/wwwroot/edusmart/frontend
npm run build
npm run dev
# Open Chrome DevTools → Performance → CPU 4x slowdown
# Should maintain 30+ fps on throttled mobile
```

**Commit:** `perf: mobile-optimized 3D with reduced particles and frame limiting`

---

## Execution Order

| Phase | Tasks | Estimated Time | Deployable? |
|-------|-------|---------------|-------------|
| **Phase 1** | Tasks 1.1-1.8 | ~2 hours | ✅ Yes, critical |
| **Phase 2** | Tasks 2.1-2.10 | ~3 hours | ✅ Yes, independent |
| **Phase 3** | Tasks 3.1-3.7 | ~4 hours | ✅ Yes, visual only |

**Total estimated time: ~9 hours**

Each phase can be deployed independently. Phase 1 is urgent (security). Phase 2 improves mobile UX. Phase 3 is the visual overhaul.

---

## Verification Checklist (Post-All-Phases)

```bash
# Backend
cd /www/wwwroot/edusmart/backend
grep -rn "TTS_AHTE" .  # 0 results
grep -rn "hardcoded" .  # 0 API keys
python -c "from main import app"  # Import succeeds

# Frontend
cd /www/wwwroot/edusmart/frontend
npm run build  # Builds without errors
npm run dev    # Starts without warnings
# Manual: Auth page loads, 3D background renders
# Manual: Home page stagger-animates
# Manual: Story player shows depth layers
# Manual: Upload shows real progress
# Manual: No console.log spam in production
# Manual: ARIA labels present on all buttons
# Manual: Error boundary catches crashes
```
