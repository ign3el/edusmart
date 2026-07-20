import { useState, useEffect, useRef, forwardRef, useImperativeHandle, lazy, Suspense } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { animate } from 'animejs'
import {
  Play, Pause, SkipForward, SkipBack, RotateCw, Menu as MenuIcon, X,
  BookOpen, Download, Save, Volume2, ImageOff, Loader2
} from 'lucide-react'
import { buildFullUrl } from '../utils/urlHelpers'
import Quiz from './Quiz'
import './StoryPlayer.css'

const StoryScene3DLayer = lazy(() => import('./StoryScene3DLayer'))

const API_URL = import.meta.env.VITE_API_URL || ''

function hasWebGL() {
  if (typeof document === 'undefined') return false
  try {
    const canvas = document.createElement('canvas')
    return !!(canvas.getContext('webgl') || canvas.getContext('experimental-webgl'))
  } catch {
    return false
  }
}

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
  initialScene = 0,
}, ref) => {
  const [currentScene, setCurrentScene] = useState(initialScene)
  const [isPlaying, setIsPlaying] = useState(false)
  const [showQuiz, setShowQuiz] = useState(false)
  const [showActionMenu, setShowActionMenu] = useState(false)
  const [progress, setProgress] = useState(0)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [imageLoaded, setImageLoaded] = useState(false)
  const [imageError, setImageError] = useState(false)
  const [audioError, setAudioError] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)
  const [downloadMessage, setDownloadMessage] = useState('')
  const [generatingMessage, setGeneratingMessage] = useState('')
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' && window.innerWidth < 768
  )
  const [supports3D] = useState(() =>
    typeof window !== 'undefined' &&
    !window.matchMedia('(prefers-reduced-motion: reduce)').matches &&
    hasWebGL()
  )
  const userPausedRef = useRef(false)
  const savedTimeRef = useRef(0)
  const audioRef = useRef(null)
  const lastUpdateRef = useRef(0)
  const prevImageUrlRef = useRef(null)
  const lastImageUrlRef = useRef(null)
  const pointerTiltRef = useRef({ x: 0, y: 0 })
  const playGlowRef = useRef(null)
  const pendingAdvanceRef = useRef(false)
  const prefetchedUrlsRef = useRef(new Set())
  const playbackRateRef = useRef(1)
  const [playbackRate, setPlaybackRate] = useState(1)

  const scenes = storyData?.scenes || []
  // Use max of what backend promised vs what we actually received
  const actualTotal = Math.max(totalScenes, scenes.length)
  const scene = scenes[currentScene]

  // URLs
  const fullImageUrl = buildFullUrl(scene?.image_url)
  const fullAudioUrl = buildFullUrl(scene?.audio_url)

  const show3D = supports3D && !imageError

  // Expose download trigger
  useImperativeHandle(ref, () => ({
    triggerDownload: () => handleOfflineDownload(),
    setGeneratingMessage: (msg) => setGeneratingMessage(msg),
    // No-op for backward compat
  }))

  // Reset on scene change
  useEffect(() => {
    prevImageUrlRef.current = lastImageUrlRef.current
    lastImageUrlRef.current = fullImageUrl

    setProgress(0)
    setCurrentTime(0)
    setDuration(0)
    savedTimeRef.current = 0
    setImageLoaded(false)
    setImageError(false)
    setAudioError(false)
    setGeneratingMessage('')  // Clear "Preparing next scene..." when new scene loads
    userPausedRef.current = false
    pendingAdvanceRef.current = false

    // Preload image
    if (fullImageUrl) {
      const img = new Image()
      img.onload = () => setImageLoaded(true)
      img.onerror = () => setImageError(true)
      img.src = fullImageUrl
    }

    // Reset audio and auto-play when ready
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.currentTime = 0
      audioRef.current.src = fullAudioUrl || ''
      audioRef.current.load()
      audioRef.current.oncanplaythrough = () => {
        setGeneratingMessage('') // Clear waiting message once audio loads
        setAudioError(false)
        audioRef.current.playbackRate = playbackRateRef.current
        audioRef.current.play().catch(() => {
          setAudioError(true)
          setIsPlaying(false)
        })
      }
    }
  }, [currentScene, fullImageUrl, fullAudioUrl])

  // Warm the browser cache for the next scene's image/audio as soon as it's ready,
  // instead of waiting for the moment playback actually reaches it (the "Reset on
  // scene change" effect above only starts fetching once currentScene changes).
  // Backend generation regularly finishes a scene's assets well before playback
  // gets there - without this, every transition pays a fresh network round-trip
  // it didn't need to, which is most of what reads as "waiting" once generation
  // itself is fast.
  useEffect(() => {
    const nextScene = scenes[currentScene + 1]
    if (!nextScene) return

    const nextImageUrl = buildFullUrl(nextScene.image_url)
    if (nextImageUrl && !prefetchedUrlsRef.current.has(nextImageUrl)) {
      prefetchedUrlsRef.current.add(nextImageUrl)
      const img = new Image()
      img.src = nextImageUrl
    }

    const nextAudioUrl = buildFullUrl(nextScene.audio_url)
    if (nextAudioUrl && !prefetchedUrlsRef.current.has(nextAudioUrl)) {
      prefetchedUrlsRef.current.add(nextAudioUrl)
      const audio = new Audio()
      audio.preload = 'auto'
      audio.src = nextAudioUrl
      audio.load()
    }
  }, [currentScene, scenes.length])

  // Quiz modal opens
  useEffect(() => {
    if (showQuiz && audioRef.current) {
      audioRef.current.pause()
      setIsPlaying(false)
    }
  }, [showQuiz])

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  // Play button breathing glow — anime.js rAF loop (Framer Motion loops have
  // been seen to stall on the user's phone, anime.js doesn't).
  useEffect(() => {
    if (!playGlowRef.current) return
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (prefersReducedMotion || !isPlaying) {
      playGlowRef.current.style.opacity = 0
      return
    }
    const animation = animate(playGlowRef.current, {
      opacity: [0.35, 0.95, 0.35],
      scale: [0.92, 1.22, 0.92],
      duration: 1800,
      easing: 'easeInOutSine',
      loop: true,
    })
    return () => animation.pause()
  }, [isPlaying])

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
      setIsPlaying(false)

      // Use scenes.length (what we actually have) not actualTotal (what backend promised)
      if (currentScene < scenes.length - 1) {
        setGeneratingMessage('Preparing next scene...')
        setCurrentScene(s => s + 1)
      } else if (scenes.length < actualTotal) {
        // Next scene isn't generated yet. Don't guess with a fixed-delay timer -
        // it used to check `scenes.length` 5s later from a closure captured right
        // now, which never saw scenes that arrived via polling in the meantime, so
        // it got stuck re-checking the same stale count forever ("scene 2 doesn't
        // work", "doesn't auto move on completion"). Instead just flag that we're
        // waiting; the effect below watches storyData/scenes.length directly and
        // advances the instant a real new scene shows up, however long that takes.
        pendingAdvanceRef.current = true
        setGeneratingMessage(`Waiting for remaining scenes... (${scenes.length}/${actualTotal} ready)`)
      } else {
        setGeneratingMessage('')
        setShowQuiz(true)
      }
    }
    const handleError = () => {
      // Don't show error if we're still generating scenes - it's expected
      if (fullAudioUrl) {
        setAudioError(true)
      }
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
  }, [currentScene, actualTotal])

  // Fires whenever a fresh scene actually lands (storyData/scenes.length changes,
  // driven by App.jsx's polling), not on a fixed timer - so playback picks the
  // next scene up the moment it's real, whether that's 2 seconds or 2 minutes
  // after we started waiting, and reliably catches up once the full story
  // finishes even if we'd already been sitting on the "waiting" screen a while.
  useEffect(() => {
    if (!pendingAdvanceRef.current) return
    if (scenes.length > currentScene + 1) {
      pendingAdvanceRef.current = false
      setGeneratingMessage('Preparing next scene...')
      setCurrentScene(s => s + 1)
    } else if (scenes.length >= actualTotal) {
      pendingAdvanceRef.current = false
      setGeneratingMessage('')
      setShowQuiz(true)
    }
  }, [scenes.length, actualTotal, currentScene])

  const togglePlay = () => {
    if (!audioRef.current) return
    if (audioRef.current.paused) {
      if (audioError) setAudioError(false)
      const targetTime = savedTimeRef.current > 0 ? savedTimeRef.current : 0
      audioRef.current.currentTime = targetTime
      audioRef.current.play().catch(() => {
        setAudioError(true)
        setIsPlaying(false)
      })
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
    // isPlaying is no longer set optimistically here - the real 'play' event
    // (wired below) is the single source of truth, so the button can't get
    // stuck showing Pause when playback actually failed to start (blocked
    // autoplay, network hiccup, etc.) without anything ever rolling it back.
    setCurrentScene(idx)
  }

  const cyclePlaybackRate = () => {
    const rates = [1, 1.25, 1.5]
    const next = rates[(rates.indexOf(playbackRateRef.current) + 1) % rates.length]
    playbackRateRef.current = next
    setPlaybackRate(next)
    if (audioRef.current) audioRef.current.playbackRate = next
  }

  const formatTime = (t) => {
    if (!t || isNaN(t)) return '0:00'
    return `${Math.floor(t / 60)}:${Math.floor(t % 60).toString().padStart(2, '0')}`
  }

  const handlePointerMove = (e) => {
    if (e.pointerType && e.pointerType !== 'mouse') return
    const rect = e.currentTarget.getBoundingClientRect()
    pointerTiltRef.current.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
    pointerTiltRef.current.y = ((e.clientY - rect.top) / rect.height) * 2 - 1
  }

  const handlePointerLeave = () => {
    pointerTiltRef.current.x = 0
    pointerTiltRef.current.y = 0
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
          setDownloadMessage('Saved offline!')
        } catch { setDownloadMessage('Failed') }
        setTimeout(() => setDownloadMessage(''), 3000)
      }
      return
    }
    setIsDownloading(true)
    setDownloadMessage('Preparing...')
    try {
      const res = await fetch(
        savedStoryId
          ? `${API_URL}/api/export-story/${savedStoryId}`
          : `${API_URL}/api/export-job/${exportId}`,
        { headers: { Authorization: `Bearer ${localStorage.getItem('auth_token')}` } }
      )
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `${storyData?.title || 'story'}-${exportId.slice(0, 8)}.zip`
      document.body.appendChild(a); a.click(); document.body.removeChild(a)
      URL.revokeObjectURL(url)
      setDownloadMessage('Downloaded!')
    } catch { setDownloadMessage('Failed') }
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
            {audioError && <span><Volume2 size={16} aria-hidden="true" /> Audio unavailable <button className="err-btn" onClick={() => { setAudioError(false); savedTimeRef.current = 0 }}>Retry</button></span>}
            {imageError && <span><ImageOff size={16} aria-hidden="true" /> Image failed to load</span>}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Header */}
      <header className="player-header">
        <button className="icon-btn" onClick={onRestart} aria-label="Restart"><RotateCw size={18} /></button>
        <div className="header-title-area">
          <h1 className="story-title">{storyData?.title || 'Untitled Story'}</h1>
          <span className="scene-badge">
            Scene {currentScene + 1} of {actualTotal}
            {scenes.length < actualTotal && (
              <span className="scenes-ready-chip">
                <Loader2 size={11} className="scenes-ready-spinner" aria-hidden="true" />
                {scenes.length}/{actualTotal} ready
              </span>
            )}
          </span>
        </div>
        <button className="icon-btn" onClick={() => setShowActionMenu(!showActionMenu)} aria-label="Menu">
          {showActionMenu ? <X size={18} /> : <MenuIcon size={18} />}
        </button>
      </header>

      {/* Action menu */}
      <AnimatePresence>
        {showActionMenu && (
          <motion.div className="action-menu" initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}>
            <button className="action-item" onClick={() => { setShowActionMenu(false); onRestart() }}><RotateCw size={16} /> Restart</button>
            <button className="action-item" onClick={() => { setShowActionMenu(false); setShowQuiz(true) }}><BookOpen size={16} /> Quiz</button>
            {onSave && !isSaved && (
              <button className="action-item" onClick={() => { setShowActionMenu(false); onSave() }}><Save size={16} /> Save</button>
            )}
            <button className="action-item" onClick={() => { setShowActionMenu(false); handleOfflineDownload() }}><Download size={16} /> Download</button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main content */}
      <main className="player-content">
        {/* Scene image */}
        <div
          className="scene-image-container"
          onPointerMove={handlePointerMove}
          onPointerLeave={handlePointerLeave}
        >
          {show3D && (
            <Suspense fallback={null}>
              <StoryScene3DLayer
                imageUrl={fullImageUrl}
                prevImageUrl={prevImageUrlRef.current}
                isMobile={isMobile}
                isPlaying={isPlaying}
                sceneIndex={currentScene}
                pointerTiltRef={pointerTiltRef}
              />
            </Suspense>
          )}
          {!imageLoaded && !imageError && (
            <div className="image-skeleton">
              <div className="skeleton-pulse" />
              <span><BookOpen size={18} aria-hidden="true" /> Scene {currentScene + 1}</span>
            </div>
          )}
          {imageError && (
            <div className="image-fallback">
              <BookOpen size={28} aria-hidden="true" />
              <span>Scene {currentScene + 1}</span>
            </div>
          )}
          {!show3D && fullImageUrl && (
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
            {Array.from({length: actualTotal}, (_, i) => (
              <button
                key={i}
                className={`dot ${i === currentScene ? 'active' : ''} ${i >= scenes.length ? 'pending' : ''}`}
                onClick={() => i < scenes.length && goToScene(i)}
                aria-label={`Scene ${i + 1}${i >= scenes.length ? ' (generating)' : ''}`}
                disabled={i >= scenes.length}
              >
                {i === currentScene && (
                  <motion.span
                    className="dot-active-glow"
                    layoutId="active-dot-glow"
                    transition={{ type: 'spring', stiffness: 500, damping: 32 }}
                  />
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Narration text */}
        <div className="narration-area">
          {scene?.text ? (
            <motion.p
              key={currentScene}
              className="narration-text"
              initial={{ opacity: 0, y: 15, filter: 'blur(4px)' }}
              animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
              transition={{ duration: 0.45, ease: 'easeOut' }}
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
            <SkipBack size={22} />
          </button>
          <button className={`ctrl-btn play-btn ${isPlaying ? 'is-playing' : ''}`} onClick={togglePlay} aria-label={isPlaying ? 'Pause' : 'Play story'}>
            <span ref={playGlowRef} className="play-btn-glow" aria-hidden="true" />
            {isPlaying ? <Pause size={26} /> : <Play size={26} />}
          </button>
          <button className="ctrl-btn" onClick={() => goToScene(Math.min(scenes.length - 1, currentScene + 1))} disabled={currentScene >= scenes.length - 1 && scenes.length >= actualTotal} aria-label="Next scene">
            <SkipForward size={22} />
          </button>
          <button className="ctrl-btn speed-btn" onClick={cyclePlaybackRate} aria-label={`Playback speed ${playbackRate}x, tap to change`}>
            {playbackRate}x
          </button>
        </div>

        {/* Quiz button */}
        <button className="quiz-btn" onClick={() => { setIsPlaying(false); setShowQuiz(true) }}>
          <BookOpen size={18} /> Take Quiz
        </button>
      </main>

      {/* Quiz modal */}
      <AnimatePresence>
        {showQuiz && (
          <motion.div className="quiz-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Quiz
              questions={storyData?.quiz || []}
              storyId={savedStoryId || currentJobId}
              onClose={() => setShowQuiz(false)}
              onBackToStory={() => setShowQuiz(false)}
              onComplete={onRestart}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
})

StoryPlayer.displayName = 'StoryPlayer'
export default StoryPlayer
