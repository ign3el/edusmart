import { useState, useEffect, useRef, lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { useAuth } from './context/AuthContext'
import apiClient from './services/api'
import updateService from './services/updateService'
import Login from './components/Login'
import Signup from './components/Signup'
import VerifyEmail from './components/VerifyEmail'
import ForgotPassword from './components/ForgotPassword'
import ResetPassword from './components/ResetPassword'
import FileUpload from './components/FileUpload'
import FileConfirmation from './components/FileConfirmation'
import AvatarSelector from './components/AvatarSelector'
import StoryPlayer from './components/StoryPlayer'
const Scene3DBackground = lazy(() => import('./components/3d/Scene3DBackground'));
import SaveStoryModal from './components/SaveStoryModal'
import LoadStory from './components/LoadStory'
import OfflineManager from './components/OfflineManager'
import UserProfile from './components/UserProfile'
import ReuploadConfirmModal from './components/ReuploadConfirmModal'
import UploadProgressOverlay from './components/UploadProgressOverlay'
import DuplicateStoryModal from './components/DuplicateStoryModal'
import TeacherCard from './components/TeacherCard'
import NavigationMenu from './components/NavigationMenu'
import StoryActionsBar from './components/StoryActionsBar'
import ErrorBoundary from './components/ErrorBoundary'
const AdminPanel = lazy(() => import('./components/AdminPanel'));
import './App.css'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function App() {
  // Use Routes to handle verification and password reset pages
  return (
    <Routes>
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/*" element={<MainApp />} />
    </Routes>
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
  const [speed, setSpeed] = useState(1.0);
  const [detectedLanguage, setDetectedLanguage] = useState('en');
  
  const [storyData, setStoryData] = useState(null)
  const storyPlayerRef = useRef(null);
  const [progress, setProgress] = useState(0)
  const [totalScenes, setTotalScenes] = useState(0) // Track total scenes from backend
  const [completedSceneCount, setCompletedSceneCount] = useState(0) // Track completed scenes
  const [error, setError] = useState(null)
  const [gradeLevel, setGradeLevel] = useState(3)
  const [currentJobId, setCurrentJobId] = useState(null)
  const [showSaveModal, setShowSaveModal] = useState(false)
  const [isSaved, setIsSaved] = useState(false)
  const [savedStoryId, setSavedStoryId] = useState(null)
  const [isOfflineMode, setIsOfflineMode] = useState(false)
  const [showReuploadModal, setShowReuploadModal] = useState(false)
  const [showDuplicateModal, setShowDuplicateModal] = useState(false)
  const [duplicateInfo, setDuplicateInfo] = useState(null)
  const [fileHash, setFileHash] = useState(null)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadFileName, setUploadFileName] = useState('')
  const [showUploadProgress, setShowUploadProgress] = useState(false)
  const fileInputRef = useRef(null)
  const [showUpdateNotification, setShowUpdateNotification] = useState(false)

  // Initialize version tracking and check for updates
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

  // If not logged in and not loading, show auth screen
  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)' }}>
        <div style={{ color: 'white', fontSize: '20px' }}>Loading...</div>
      </div>
    )
  }

  if (!isAuthenticated) {
    if (signupSuccess) {
      return (
        <div className="auth-container" style={{
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
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
      <div style={{
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
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
      if (DEBUG) console.log('✅ No duplicate, continuing upload')
    } catch (err) {
      if (DEBUG) console.error('❌ Error checking duplicate:', err)
      setShowUploadProgress(false)
      setError('Failed to check for duplicates: ' + err.message)
      return
    }
    
    // Real upload with XHR progress tracking
    if (DEBUG) console.log('📊 Starting real upload with progress tracking')
    const uploadData = new FormData()
    uploadData.append('file', file)
    uploadData.append('grade_level', gradeLevel)
    uploadData.append('voice', voice)
    uploadData.append('speed', speed)
    if (fileHashLocal) {
      uploadData.append('file_hash', fileHashLocal)
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
            // Start polling for job status
            const pollTimer = setInterval(async () => {
              try {
                const statusRes = await fetch(`${API_URL}/api/status/${jobId}`, {
                  headers: { 'Authorization': `Bearer ${localStorage.getItem('auth_token')}` }
                })
                if (!statusRes.ok) throw new Error('Could not fetch status')
                const job = await statusRes.json()
                if (job.status === 'processing') {
                  setProgress((prev) => Math.max(prev, job.progress ?? prev))
                  if (job.total_scenes > 0) {
                    setTotalScenes(job.total_scenes)
                    setCompletedSceneCount(job.completed_scene_count || 0)
                  }
                  if (job.result && job.result.scenes && job.result.scenes.length > 0) {
                    if (!storyData || storyData.scenes.length === 0) {
                      setStoryData(job.result)
                      navigateTo('playing')
                    } else {
                      setStoryData(job.result)
                    }
                  }
                } else if (job.status === 'completed') {
                  clearInterval(pollTimer)
                  setStoryData(job.result)
                  setProgress(100)
                  if (job.total_scenes > 0) {
                    setTotalScenes(job.total_scenes)
                    setCompletedSceneCount(job.total_scenes)
                  }
                  if (step !== 'playing') {
                    navigateTo('playing')
                  }
                } else if (job.status === 'failed') {
                  clearInterval(pollTimer)
                  throw new Error(job.error || 'AI Generation failed.')
                }
              } catch (err) {
                clearInterval(pollTimer)
                setError('Connection lost: ' + err.message)
                navigateTo('upload')
              }
            }, 2000)
          } catch (parseErr) {
            setError('Invalid response from server')
            navigateTo('upload')
          }
        } else {
          setError('Upload failed with status: ' + xhr.status)
          navigateTo('upload')
        }
      }, 500)
    })
    xhr.addEventListener('error', () => {
      setShowUploadProgress(false)
      setError('Upload failed due to network error')
      navigateTo('upload')
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
    if (settings) {
      setVoice(settings.voice);
      setSpeed(settings.speed);
    }
    // Check if this was after a duplicate detection and user wants to force new
    const forceNew = duplicateInfo !== null
    await generateStory('Professor Paws', forceNew) // Use default avatar
    // Clear duplicate info after using it
    setDuplicateInfo(null)
  }

  const handleAvatarSelect = async (avatar) => {
    setSelectedAvatar(avatar)
    // Check if this was after a duplicate detection and user wants to force new
    const forceNew = duplicateInfo !== null
    await generateStory(avatar, forceNew)
    // Clear duplicate info after using it
    if (forceNew) {
      setDuplicateInfo(null)
    }
  }

  const generateStory = async (avatar, forceNew = false) => {
    try {
      navigateTo('generating')
      setError(null)
      setProgress(0)
      setIsSaved(false)

      const formData = new FormData()
      formData.append('file', uploadedFile)
      formData.append('grade_level', gradeLevel)
      formData.append('avatar_type', avatar.id)
      // Append new Kokoro TTS settings
      formData.append('voice', voice)
      formData.append('speed', speed)
      // Append file hash and force_new flag
      if (fileHash) {
        formData.append('file_hash', fileHash)
      }
      formData.append('force_new', forceNew.toString())
      
      // Append user agent for mobile detection
      formData.append('user_agent', navigator.userAgent)

      // Use apiClient for automatic auth headers, assuming it's the default export from api.js
      const response = await fetch(`${API_URL}/api/upload`, { 
        method: 'POST', 
        body: formData,
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('auth_token')}`
        }
      })
      
      if (!response.ok) throw new Error("Failed to start story generation.")
      
      const { job_id } = await response.json()
      setCurrentJobId(job_id)

      // Polling Loop
      const pollTimer = setInterval(async () => {
        try {
          const statusRes = await fetch(`${API_URL}/api/status/${job_id}`, {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('auth_token')}` }
          })
          if (!statusRes.ok) throw new Error("Could not fetch status")
          
          const job = await statusRes.json()

          if (job.status === 'processing') {
            setProgress((prev) => Math.max(prev, job.progress ?? prev))
            
            // Update total scenes count when available
            if (job.total_scenes > 0) {
              setTotalScenes(job.total_scenes)
              setCompletedSceneCount(job.completed_scene_count || 0)
            }
            
            // Show story immediately when first scene is ready
            if (job.result && job.result.scenes && job.result.scenes.length > 0) {
              if (!storyData || storyData.scenes.length === 0) {
                // First scene is ready - show it immediately
                setStoryData(job.result)
                navigateTo('playing')
                // Don't clear interval - continue polling for remaining scenes
              } else {
                // Update story data with newly completed scenes
                setStoryData(job.result)
              }
            }
          } else if (job.status === 'completed') {
            clearInterval(pollTimer)
            setStoryData(job.result)
            setProgress(100)
            if (job.total_scenes > 0) {
              setTotalScenes(job.total_scenes)
              setCompletedSceneCount(job.total_scenes)
            }
            // Only navigate if not already playing
            if (step !== 'playing') {
              navigateTo('playing')
            }
          } else if (job.status === 'failed') {
            clearInterval(pollTimer)
            throw new Error(job.error || "AI Generation failed.")
          }
        } catch (err) {
          clearInterval(pollTimer)
          setError("Connection lost: " + err.message)
          navigateTo('upload')
        }
      }, 2000)

    } catch (err) {
      setError(err.message)
      navigateTo('upload')
    }
  }

  const handleRestart = async () => {
    // Cleanup unsaved story
    if (currentJobId && !isSaved) {
      try {
        await fetch(`/api/cleanup/${currentJobId}`, { method: 'DELETE' })
      } catch (error) {
        if (DEBUG) console.error('Cleanup failed:', error)
      }
    }
    
    navigateTo('home')
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
  }

  const handleSaveStory = () => {
    setShowSaveModal(true)
  }

  const handleSaveComplete = async (storyId, storyName) => {
    setShowSaveModal(false)
    setIsSaved(true)
    setSavedStoryId(storyId)
    alert(`✅ Story "${storyName}" saved successfully!`)
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
      alert(`✅ Story "${storyName}" saved locally!`)
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
    <div className="app">
      <header className="app-header">
        <div className="app-header-content">
          <h1>EduSmart</h1>
          <p className="header-subtitle">AI-Powered Storymaker</p>
        </div>
        {isAuthenticated && (
          <NavigationMenu
            user={user}
            isAdmin={user?.is_admin}
            onHome={() => navigateTo('home')}
            onNewStory={handleRestart}
            onLoadStories={() => navigateTo('load')}
            onOfflineManager={() => navigateTo('offline')}
            onAdminClick={() => navigateTo('admin')}
            onProfile={() => navigateTo('profile')}
            onLogout={logout}
            onSaveStory={step === 'playing' && !isSaved ? handleSaveStory : null}
            onDownloadStory={step === 'playing' ? () => storyPlayerRef.current?.triggerDownload() : null}
            isPlayingStory={step === 'playing'}
          />
        )}
      </header>

      <main className="app-main">
        {step !== 'playing' && (
          <ErrorBoundary fallback={<div className="bg-fallback" />}>
            <Suspense fallback={null}>
              <Scene3DBackground className="global-3d-background" />
            </Suspense>
          </ErrorBoundary>
        )}
        <div className="content-shell">
          {error && (
            <div className="error-message">
              <p>⚠️ {error}</p>
              <button onClick={() => setError(null)}>Try Again</button>
            </div>
          )}
          
          <AnimatePresence mode="wait">
            {step === 'admin' && (
              <Suspense fallback={<div className="loading-message">Loading Admin Panel...</div>}>
                <motion.div key="admin-panel" className="step-container">
                  <AdminPanel 
                    onPlayStory={handlePlayStoryFromAdmin}
                    onBack={() => navigateTo('home')}
                  />
                </motion.div>
              </Suspense>
            )}

            {step === 'home' && (
              <motion.div key="home" className="home-wrapper">
                <div className="home-content-overlay">
                  <div className="home-pill">✨ AI-Powered Storymaker</div>
                  <h1 className="home-title">Turn Lessons into Adventures</h1>
                  <p className="home-subtitle">Upload a PDF, choose your grade level, and let AI create an immersive story with custom images and voiceovers.</p>
                  <div className="home-buttons">
                    <motion.button 
                      className="home-btn"
                      onClick={() => navigateTo('upload')}
                      whileHover={{ scale: 1.02 }}
                      whileTap={{ scale: 0.98 }}
                    >
                      <div className="emoji">✨</div>
                      <strong>Create New Story</strong>
                      <span>Upload a lesson file and let AI turn it into a story</span>
                    </motion.button>
                    <motion.button 
                      className="home-btn"
                      onClick={() => navigateTo('load')}
                      whileHover={{ scale: 1.02 }}
                      whileTap={{ scale: 0.98 }}
                    >
                      <div className="emoji">📚</div>
                      <strong>Load Online Story</strong>
                      <span>Pull down a saved adventure from the cloud</span>
                    </motion.button>
                    <motion.button 
                      className="home-btn"
                      onClick={() => navigateTo('offline')}
                      whileHover={{ scale: 1.02 }}
                      whileTap={{ scale: 0.98 }}
                    >
                      <div className="emoji">📱</div>
                      <strong>Offline Manager</strong>
                      <span>Manage locally stored stories without an internet connection</span>
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
                onLogout={logout}
              />
            </motion.div>
          )}

          {step === 'generating' && (
            <motion.div key="generating" className="generating-container">
              <div className="loading-spinner" />
              <h2>Creating Your Story...</h2>
              <div className="progress-container">
                <div className="progress-bar-bg">
                  <motion.div 
                    className="progress-bar-fill"
                    initial={{ width: 0 }}
                    animate={{ width: `${progress}%` }}
                    transition={{ duration: 0.5 }}
                  />
                </div>
                <p>{progress}% Complete</p>
                {totalScenes > 0 && (
                  <p className="scene-progress">
                    {completedSceneCount} of {totalScenes} scenes ready
                  </p>
                )}
              </div>
              <p className="small-text">Generating custom images and voiceovers...</p>
            </motion.div>
          )}

          {step === 'playing' && storyData && (
            <motion.div key="playing" className="player-container">
              <StoryPlayer
                ref={storyPlayerRef}
                storyData={storyData} 
                avatar={selectedAvatar} 
                onRestart={handleRestart}
                onSave={!isSaved ? handleSaveStory : null}
                onDownloadOffline={() => storyPlayerRef.current?.triggerDownload()}
                isSaved={isSaved}
                isOffline={isOfflineMode}
                savedStoryId={savedStoryId}
                currentJobId={currentJobId}
                totalScenes={totalScenes}
                completedSceneCount={completedSceneCount}
              />
            </motion.div>
          )}
        </AnimatePresence>

          {showSaveModal && (
            <SaveStoryModal 
              jobId={currentJobId}
              onSave={handleSaveComplete}
              onCancel={() => setShowSaveModal(false)}
            />
          )}

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
                      quiz: storyStatus.quiz || []
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

          {showUploadProgress && (
            <UploadProgressOverlay
              progress={uploadProgress}
              fileName={uploadFileName}
              isVisible={showUploadProgress}
            />
          )}

          {/* Update notification */}
          {showUpdateNotification && (
            <motion.div
              initial={{ opacity: 0, y: -50 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -50 }}
              style={{
                position: 'fixed',
                top: '20px',
                right: '20px',
                zIndex: 10000,
                background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                padding: '1rem 1.5rem',
                borderRadius: '12px',
                boxShadow: '0 8px 32px rgba(0, 0, 0, 0.3)',
                color: 'white',
                maxWidth: '400px',
                border: '1px solid rgba(255, 255, 255, 0.2)'
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '0.75rem' }}>
                <span style={{ fontSize: '1.5rem' }}>🔄</span>
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
                    color: '#667eea',
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
    </div>
    </ErrorBoundary>
  )
}

export default App
