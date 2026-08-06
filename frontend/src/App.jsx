import { useState, useEffect, useRef, lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Sparkles, BookOpen, Smartphone, AlertTriangle, ShieldAlert, RefreshCw } from 'lucide-react'
import { useAuth } from './context/AuthContext'
import apiClient from './services/api'
import updateService from './services/updateService'
import Login from './components/Login'
import Signup from './components/Signup'
import VerifyEmail from './components/VerifyEmail'
import ForgotPassword from './components/ForgotPassword'
import ResetPassword from './components/ResetPassword'
import SharedStory from './components/SharedStory'
import FileUpload from './components/FileUpload'
import FileConfirmation from './components/FileConfirmation'
import GeneratingSpinner from './components/GeneratingSpinner'
import StoryPlayer from './components/StoryPlayer'
const Scene3DBackground = lazy(() => import('./components/3d/Scene3DBackground'));
const HeroScene = lazy(() => import('./components/3d/HeroScene'));
import SaveStoryModal from './components/SaveStoryModal'
import SaveFeedbackModal from './components/SaveFeedbackModal'
import LoadStory from './components/LoadStory'
import OfflineManager from './components/OfflineManager'
import UserProfile from './components/UserProfile'
import ReuploadConfirmModal from './components/ReuploadConfirmModal'
import UploadProgressOverlay from './components/UploadProgressOverlay'
import DuplicateStoryModal from './components/DuplicateStoryModal'
import PricingPage from './components/PricingPage'
import UpgradeModal from './components/UpgradeModal'
import TeacherCard from './components/TeacherCard'
import NavigationMenu from './components/NavigationMenu'
import BrandMark from './components/BrandMark'
import Mascot from './components/Mascot'
import ErrorBoundary from './components/ErrorBoundary'
import AnnouncementBanner from './components/AnnouncementBanner'
const AdminPanel = lazy(() => import('./components/AdminPanel'));
import './App.css'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function App() {
  // Use Routes to handle verification and password reset pages
  return (
    <>
      <AnnouncementBanner />
      <Routes>
        <Route path="/verify-email" element={<VerifyEmail />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        {/* Public shared story. Must sit ABOVE the /* catch-all, and outside
            MainApp's auth gate - the whole point is that a visitor with no
            account can open it. */}
        <Route path="/s/:token" element={<SharedStory />} />
        <Route path="/*" element={<MainApp />} />
      </Routes>
    </>
  )
}

function MainApp() {
  const DEBUG = import.meta.env.DEV;
  const { user, isAuthenticated, isLoading, logout } = useAuth()
  const [authStep, setAuthStep] = useState('login') // 'login' or 'signup'
  const [signupSuccess, setSignupSuccess] = useState(false)
  const [step, setStep] = useState('home') 
  const [uploadedFile, setUploadedFile] = useState(null)
  const [selectedAvatar, setSelectedAvatar] = useState(null)
  // New state for Kokoro TTS
  const [voice, setVoice] = useState('af_sarah');
  const [detectedLanguage, setDetectedLanguage] = useState('en');
  
  const [storyData, setStoryData] = useState(null)
  const storyPlayerRef = useRef(null);
  const [progress, setProgress] = useState(0)
  const [totalScenes, setTotalScenes] = useState(0) // Track total scenes from backend
  const [completedSceneCount, setCompletedSceneCount] = useState(0) // Track completed scenes
  // Save and Download stay unavailable until EVERY scene has both its image
  // and its audio. Saving mid-generation moves the live story folder out
  // from under the background TTS worker, so any scene still being written
  // lands nowhere and its narration is lost silently.
  const storyFullyReady = totalScenes > 0 && completedSceneCount >= totalScenes
  const [error, setError] = useState(null)
  // Set when the backend reports attempt > 1: the first generation failed for a
  // retryable reason and a second is running. Without this the extra ~60s reads
  // as the app having hung.
  const [isRetrying, setIsRetrying] = useState(false)
  // A note on a DELIVERED story ("8 questions instead of the 10 you asked for"),
  // never an error - the story is complete and playable either way.
  const [quizNotice, setQuizNotice] = useState(null)
  // Grade id, e.g. "KG1", "KG2", "1".."10" - matches backend
  // services/grade_bands.py GRADE_BANDS keys exactly. This used to be a bare
  // int (1-7) sent straight to the LLM prompt as e.g. "3", with no grade
  // descriptor at all - see grade_bands.py for why that mattered.
  const [gradeLevel, setGradeLevel] = useState('4')
  const [currentJobId, setCurrentJobId] = useState(null)
  const [showSaveModal, setShowSaveModal] = useState(false)
  const [isSaved, setIsSaved] = useState(false)
  const [savedStoryId, setSavedStoryId] = useState(null)
  const [isOfflineMode, setIsOfflineMode] = useState(false)
  const [showReuploadModal, setShowReuploadModal] = useState(false)
  const [showDuplicateModal, setShowDuplicateModal] = useState(false)
  const [duplicateInfo, setDuplicateInfo] = useState(null)
  const [showUpgradeModal, setShowUpgradeModal] = useState(false)
  const [upgradeMessage, setUpgradeMessage] = useState('')
  const [fileHash, setFileHash] = useState(null)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadFileName, setUploadFileName] = useState('')
  const [showUploadProgress, setShowUploadProgress] = useState(false)
  const fileInputRef = useRef(null)
  const pollTimerRef = useRef(null)
  const pollErrorsRef = useRef(0)
  const pollLastProgressRef = useRef({ at: 0, value: -1 })
  const generationStartRef = useRef(0)
  const [generatingStage, setGeneratingStage] = useState(0)
  // 0 = running (or unknown); >0 means the job is still waiting for a
  // generation worker, and the number is how many stories are ahead of it.
  const [queuePosition, setQueuePosition] = useState(0)
  const storyDataRef = useRef(null)
  const stepRef = useRef(step)
  const [showUpdateNotification, setShowUpdateNotification] = useState(false)
  const [saveFeedback, setSaveFeedback] = useState(null)

  // Initialize version tracking and check for updates
  useEffect(() => {
    storyDataRef.current = storyData
  }, [storyData])

  useEffect(() => {
    stepRef.current = step
  }, [step])

  useEffect(() => {
    return () => stopPolling()
  }, [])

  useEffect(() => {
    updateService.initializeVersion();
    
    // Start checking for updates
    updateService.startPeriodicCheck(() => {
      setShowUpdateNotification(true);
    });

    return () => {
      updateService.stopPeriodicCheck();
    };
  }, []);

  const handleUpdateApp = () => {
    updateService.applyUpdate();
  };

  const dismissUpdate = () => {
    setShowUpdateNotification(false);
  };

  // Manual "Check for Updates" from the nav menu used to be its own alert()/confirm()
  // flow, separate from the auto-detected update path above. Routes through the
  // same showUpdateNotification toast and saveFeedback modal instead, so there's
  // one consistent, non-native UI for updates rather than two.
  const handleCheckForUpdate = async () => {
    try {
      const hasUpdate = await updateService.checkForUpdates()
      if (hasUpdate) {
        setShowUpdateNotification(true)
      } else {
        setSaveFeedback({
          variant: 'success',
          title: "You're up to date",
          message: 'LearnTale is already running the latest version.',
        })
      }
    } catch (err) {
      setSaveFeedback({
        variant: 'error',
        title: 'Update check failed',
        message: 'Could not check for updates. Please try again.',
      })
    }
  };

  // Handle browser back button to navigate within app steps
  useEffect(() => {
    const handlePopState = (event) => {
      if (event.state && event.state.step) {
        // Use the step from history state
        setStep(event.state.step)
      } else {
        // Fallback to previous step logic
        const prev = previousStep(step)
        setStep(prev)
      }
    }

    // Push current step to history
    window.history.replaceState({ step }, '', window.location.pathname)
    window.addEventListener('popstate', handlePopState)
    
    return () => window.removeEventListener('popstate', handlePopState)
  }, [step])

  const navigateTo = (newStep) => {
    window.history.pushState({ step: newStep }, '', window.location.pathname)
    setStep(newStep)
  }

  // There is now exactly one upload path (startUpload), so this is the single
  // place that knows how to interpret a /api/status/{job_id} tick. There used to
  // be two upload implementations with separate, drifting copies of this logic,
  // which is how one of them ended up with a real bug.
  // Polling control. A fixed 2s setInterval that aborted on the first failed
  // fetch was a bad fit for the target device: a phone that briefly drops to no
  // signal would kill a perfectly healthy 3-minute generation with
  // "Connection lost". Now: tolerate transient failures, slow down once the job
  // is clearly long-running, and give up only when nothing has actually moved
  // for a long time.
  const POLL_FAST_MS = 2000
  const POLL_SLOW_MS = 5000
  const POLL_SLOW_AFTER_MS = 60000
  const POLL_MAX_CONSECUTIVE_ERRORS = 4
  const POLL_STALL_TIMEOUT_MS = 300000

  const stopPolling = () => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }

  const startPolling = (jobId) => {
    stopPolling()
    pollErrorsRef.current = 0
    pollLastProgressRef.current = { at: Date.now(), value: -1 }
    const tick = () => {
      pollTimerRef.current = setTimeout(async () => {
        const stillRunning = await pollJobStatus(jobId)
        if (stillRunning) tick()
      }, Date.now() - generationStartRef.current > POLL_SLOW_AFTER_MS ? POLL_SLOW_MS : POLL_FAST_MS)
    }
    tick()
  }

  // Returns true if polling should continue.
  const pollJobStatus = async (jobId) => {
    try {
      const statusRes = await fetch(`${API_URL}/api/status/${jobId}`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('auth_token')}` }
      })
      if (!statusRes.ok) throw new Error('Could not fetch status')

      const job = await statusRes.json()
      pollErrorsRef.current = 0

      // Queue position, when the backend reports one. A queued job has no
      // progress and no scenes by definition, so this has to be read before
      // stall detection or a healthy wait looks identical to a hang.
      const position = job.queue_position ?? 0
      setQueuePosition(position)

      // Stall detection: a wedged backend job used to be polled forever with no
      // feedback. Track real forward motion, not just "did the request succeed".
      // Queue position is part of the marker so a queue that is draining counts
      // as motion, and a job that is merely waiting is never tripped at all -
      // the backend's own generation timeout is what reclaims a stuck worker.
      const marker = (job.progress ?? 0) * 1000 + (job.completed_scene_count ?? 0) - position
      if (marker !== pollLastProgressRef.current.value) {
        pollLastProgressRef.current = { at: Date.now(), value: marker }
      } else if (position === 0 && Date.now() - pollLastProgressRef.current.at > POLL_STALL_TIMEOUT_MS) {
        stopPolling()
        setError("This story seems to be stuck - nothing has moved for 5 minutes. Your credit hasn't been used up; please try again.")
        navigateTo('upload')
        return false
      }

      const elapsedSinceStart = Date.now() - generationStartRef.current
      setGeneratingStage(elapsedSinceStart < 8000 ? 0 : elapsedSinceStart < 20000 ? 1 : 2)

      // >1 means the first attempt failed for a retryable reason and the backend
      // is running a second one. Reported here rather than left as dead air.
      setIsRetrying((job.attempt ?? 1) > 1)

      if (job.status === 'processing') {
        setProgress((prev) => Math.max(prev, job.progress ?? prev))

        if (job.total_scenes > 0) {
          setTotalScenes(job.total_scenes)
          setCompletedSceneCount(job.completed_scene_count || 0)
        }

        if (job.result && job.result.scenes && job.result.scenes.length > 0) {
          if (!storyDataRef.current || storyDataRef.current.scenes.length === 0) {
            setStoryData(job.result)
            navigateTo('playing')
          } else {
            setStoryData(job.result)
          }
        }
      } else if (job.status === 'completed') {
        stopPolling()
        setStoryData(job.result)
        setProgress(100)
        setIsRetrying(false)
        setQuizNotice(job.quiz_notice || null)
        if (job.total_scenes > 0) {
          setTotalScenes(job.total_scenes)
          setCompletedSceneCount(job.total_scenes)
        }
        if (stepRef.current !== 'playing') {
          navigateTo('playing')
        }
        return false
      } else if (job.status === 'failed') {
        stopPolling()
        setIsRetrying(false)
        // job.error is now the classified, cause-specific sentence from
        // services/failure_reasons.py, already carrying the credit-refund line.
        // The old fallback stays only for a backend older than that change.
        setError(job.error || 'AI Generation failed.')
        // A retryable failure (busy service, timeout, incomplete story) is not
        // the file's fault, so keep the file and the voice choice and land the
        // user one tap from trying again. A verdict-style failure - not
        // educational, unreadable - needs a DIFFERENT document, so send them
        // back to pick one.
        navigateTo(job.can_retry ? 'confirm' : 'upload')
        return false
      }
      return true
    } catch (err) {
      // Transient network blips are expected on mobile; only surface an error
      // once several consecutive attempts have failed.
      pollErrorsRef.current += 1
      if (pollErrorsRef.current < POLL_MAX_CONSECUTIVE_ERRORS) {
        return true
      }
      stopPolling()
      setError('Connection lost: ' + err.message)
      navigateTo('upload')
      return false
    }
  }

  // Mobile browsers throttle/suspend setInterval heavily in a backgrounded or
  // screen-locked tab — the "scenes ready" count can silently freeze for minutes
  // even though the backend kept working. Re-poll immediately the moment the tab
  // becomes visible again instead of waiting for the next (possibly very late) tick.
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'visible' && pollTimerRef.current && currentJobId) {
        pollJobStatus(currentJobId)
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [currentJobId])

  // Remember which story is on screen so an accidental refresh doesn't wipe it.
  // The backend keeps unsaved generated stories around too (only resetStory's
  // explicit delete-story call removes one early) - this is just a pointer, not
  // a copy of the story itself, rehydrated via the same endpoints already used
  // elsewhere in this file. Offline stories are skipped - OfflineManager already
  // owns their persistence.
  const ACTIVE_SESSION_KEY = 'edusmart_active_session'
  useEffect(() => {
    if (step === 'playing' && !isOfflineMode) {
      localStorage.setItem(ACTIVE_SESSION_KEY, JSON.stringify({
        jobId: currentJobId, savedStoryId, ts: Date.now(),
      }))
    } else if (step === 'home') {
      localStorage.removeItem(ACTIVE_SESSION_KEY)
    }
  }, [step, currentJobId, savedStoryId, isOfflineMode])

  const rehydratedRef = useRef(false)
  useEffect(() => {
    if (!isAuthenticated || rehydratedRef.current) return
    rehydratedRef.current = true

    let pointer
    try {
      pointer = JSON.parse(localStorage.getItem(ACTIVE_SESSION_KEY) || 'null')
    } catch {
      pointer = null
    }
    if (!pointer) return
    // Cap at 24h, matching the backend's own generated-story TTL - no point
    // trying to resurrect a pointer to something guaranteed already cleaned up.
    if (Date.now() - (pointer.ts || 0) > 24 * 60 * 60 * 1000) {
      localStorage.removeItem(ACTIVE_SESSION_KEY)
      return
    }

    (async () => {
      try {
        if (pointer.savedStoryId) {
          const response = await apiClient.get(`/api/load-story/${pointer.savedStoryId}`)
          if (response.data?.story_data && stepRef.current === 'home') {
            setStoryData(response.data.story_data)
            setSelectedAvatar({ id: 'loaded', name: response.data.name })
            setIsSaved(true)
            setSavedStoryId(pointer.savedStoryId)
            navigateTo('playing')
          }
          return
        }
        if (pointer.jobId) {
          const statusRes = await fetch(`${API_URL}/api/status/${pointer.jobId}`, {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('auth_token')}` }
          })
          if (!statusRes.ok) throw new Error('Session expired')
          const job = await statusRes.json()
          if (stepRef.current !== 'home') return

          if (job.status === 'completed' && job.result) {
            setStoryData(job.result)
            setCurrentJobId(pointer.jobId)
            setProgress(100)
            if (job.total_scenes > 0) {
              setTotalScenes(job.total_scenes)
              setCompletedSceneCount(job.total_scenes)
            }
            navigateTo('playing')
          } else if (job.status === 'processing' && job.result?.scenes?.length > 0) {
            setStoryData(job.result)
            setCurrentJobId(pointer.jobId)
            generationStartRef.current = Date.now()
            navigateTo('playing')
            startPolling(pointer.jobId)
          } else {
            localStorage.removeItem(ACTIVE_SESSION_KEY)
          }
        }
      } catch {
        // Expected/benign - session simply aged out or story was deleted.
        // No error banner for what isn't the user's fault or action.
        localStorage.removeItem(ACTIVE_SESSION_KEY)
      }
    })()
  }, [isAuthenticated])

  // If not logged in and not loading, show auth screen
  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#150d33' }}>
        <div style={{ color: '#f8fafc', fontSize: '20px' }}>Loading...</div>
      </div>
    )
  }

  if (!isAuthenticated) {
    if (signupSuccess) {
      return (
        <div className="auth-container" style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
          color: 'white'
        }}>
          <motion.div
            className="auth-box"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <h2>Signup Successful!</h2>
            <p style={{ margin: '20px 0' }}>Please check your email to verify your account, then you can log in.</p>
            <button className="auth-button" onClick={() => {
              setSignupSuccess(false);
              setAuthStep('login');
            }}>
              Proceed to Login
            </button>
          </motion.div>
        </div>
      );
    }

    return (
      <div className="auth-page" style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh'
      }}>
        <AnimatePresence mode="wait">
          {authStep === 'login' ? (
            <Login
              key="login"
              onSwitchToSignup={() => setAuthStep('signup')}
            />
          ) : (
            <Signup
              key="signup"
              onSwitchToLogin={() => setAuthStep('login')}
              onSuccess={() => setSignupSuccess(true)}
            />
          )}
        </AnimatePresence>
      </div>
    )
  }

  const previousStep = (current) => {
    switch (current) {
      case 'playing':
      case 'generating':
        return 'confirm'
      case 'confirm':
        return 'upload'
      case 'upload':
      case 'offline':
      case 'load':
      case 'profile':
      default:
        return 'home'
    }
  }

  const handleFileUpload = async (file) => {
    // A new attempt starts here, so any error from the previous one is stale.
    // Nothing on this path used to clear it, which left the banner from a failed
    // upload sitting above a perfectly healthy retry.
    setError(null)
    setUploadedFile(file)
    setUploadFileName(file.name)
    setShowUploadProgress(true)
    setUploadProgress(0)
    
    // Calculate file hash and check for duplicates
    let fileHashLocal = null
    try {
      const formData = new FormData()
      formData.append('file', file)
      
      if (DEBUG) console.log('📤 Checking for duplicates...')
      const response = await apiClient.post('/api/check-duplicate', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      
      if (DEBUG) console.log('✅ Duplicate check response:', response.data)
      
      if (response.data.is_duplicate) {
        // Found duplicate - show modal
        if (DEBUG) console.log('🔄 Duplicate found, showing modal')
        setShowUploadProgress(false)
        setDuplicateInfo(response.data)
        setShowDuplicateModal(true)
        return
      }
      
      // Store hash for later use
      fileHashLocal = response.data.file_hash
      setFileHash(fileHashLocal)
      if (DEBUG) console.log('✅ No duplicate, going to confirm screen')
    } catch (err) {
      if (DEBUG) console.error('❌ Error checking duplicate:', err)
      setShowUploadProgress(false)
      setError('Failed to check for duplicates: ' + err.message)
      return
    }

    // Picking a file selects it, it does not submit it. This used to run the
    // whole upload right here and jump straight to 'generating', which made the
    // confirm/voice screen reachable ONLY through the duplicate-detection modal
    // (onCreateNew -> navigateTo('confirm')). Testing with the same file every
    // time always hit that modal, so the skipped screen stayed invisible until
    // the first genuinely new file was uploaded.
    setShowUploadProgress(false)
    navigateTo('confirm')
  }

  // The one and only upload implementation. `selectedVoice` is passed in rather
  // than read from state because the confirm screen calls setVoice() and this in
  // the same tick - the state update would not have landed yet, and the user's
  // voice choice would be silently dropped in favour of the previous value.
  const startUpload = (selectedVoice, selectedQuizSize) => {
    const file = uploadedFile
    if (!file) {
      setError('No file selected. Please pick a file again.')
      navigateTo('upload')
      return
    }

    setError(null)
    setShowUploadProgress(true)
    setUploadProgress(0)

    if (DEBUG) console.log('📊 Starting real upload with progress tracking')
    const uploadData = new FormData()
    uploadData.append('file', file)
    uploadData.append('grade_level', gradeLevel)
    uploadData.append('voice', selectedVoice || voice)
    // Passed in for the same reason as the voice: the confirm screen sets it and
    // calls this in one tick, so reading it back from state would send the
    // previous value. The backend re-normalises it, so a junk value degrades to
    // the default instead of failing the upload.
    uploadData.append('quiz_size', String(selectedQuizSize || 10))
    if (fileHash) {
      uploadData.append('file_hash', fileHash)
    }
    uploadData.append('force_new', (duplicateInfo !== null).toString())
    uploadData.append('user_agent', navigator.userAgent)

    const xhr = new XMLHttpRequest()
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        setUploadProgress(Math.round((e.loaded / e.total) * 90))
      }
    })
    xhr.addEventListener('load', () => {
      setUploadProgress(100)
      setTimeout(() => {
        setShowUploadProgress(false)
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const result = JSON.parse(xhr.responseText)
            const jobId = result.job_id
            setCurrentJobId(jobId)
            navigateTo('generating')
            generationStartRef.current = Date.now()
            setGeneratingStage(0)
            // Start polling for job status
            startPolling(jobId)
          } catch (parseErr) {
            setError('Invalid response from server')
            navigateTo('confirm')
          }
        } else if (xhr.status === 429) {
          // Admission control: the queue is full, or this account already has
          // the maximum number of stories generating. Nothing was charged and
          // nothing was created - retrying later is all that is needed.
          try {
            const result = JSON.parse(xhr.responseText)
            setError(result?.detail?.message || 'Too many stories are generating right now. Please try again in a few minutes.')
          } catch (parseErr) {
            setError('Too many stories are generating right now. Please try again in a few minutes.')
          }
          // Stay on 'confirm' - the file and the voice choice are still valid,
          // so the retry is one tap. Going back to 'upload' would make the user
          // re-pick the file for an error that has nothing to do with the file.
          navigateTo('confirm')
        } else if (xhr.status === 402) {
          try {
            const result = JSON.parse(xhr.responseText)
            setUpgradeMessage(result.detail || '')
          } catch (parseErr) {
            setUpgradeMessage('')
          }
          setShowUpgradeModal(true)
          navigateTo('confirm')
        } else {
          setError('Upload failed with status: ' + xhr.status)
          navigateTo('confirm')
        }
      }, 500)
    })
    xhr.addEventListener('error', () => {
      setShowUploadProgress(false)
      setError('Upload failed due to network error')
      navigateTo('confirm')
    })
    xhr.open('POST', API_URL + '/api/upload')
    xhr.setRequestHeader('Authorization', `Bearer ${localStorage.getItem('auth_token')}`)
    xhr.send(uploadData)
  }

  const handleReuploadClick = () => {
    setShowReuploadModal(true)
  }

  const handleReuploadConfirm = () => {
    setShowReuploadModal(false)
    // Trigger hidden file input instead of navigating
    if (fileInputRef.current) {
      fileInputRef.current.click()
    }
  }

  const handleFileInputChange = (e) => {
    const files = e.target.files
    if (files && files.length > 0) {
      handleFileUpload(files[0])
    }
  }

  const handleConfirmFile = async (settings) => {
    const chosenVoice = settings?.voice || voice
    if (settings?.voice) {
      setVoice(settings.voice)
    }
    setProgress(0)
    setIsSaved(false)
    setIsRetrying(false)
    setQuizNotice(null)
    // duplicateInfo being set means the user came through the duplicate modal
    // and explicitly asked for a fresh generation.
    startUpload(chosenVoice, settings?.quizSize)
    setDuplicateInfo(null)
  }

  const handleLogout = () => {
    localStorage.removeItem(ACTIVE_SESSION_KEY)
    logout()
  }

  // Throwing away the current unsaved story is the same teardown every time,
  // but it does NOT always end in the same place, which is why the destination
  // is a parameter rather than baked in. Two callers want two different things:
  // the nav's "New Story" is a request to make one, so it belongs on the upload
  // screen, while finishing the quiz is the end of a session and belongs home.
  // This used to hardcode 'home', so "New Story" quietly did nothing but return
  // to the screen the reader was already looking at.
  const resetStory = (destination) => {
    // Cleanup unsaved story
    if (pollTimerRef.current) {
      stopPolling()
      pollTimerRef.current = null
    }
    // Captured before the state resets below, which would otherwise clear it.
    const jobToDelete = currentJobId && !isSaved ? currentJobId : null

    navigateTo(destination)
    setUploadedFile(null)
    setSelectedAvatar(null)
    setStoryData(null)
    setError(null)
    setProgress(0)
    setTotalScenes(0)
    setCompletedSceneCount(0)
    setCurrentJobId(null)
    setIsSaved(false)
    setSavedStoryId(null)
    setIsOfflineMode(false)

    // Fire-and-forget, and deliberately AFTER the navigation. This DELETE used
    // to be awaited before any UI work happened, so a slow or hanging request -
    // the backend is single-worker and busy while scenes are still generating -
    // left the button looking completely dead: no spinner, no navigation, no
    // error. Server-side cleanup is not something the user should wait on to
    // leave a screen.
    if (jobToDelete) {
      fetch(`${API_URL}/api/delete-story/${jobToDelete}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('auth_token')}` }
      }).catch((error) => {
        if (DEBUG) console.error('Cleanup failed:', error)
      })
    }
  }

  // Both are zero-arg wrappers on purpose: they get handed straight to onClick,
  // so anything taking an argument would receive the click event as its
  // destination and navigate somewhere that doesn't exist.
  const handleRestart = () => resetStory('home')
  const handleNewStory = () => resetStory('upload')

  const handleSaveStory = () => {
    setShowSaveModal(true)
  }

  const handleSaveComplete = async (storyId, storyName) => {
    setShowSaveModal(false)
    setIsSaved(true)
    setSavedStoryId(storyId)
    setSaveFeedback({
      variant: 'success',
      title: 'Story saved!',
      message: `"${storyName}" is now in your library.`,
    })
  }

  const handleLoadOffline = (loadedStoryData, storyName) => {
    setStoryData(loadedStoryData)
    setSelectedAvatar({ id: 'offline', name: 'Offline Story' })
    setIsSaved(true)
    setIsOfflineMode(true)
    navigateTo('playing')
  }

  const handleSaveOffline = async (storyData, storyName) => {
    try {
      // Save to localStorage
      const storyId = `local_${Date.now()}`
      const localStory = {
        id: storyId,
        name: storyName,
        storyData: storyData,
        savedAt: Date.now(),
        isOffline: true
      }
      
      localStorage.setItem(`edusmart_story_${storyId}`, JSON.stringify(localStory))
      setSaveFeedback({
        variant: 'success',
        title: 'Saved locally!',
        message: `"${storyName}" is available offline.`,
      })
      return storyId
    } catch (error) {
      throw new Error('Failed to save locally: ' + error.message)
    }
  }

  const handlePlayStoryFromAdmin = async (storyId) => {
    if (!storyId) {
      setError('Cannot play story: Invalid story ID');
      return;
    }
    
    try {
      const response = await apiClient.get(`/api/load-story/${storyId}`);
      
      if (response.data && response.data.story_data) {
          setStoryData(response.data.story_data);
          setSelectedAvatar({ id: 'loaded', name: response.data.name });
          setIsSaved(true);
          setSavedStoryId(storyId);
          navigateTo('playing');
      } else {
        setError('Story data is incomplete or invalid');
      }
    } catch (err) {
        if (DEBUG) console.error('Error loading story:', err);
        setError(`Failed to load story. ${err.response?.data?.detail || err.message}`);
    }
  }

  return (
    <ErrorBoundary>
    <div className={`app${step === 'playing' ? ' is-playing' : ''}`}>
      <header className="app-header">
        <div className="app-header-content">
          <BrandMark />
          <p className="header-subtitle">Where lessons become adventures</p>
        </div>
        {isAuthenticated && (
          <NavigationMenu
            user={user}
            isAdmin={user?.is_admin}
            onHome={() => navigateTo('home')}
            onNewStory={handleNewStory}
            onLoadStories={() => navigateTo('load')}
            onOfflineManager={() => navigateTo('offline')}
            onAdminClick={() => navigateTo('admin')}
            onProfile={() => navigateTo('profile')}
            onPricing={() => navigateTo('pricing')}
            onLogout={handleLogout}
            onSaveStory={step === 'playing' && storyFullyReady && !isSaved ? handleSaveStory : null}
            onDownloadStory={step === 'playing' && storyFullyReady ? () => storyPlayerRef.current?.triggerDownload() : null}
            isPlayingStory={step === 'playing'}
            onCheckUpdate={handleCheckForUpdate}
          />
        )}
      </header>

      <main className="app-main">
        {step !== 'playing' && (
          <ErrorBoundary fallback={<div className="bg-fallback" />}>
            <Suspense fallback={null}>
              {step === 'home'
                ? <HeroScene className="global-3d-background" />
                : <Scene3DBackground className="global-3d-background" />}
            </Suspense>
          </ErrorBoundary>
        )}
        <div className="content-shell">
          {error && (
            <div className="error-message">
              <p><AlertTriangle size={18} aria-hidden="true" /> {error}</p>
              <button onClick={() => setError(null)}>Try Again</button>
            </div>
          )}
          
          {/* mode="wait" removed 2026-08-05: none of the screens below declare
              initial/animate/exit props, so "wait" bought zero visible
              crossfade while creating a real deadlock - StoryPlayer's own
              nested scene-turn AnimatePresence (StoryPlayer.jsx, the
              turnDir-driven page transition) left mid-cycle by a single
              "Next scene" click meant this outer AnimatePresence's
              exit-complete callback never fired, permanently freezing the
              screen on the old step even though `step` state (and the URL
              history) had already updated correctly. Confirmed via a live
              repro: history.state and the .app element's class both flipped
              to the new step instantly, but .story-player never unmounted
              and the new screen's content never mounted either - classic
              orphaned mode="wait" exit signal, not a click or event-handler
              bug. Default (sync) mode swaps screens without waiting on any
              exit callback, which removes the deadlock class entirely. */}
          <AnimatePresence>
            {step === 'admin' && (
              user?.is_admin ? (
                <Suspense fallback={<div className="loading-message">Loading Admin Panel...</div>}>
                  <motion.div key="admin-panel" className="step-container">
                    <AdminPanel 
                      onPlayStory={handlePlayStoryFromAdmin}
                      onBack={() => navigateTo('home')}
                    />
                  </motion.div>
                </Suspense>
              ) : (
                <motion.div key="admin-denied" className="step-container">
                  <div className="error-message">
                    <p><ShieldAlert size={18} aria-hidden="true" /> Admin access required</p>
                    <button onClick={() => navigateTo('home')}>Return Home</button>
                  </div>
                </motion.div>
              )
            )}

            {step === 'home' && (
              <motion.div key="home" className="home-wrapper">
                <div className="home-content-overlay">
                  <div className="home-mascot-stage">
                    <Mascot
                      mood="happy"
                      size={132}
                      trackPointer
                      message="Hoot! What are we learning today?"
                    />
                  </div>
                  <div className="home-pill"><Sparkles size={14} aria-hidden="true" /> Made for curious minds</div>
                  <h1 className="home-title">Turn Lessons into Adventures</h1>
                  <p className="home-subtitle">Hand Ollie a lesson. He'll turn it into an illustrated story, read it out loud, and quiz you at the end.</p>
                  <div className="home-buttons">
                    <motion.button
                      className="home-btn"
                      onClick={() => navigateTo('upload')}
                      whileHover={{ scale: 1.02, y: -4 }}
                      whileTap={{ scale: 0.98 }}
                    >
                      <div className="home-btn-icon home-btn-icon-primary"><Sparkles size={22} aria-hidden="true" /></div>
                      <strong>Start an Adventure</strong>
                      <span>Pick a lesson and watch it turn into a story</span>
                    </motion.button>
                    <motion.button
                      className="home-btn"
                      onClick={() => navigateTo('load')}
                      whileHover={{ scale: 1.02, y: -4 }}
                      whileTap={{ scale: 0.98 }}
                    >
                      <div className="home-btn-icon home-btn-icon-secondary"><BookOpen size={22} aria-hidden="true" /></div>
                      <strong>My Story Shelf</strong>
                      <span>Open an adventure you've already made</span>
                    </motion.button>
                    <motion.button
                      className="home-btn"
                      onClick={() => navigateTo('offline')}
                      whileHover={{ scale: 1.02, y: -4 }}
                      whileTap={{ scale: 0.98 }}
                    >
                      <div className="home-btn-icon home-btn-icon-tertiary"><Smartphone size={22} aria-hidden="true" /></div>
                      <strong>Read Offline</strong>
                      <span>Your downloaded stories work with no internet at all</span>
                    </motion.button>
                  </div>
                </div>
              </motion.div>
            )}

          {step === 'upload' && (
            <motion.div key="upload" className="step-container">
              <FileUpload 
                onUpload={handleFileUpload}
                gradeLevel={gradeLevel}
                onGradeLevelChange={setGradeLevel}
              />
              <button className="back-to-home-btn" onClick={() => navigateTo('home')}>
                ← Back to Home
              </button>
            </motion.div>
          )}

          {step === 'confirm' && (
            <motion.div key="confirm" className="step-container">
              <FileConfirmation
                file={uploadedFile}
                gradeLevel={gradeLevel}
                onConfirm={handleConfirmFile}
                onBack={() => navigateTo('upload')}
                onReupload={handleReuploadClick}
                onEditGrade={(newGrade) => setGradeLevel(newGrade)}
              />
            </motion.div>
          )}

          {step === 'offline' && (
            <motion.div key="offline" className="step-container">
              <OfflineManager 
                onLoadOffline={handleLoadOffline}
                onBack={() => navigateTo('home')}
              />
            </motion.div>
          )}

          {step === 'load' && (
            <motion.div key="load" className="step-container">
              <LoadStory 
                onLoad={(storyData, storyName, storyId) => {
                  setStoryData(storyData)
                  setSelectedAvatar({ id: 'loaded', name: 'Saved Story' })
                  setIsSaved(true)
                  setSavedStoryId(storyId)
                  navigateTo('playing')
                }}
                onBack={() => navigateTo('home')}
              />
            </motion.div>
          )}

          {step === 'profile' && (
            <motion.div key="profile" className="step-container">
              <UserProfile 
                user={user}
                onBack={() => navigateTo('home')}
                onLogout={handleLogout}
                onViewPlans={() => navigateTo('pricing')}
              />
            </motion.div>
          )}

          {step === 'pricing' && (
            <motion.div key="pricing" className="step-container">
              <PricingPage onBack={() => navigateTo('home')} />
            </motion.div>
          )}

          {step === 'generating' && (
            <motion.div key="generating" className="generating-container">
              {/* Progress ring replaces the flat bar: the number is readable at a
                  glance on a phone, and the conic sweep keeps moving so a long
                  generation never looks frozen. */}
              {/* Before the first scene exists there is genuinely nothing to
                  measure, so the backend reports 0 rather than inventing a fake
                  ramp. Showing a literal "0%" for the first ~30s reads as
                  broken - spin the ring instead and say what is happening. */}
              <div
                className={`progress-ring${progress === 0 ? ' is-indeterminate' : ''}`}
                style={{ '--ring-progress': `${progress}%` }}
                role="progressbar"
                {...(progress === 0
                  ? {}
                  : { 'aria-valuenow': progress, 'aria-valuemin': 0, 'aria-valuemax': 100 })}
                aria-label="Story generation progress"
              >
                <div className="progress-ring-track" />
                <div className="progress-ring-core">
                  {progress === 0 ? (
                    <span className="progress-ring-label">Reading</span>
                  ) : (
                    <>
                      <span className="progress-ring-value">{progress}<i>%</i></span>
                      <span className="progress-ring-label">Building</span>
                    </>
                  )}
                </div>
              </div>
              {isRetrying && (
                <p className="retry-notice" role="status">
                  <RefreshCw size={14} aria-hidden="true" />
                  <span>The first attempt didn&rsquo;t come through &mdash; trying once more. Your credit is safe.</span>
                </p>
              )}
              <div className="generating-mascot">
                <Mascot mood={generatingStage === 0 ? 'reading' : 'thinking'} size={104} />
              </div>
              <h2>
                {queuePosition > 0
                  ? (queuePosition > 1 ? `Waiting in line - ${queuePosition - 1} ahead of you` : 'Starting your story...')
                  : ['Ollie is reading your lesson', 'Ollie is writing your story', 'Ollie is painting the pictures'][generatingStage]}
              </h2>
              <div className="progress-container">
                {totalScenes > 0 && (
                  <>
                    {/* One dot per scene. Each lights up the moment that scene's
                        audio is actually published, which is what makes the
                        progressive delivery visible instead of implied. */}
                    <div className="scene-dots" aria-hidden="true">
                      {Array.from({ length: totalScenes }).map((_, i) => (
                        <span
                          key={i}
                          className={`scene-dot ${i < completedSceneCount ? 'is-ready' : ''} ${i === completedSceneCount ? 'is-active' : ''}`}
                        />
                      ))}
                    </div>
                    <p className="scene-progress" aria-live="polite">
                      {completedSceneCount} of {totalScenes} pages ready
                    </p>
                  </>
                )}
              </div>
              <p className="small-text" aria-live="polite">
                {queuePosition > 0
                  ? 'Ollie is finishing someone else\u2019s story first. Yours starts automatically - you can leave this page open.'
                  : [
                      'Turning the pages of your document...',
                      'Dreaming up characters and places...',
                      'The first picture is nearly dry...',
                    ][generatingStage]}
              </p>
            </motion.div>
          )}

        </AnimatePresence>

          {/* Deliberately OUTSIDE AnimatePresence, and a plain div rather than
              motion.div (moved 2026-08-05). This screen never had its own
              enter/exit animation, so it got nothing from framer-motion's
              presence tracking - but it paid for it: StoryPlayer owns a
              SEPARATE nested AnimatePresence for the scene-turn transition
              (StoryPlayer.jsx, turnDir), and once that inner group had been
              through even one enter/exit cycle (i.e. the user clicked "Next
              scene" once), its unresolved presence bookkeeping blocked this
              outer AnimatePresence from ever signaling "safe to unmount" for
              the whole player subtree - confirmed live: `step` state and the
              URL both flipped to the next screen instantly and correctly,
              but .story-player stayed mounted in the DOM indefinitely (mode
              ="wait": both old and new screen stuck in limbo; default mode:
              new screen mounted fine but the old one leaked forever). Taking
              this screen out of AnimatePresence's management entirely removes
              the dependency on that inner group ever settling - React's own
              reconciliation mounts/unmounts it immediately and correctly, the
              same way it always did before framer-motion was in the loop. */}
          {step === 'playing' && storyData && (
            <div className="player-container">
              {/* A short quiz is a note on a finished story, not a failure - it
                  must never use the error banner, which offers "Try Again" for
                  something that already succeeded. */}
              {quizNotice && (
                <div className="quiz-notice" role="status">
                  <AlertTriangle size={16} aria-hidden="true" />
                  <span>{quizNotice}</span>
                  <button
                    onClick={() => setQuizNotice(null)}
                    aria-label="Dismiss notice"
                  >&times;</button>
                </div>
              )}
              <StoryPlayer
                ref={storyPlayerRef}
                storyData={storyData}
                avatar={selectedAvatar}
                onRestart={handleRestart}
                onSave={storyFullyReady && !isSaved ? handleSaveStory : null}
                onDownloadOffline={storyFullyReady ? () => storyPlayerRef.current?.triggerDownload() : null}
                isSaved={isSaved}
                isOffline={isOfflineMode}
                savedStoryId={savedStoryId}
                currentJobId={currentJobId}
                totalScenes={totalScenes}
                completedSceneCount={completedSceneCount}
              />
            </div>
          )}

          {showSaveModal && (
            <SaveStoryModal
              jobId={currentJobId}
              onSave={handleSaveComplete}
              onCancel={() => setShowSaveModal(false)}
            />
          )}

          <AnimatePresence>
            {saveFeedback && (
              <SaveFeedbackModal
                variant={saveFeedback.variant}
                title={saveFeedback.title}
                message={saveFeedback.message}
                onDismiss={() => setSaveFeedback(null)}
              />
            )}
          </AnimatePresence>

          {showReuploadModal && (
            <ReuploadConfirmModal
              onConfirm={handleReuploadConfirm}
              onCancel={() => setShowReuploadModal(false)}
            />
          )}

          {showDuplicateModal && duplicateInfo && (
            <DuplicateStoryModal
              isOpen={showDuplicateModal}
              onClose={() => setShowDuplicateModal(false)}
              onLoadExisting={async () => {
                if (DEBUG) console.log('👀 onLoadExisting called')
                if (DEBUG) console.log('📚 duplicateInfo:', duplicateInfo)
                setShowDuplicateModal(false)
                // Load the existing story
                try {
                  if (DEBUG) console.log('🔄 Loading existing story:', duplicateInfo.story_id)
                  const response = await apiClient.get(`/api/story/${duplicateInfo.story_id}/status`)
                  if (DEBUG) console.log('📦 API Response:', response)
                  const storyStatus = response.data
                  if (DEBUG) console.log('📊 Story status:', storyStatus)
                  
                  if (!storyStatus) {
                    if (DEBUG) console.error('❌ Story status is null/undefined')
                    setError('Failed to load story: No data returned')
                    return
                  }
                  
                  if (storyStatus.status === 'completed') {
                    if (DEBUG) console.log('✅ Story is completed')
                    if (DEBUG) console.log('🎬 Scenes data:', storyStatus.scenes)
                    
                    if (!storyStatus.scenes || storyStatus.scenes.length === 0) {
                      if (DEBUG) console.error('❌ No scenes found in story')
                      setError('Story has no scenes')
                      return
                    }
                    
                    const quizData = storyStatus.quiz || []
                    const parsedQuiz = typeof quizData === 'string' ? (() => { try { return JSON.parse(quizData) } catch(e) { return [] } })() : quizData
                    const formattedStory = {
                      title: storyStatus.title || 'Saved Story',
                      scenes: storyStatus.scenes.map((scene, idx) => {
                        if (DEBUG) console.log(`🎬 Scene ${idx}:`, scene)
                        return {
                          id: idx,
                          text: scene.text || '',
                          imageUrl: scene.image_url || '',
                          audioUrl: scene.audio_url || ''
                        }
                      }),
                      quiz: parsedQuiz
                    }
                    
                    if (DEBUG) console.log('✅ Formatted story:', formattedStory)
                    if (DEBUG) console.log('🎨 Setting story data...')
                    
                    setStoryData(formattedStory)
                    setCurrentJobId(duplicateInfo.story_id)
                    setIsSaved(true)
                    setSavedStoryId(duplicateInfo.story_id)
                    setSelectedAvatar({ id: 'loaded', name: 'Saved Story' })
                    
                    if (DEBUG) console.log('🎬 Navigating to playing...')
                    navigateTo('playing')
                    
                    if (DEBUG) console.log('✅ Navigation complete, story should be visible')
                  } else {
                    if (DEBUG) console.warn('⚠️ Story not completed:', storyStatus.status)
                    setError(`Story is not ready: ${storyStatus.status}`)
                  }
                } catch (err) {
                  if (DEBUG) console.error('❌ Error loading duplicate story:', err)
                  if (DEBUG) console.error('Error response:', err.response)
                  if (DEBUG) console.error('Error data:', err.response?.data)
                  if (DEBUG) console.error('Error status:', err.response?.status)
                  setError('Failed to load existing story: ' + (err.response?.data?.detail || err.message))
                }
              }}
              onCreateNew={() => {
                setShowDuplicateModal(false)
                // Continue with normal upload flow but force new generation
                navigateTo('confirm')
              }}
              duplicateInfo={duplicateInfo}
            />
          )}

          <UpgradeModal
            isOpen={showUpgradeModal}
            onClose={() => setShowUpgradeModal(false)}
            onViewPlans={() => { setShowUpgradeModal(false); navigateTo('pricing') }}
            message={upgradeMessage}
          />

          {showUploadProgress && (
            <UploadProgressOverlay
              progress={uploadProgress}
              fileName={uploadFileName}
              isVisible={showUploadProgress}
            />
          )}

          {/* Hidden file input for re-upload */}
          <input
            ref={fileInputRef}
            type="file"
            id="file-reupload"
            name="file-reupload"
            accept=".pdf,.docx,.doc"
            onChange={handleFileInputChange}
            style={{ display: 'none' }}
          />
        </div>
      </main>

      {/* Update notification - lives OUTSIDE <main> on purpose. .app-main sets
          position:relative + z-index:1, which traps everything inside it in a
          stacking context below the z-index:100 sticky header, so no z-index
          value here (however large) could ever lift it above the header. */}
      {showUpdateNotification && (
        <motion.div
          initial={{ opacity: 0, y: -50 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -50 }}
          style={{
            position: 'fixed',
            top: 'calc(var(--app-header-h, 68px) + 12px)',
            left: '16px',
            right: '16px',
            marginLeft: 'auto',
            zIndex: 10000,
            background: 'linear-gradient(135deg, #8b5cf6 0%, #22d3ee 100%)',
            padding: '1rem 1.5rem',
            borderRadius: '12px',
            boxShadow: '0 8px 32px rgba(139, 92, 246, 0.35)',
            color: 'white',
            maxWidth: '400px',
            border: '1px solid rgba(255, 255, 255, 0.2)'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '0.75rem' }}>
            <RefreshCw size={22} aria-hidden="true" />
            <div>
              <h4 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 600 }}>Update Available</h4>
              <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.9rem', opacity: 0.9 }}>
                A new version is ready to install
              </p>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <button
              onClick={handleUpdateApp}
              style={{
                flex: 1,
                padding: '0.65rem 1.2rem',
                background: 'white',
                color: '#8b5cf6',
                border: 'none',
                borderRadius: '8px',
                fontWeight: 600,
                cursor: 'pointer',
                fontSize: '0.95rem'
              }}
            >
              Update Now
            </button>
            <button
              onClick={dismissUpdate}
              style={{
                padding: '0.65rem 1.2rem',
                background: 'rgba(255, 255, 255, 0.15)',
                color: 'white',
                border: '1px solid rgba(255, 255, 255, 0.3)',
                borderRadius: '8px',
                fontWeight: 600,
                cursor: 'pointer',
                fontSize: '0.95rem'
              }}
            >
              Later
            </button>
          </div>
        </motion.div>
      )}
    </div>
    </ErrorBoundary>
  )
}

export default App
