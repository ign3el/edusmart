import { useState, useEffect, useRef, forwardRef, useImperativeHandle } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { FiPlay, FiPause, FiSkipForward, FiSkipBack, FiRotateCw, FiMenu, FiX, FiBookOpen, FiDownload, FiSave, FiChevronLeft, FiChevronRight } from 'react-icons/fi'
import { buildFullUrl } from '../utils/urlHelpers'
import Quiz from './Quiz'
import './StoryPlayer.css'

const API_URL = import.meta.env.VITE_API_URL || ''

const StoryPlayer = forwardRef(({
  storyData,
  avatar,
  onRestart,
  onSave,
  onDownloadOffline,
  isSaved = false,
  isOffline = false,
  savedStoryId = null,
  currentJobId = null,
  totalScenes = 0,
  completedSceneCount = 0,
}, ref) => {
  const [currentScene, setCurrentScene] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [showQuiz, setShowQuiz] = useState(false)
  const [progress, setProgress] = useState(0)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [showActionMenu, setShowActionMenu] = useState(false)
  const [imageLoaded, setImageLoaded] = useState(false)
  const [imageError, setImageError] = useState(false)
  const [audioError, setAudioError] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)
  const [downloadMessage, setDownloadMessage] = useState('')
  const [generatingMessage, setGeneratingMessage] = useState('')
  const userPausedRef = useRef(false)
  const savedTimeRef = useRef(0)
  const audioRef = useRef(null)
  const lastUpdateRef = useRef(0)

  const scenes = storyData?.scenes || []
  const actualTotal = totalScenes > 0 ? totalScenes : scenes.length
  const scene = scenes[currentScene]

  // URLs
  const fullImageUrl = buildFullUrl(scene?.image_url)
  const fullAudioUrl = buildFullUrl(scene?.audio_url)

  // Expose download trigger
  useImperativeHandle(ref, () => ({
    triggerDownload: () => handleOfflineDownload(),
  }))

  // Reset on scene change
  useEffect(() => {
    setProgress(0)
    setCurrentTime(0)
    setDuration(0)
    savedTimeRef.current = 0
    setImageLoaded(false)
    setImageError(false)
    setAudioError(false)
    userPausedRef.current = false

    // Preload image
    if (fullImageUrl) {
      const img = new Image()
      img.onload = () => setImageLoaded(true)
      img.onerror = () => setImageError(true)
      img.src = fullImageUrl
    }

    // Reset audio
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.currentTime = 0
      audioRef.current.src = fullAudioUrl || ''
      audioRef.current.load()
    }
  }, [currentScene, fullImageUrl, fullAudioUrl])

  // Audio event handlers
  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return

    const handlePlay = () => {
      userPausedRef.current = false
      setIsPlaying(true)
    }
    const handlePause = () => {
      if (audio.currentTime > 0) savedTimeRef.current = audio.currentTime
    }
    const handleEnded = () => {
      savedTimeRef.current = 0
      userPausedRef.current = false
      // Auto-advance
      if (currentScene < scenes.length - 1) {
        setCurrentScene(s => s + 1)
        setIsPlaying(true)
      } else {
        setIsPlaying(false)
        setShowQuiz(true)
      }
    }
    const handleError = () => {
      setAudioError(true)
      setIsPlaying(false)
    }

    audio.addEventListener('play', handlePlay)
    audio.addEventListener('pause', handlePause)
    audio.addEventListener('ended', handleEnded)
    audio.addEventListener('error', handleError)
    return () => {
      audio.removeEventListener('play', handlePlay)
      audio.removeEventListener('pause', handlePause)
      audio.removeEventListener('ended', handleEnded)
      audio.removeEventListener('error', handleError)
    }
  }, [currentScene, scenes.length])

  const togglePlay = () => {
    if (!audioRef.current) return
    if (audioRef.current.paused) {
      if (audioError) setAudioError(false)
      const targetTime = savedTimeRef.current > 0 ? savedTimeRef.current : 0
      audioRef.current.currentTime = targetTime
      audioRef.current.play().catch(() => setAudioError(true))
    } else {
      savedTimeRef.current = audioRef.current.currentTime
      audioRef.current.pause()
      userPausedRef.current = true
      setIsPlaying(false)
    }
  }

  const handleSeek = (e) => {
    if (!audioRef.current?.duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    const clientX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    audioRef.current.currentTime = ratio * audioRef.current.duration
  }

  const handleTimeUpdate = () => {
    const now = performance.now()
    if (now - lastUpdateRef.current < 250) return
    lastUpdateRef.current = now
    const a = audioRef.current
    if (a?.duration) {
      setProgress((a.currentTime / a.duration) * 100)
      setCurrentTime(a.currentTime)
      setDuration(a.duration)
    }
  }

  const goToScene = (idx) => {
    if (audioRef.current) audioRef.current.pause()
    setGeneratingMessage('')
    setIsPlaying(true)
    setCurrentScene(idx)
  }

  const formatTime = (t) => {
    if (!t || isNaN(t)) return '0:00'
    return `${Math.floor(t / 60)}:${Math.floor(t % 60).toString().padStart(2, '0')}`
  }

  const handleOfflineDownload = async () => {
    const exportId = savedStoryId || currentJobId
    if (!exportId) {
      const name = prompt('Story name:', storyData?.title || 'My Story')
      if (name?.trim()) {
        try {
          localStorage.setItem(`edusmart_story_${Date.now()}`, JSON.stringify({
            id: `local_${Date.now()}`, name: name.trim(), storyData, savedAt: Date.now(), isOffline: true,
          }))
          setDownloadMessage('✅ Saved offline!')
        } catch { setDownloadMessage('❌ Failed') }
        setTimeout(() => setDownloadMessage(''), 3000)
      }
      return
    }
    setIsDownloading(true)
    setDownloadMessage('Preparing...')
    try {
      const res = await fetch(`${API_URL}/api/export/${exportId}`, {
        headers: { Authorization: `Bearer ${localStorage.getItem('auth_token')}` },
      })
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `${storyData?.title || 'story'}-${exportId.slice(0, 8)}.zip`
      document.body.appendChild(a); a.click(); document.body.removeChild(a)
      URL.revokeObjectURL(url)
      setDownloadMessage('✅ Downloaded!')
    } catch { setDownloadMessage('❌ Failed') }
    setIsDownloading(false)
    setTimeout(() => setDownloadMessage(''), 3000)
  }

  return (
    <div className="story-player">
      {/* Background gradient */}
      <div className="player-bg" />

      {/* Audio element */}
      <audio ref={audioRef} onTimeUpdate={handleTimeUpdate} preload="metadata" />

      {/* Floating messages */}
      {downloadMessage && (
        <motion.div className="player-toast" initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
          {downloadMessage}
        </motion.div>
      )}
      {generatingMessage && (
        <motion.div className="player-toast generating" initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
          {generatingMessage}
        </motion.div>
      )}

      {/* Error banners */}
      <AnimatePresence>
        {(audioError || imageError) && (
          <motion.div className="player-error" initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -20 }}>
            {audioError && <span>🔊 Audio unavailable <button className="err-btn" onClick={() => { setAudioError(false); savedTimeRef.current = 0 }}>Retry</button></span>}
            {imageError && <span>🖼️ Image failed to load</span>}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Header */}
      <header className="player-header">
        <button className="icon-btn" onClick={onRestart} aria-label="Restart"><FiRotateCw size={18} /></button>
        <div className="header-title-area">
          <h1 className="story-title">{storyData?.title || 'Untitled Story'}</h1>
          <span className="scene-badge">Scene {currentScene + 1} of {actualTotal}</span>
        </div>
        <button className="icon-btn" onClick={() => setShowActionMenu(!showActionMenu)} aria-label="Menu">
          {showActionMenu ? <FiX size={18} /> : <FiMenu size={18} />}
        </button>
      </header>

      {/* Action menu */}
      <AnimatePresence>
        {showActionMenu && (
          <motion.div className="action-menu" initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}>
            <button className="action-item" onClick={() => { setShowActionMenu(false); onRestart() }}><FiRotateCw size={16} /> Restart</button>
            <button className="action-item" onClick={() => { setShowActionMenu(false); setShowQuiz(true) }}><FiBookOpen size={16} /> Quiz</button>
            {onSave && !isSaved && (
              <button className="action-item" onClick={() => { setShowActionMenu(false); onSave() }}><FiSave size={16} /> Save</button>
            )}
            <button className="action-item" onClick={() => { setShowActionMenu(false); handleOfflineDownload() }}><FiDownload size={16} /> Download</button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main content */}
      <main className="player-content">
        {/* Scene image */}
        <div className="scene-image-container">
          {!imageLoaded && !imageError && (
            <div className="image-skeleton">
              <div className="skeleton-pulse" />
              <span>📖 Scene {currentScene + 1}</span>
            </div>
          )}
          {imageError && (
            <div className="image-fallback">
              <span>📖</span>
              <span>Scene {currentScene + 1}</span>
            </div>
          )}
          {fullImageUrl && (
            <img
              src={fullImageUrl}
              alt={`Scene ${currentScene + 1}`}
              className={`scene-image ${imageLoaded ? 'loaded' : ''}`}
              onLoad={() => setImageLoaded(true)}
              onError={() => setImageError(true)}
            />
          )}
          {/* Scene dots overlay */}
          <div className="scene-dots-overlay">
            {scenes.map((_, i) => (
              <button
                key={i}
                className={`dot ${i === currentScene ? 'active' : ''}`}
                onClick={() => goToScene(i)}
                aria-label={`Scene ${i + 1}`}
              />
            ))}
          </div>
        </div>

        {/* Narration text */}
        <div className="narration-area">
          {scene?.text ? (
            <motion.p
              key={currentScene}
              className="narration-text"
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4 }}
            >
              {scene.text}
            </motion.p>
          ) : (
            <p className="narration-empty">Scene content loading...</p>
          )}
        </div>

        {/* Audio progress */}
        <div className="audio-row">
          <span className="time">{formatTime(currentTime)}</span>
          <div className="progress-track" onClick={handleSeek} onTouchStart={handleSeek}>
            <motion.div className="progress-fill" style={{ width: `${progress}%` }} />
            <div className="progress-thumb" style={{ left: `${progress}%` }} />
          </div>
          <span className="time">{formatTime(duration)}</span>
        </div>

        {/* Controls */}
        <div className="controls">
          <button className="ctrl-btn" onClick={() => goToScene(Math.max(0, currentScene - 1))} disabled={currentScene === 0} aria-label="Previous scene">
            <FiSkipBack size={22} />
          </button>
          <button className={`ctrl-btn play-btn ${isPlaying ? 'is-playing' : ''}`} onClick={togglePlay} aria-label={isPlaying ? 'Pause' : 'Play story'}>
            {isPlaying ? <FiPause size={26} /> : <FiPlay size={26} />}
          </button>
          <button className="ctrl-btn" onClick={() => goToScene(Math.min(scenes.length - 1, currentScene + 1))} disabled={currentScene >= scenes.length - 1} aria-label="Next scene">
            <FiSkipForward size={22} />
          </button>
        </div>

        {/* Quiz button */}
        <button className="quiz-btn" onClick={() => { setIsPlaying(false); setShowQuiz(true) }}>
          <FiBookOpen size={18} /> Take Quiz
        </button>
      </main>

      {/* Quiz modal */}
      <AnimatePresence>
        {showQuiz && (
          <motion.div className="quiz-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Quiz quiz={storyData?.quiz || []} onClose={() => setShowQuiz(false)} onRestart={onRestart} storyTitle={storyData?.title} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
})

StoryPlayer.displayName = 'StoryPlayer'
export default StoryPlayer
