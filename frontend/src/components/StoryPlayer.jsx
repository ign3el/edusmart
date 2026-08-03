import { useState, useEffect, useRef, forwardRef, useImperativeHandle, lazy, Suspense } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { animate } from 'animejs'
import {
  Play, Pause, SkipForward, SkipBack, RotateCw, Home,
  BookOpen, Volume2, ImageOff, Loader2
} from 'lucide-react'
import { buildFullUrl } from '../utils/urlHelpers'
import Quiz from './Quiz'
import './StoryPlayer.css'

const StoryScene3DLayer = lazy(() => import('./StoryScene3DLayer'))

const API_URL = import.meta.env.VITE_API_URL || ''

/* A real page turn, not a crossfade: the outgoing page swings away around the
   spine while the next one swings in from the opposite edge. transformOrigin is
   set per-element (not here) because it is a plain style, not an animatable
   value - Framer would ignore it inside a variant. */
const PAGE_TURN = {
  enter: (dir) => ({
    rotateY: dir > 0 ? 68 : -68,
    x: dir > 0 ? '30%' : '-30%',
    opacity: 0,
  }),
  center: { rotateY: 0, x: '0%', opacity: 1 },
  exit: (dir) => ({
    rotateY: dir > 0 ? -68 : 68,
    x: dir > 0 ? '-30%' : '30%',
    opacity: 0,
  }),
}

const PAGE_TURN_TRANSITION = { duration: 0.5, ease: [0.36, 0.06, 0.2, 1] }

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
  onHome,
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
  // canplaythrough fires again after every re-buffer on mobile Chrome. This
  // marks "this scene has already had its one automatic start" so a re-fire
  // can't restart audio the user deliberately paused.
  const autoPlayedRef = useRef(false)
  const seekingRef = useRef(false)
  const savedTimeRef = useRef(0)
  const audioRef = useRef(null)
  const lastUpdateRef = useRef(0)
  const prevImageUrlRef = useRef(null)
  const lastImageUrlRef = useRef(null)
  const pointerTiltRef = useRef({ x: 0, y: 0 })
  const playGlowRef = useRef(null)
  const pendingAdvanceRef = useRef(false)
  // Which scene the reset block last ran for, and which audio url is currently
  // loaded into the element. Both exist because the scene-change effect now
  // also fires when assets arrive mid-scene; see the effect for why.
  const resetForSceneRef = useRef(-1)
  const appliedAudioUrlRef = useRef(null)
  const prefetchedUrlsRef = useRef(new Set())
  const playbackRateRef = useRef(1)
  const [playbackRate, setPlaybackRate] = useState(1)
  // +1 = moving forward through the book, -1 = back. Drives which way the page
  // swings, so jumping backwards doesn't look like turning forwards.
  const [turnDir, setTurnDir] = useState(1)

  const scenes = storyData?.scenes || []
  // Use max of what backend promised vs what we actually received
  const actualTotal = Math.max(totalScenes, scenes.length)
  const scene = scenes[currentScene]

  // "Published" no longer means "playable". A scene reaches the player as soon
  // as its TEXT exists, so scenes.length reaches actualTotal a second or two
  // after the LLM finishes while every picture and narration track is still
  // being made. Readiness therefore has to count ASSETS, not array entries -
  // counting entries is exactly what made the "N/M ready" chip stop appearing
  // once the player started opening early.
  const readyCount = scenes.filter(s => s.image_url && s.audio_url).length
  const allScenesReady = scenes.length >= actualTotal && readyCount >= actualTotal
  // Generation is over when the backend says every scene is done - and App.jsx
  // forces this true the moment polling stops, INCLUDING for a story that
  // finished with a scene whose audio never got made. That distinction is
  // load-bearing: Next is gated on narration below, and a permanently-null
  // audio_url must never strand a child mid-story behind a dead button.
  const generationDone = totalScenes > 0 && completedSceneCount >= totalScenes
  const nextScene = scenes[currentScene + 1]
  const nextNotReady = !generationDone && !!nextScene && !nextScene.audio_url
  // The 'ended' listener is registered per scene but runs much later, by which
  // point more assets have landed - so it must not trust its captured `scenes`.
  const scenesRef = useRef(scenes)
  scenesRef.current = scenes

  // URLs
  const fullImageUrl = buildFullUrl(scene?.image_url)
  const fullAudioUrl = buildFullUrl(scene?.audio_url)

  // A scene now reaches the player as soon as its TEXT exists; the picture and
  // the narration land afterwards and arrive through polling as url changes on
  // this same object (see /api/status in backend/main.py). Null means "not made
  // yet", which is a waiting state - never an error, and never something to
  // hand to <img>, <audio> or the 3D layer.
  const imagePending = !fullImageUrl
  const audioPending = !fullAudioUrl

  const show3D = supports3D && !imageError && !imagePending

  // Expose download trigger
  useImperativeHandle(ref, () => ({
    triggerDownload: () => handleOfflineDownload(),
    setGeneratingMessage: (msg) => setGeneratingMessage(msg),
    // No-op for backward compat
  }))

  // Reset on scene change.
  //
  // This effect also has to re-run when image_url/audio_url arrive mid-scene,
  // because a scene now reaches the player with text only and its assets are
  // filled in later by polling. That makes the distinction below load-bearing:
  // everything that represents the USER'S intent (paused, already auto-played,
  // pending advance) must survive an asset arriving, or the story resumes
  // itself the moment the picture lands - the same "pause doesn't work" bug
  // that oncanplaythrough used to cause, arriving by a new route.
  useEffect(() => {
    const isNewScene = resetForSceneRef.current !== currentScene
    resetForSceneRef.current = currentScene

    if (isNewScene) {
      // Only a genuine page turn feeds the 3D transition its previous image;
      // an asset arriving for the CURRENT scene is not a page turn.
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
      autoPlayedRef.current = false
      pendingAdvanceRef.current = false
    } else {
      lastImageUrlRef.current = fullImageUrl
    }

    // Preload image
    if (fullImageUrl) {
      const img = new Image()
      img.onload = () => setImageLoaded(true)
      img.onerror = () => setImageError(true)
      img.src = fullImageUrl
    }

    // Reset audio and auto-play when ready. Narration for this scene may not
    // exist yet - assigning src="" and calling load() fires a media error and
    // leaves the element permanently unusable for this scene, so leave it alone
    // entirely until a url arrives. fullAudioUrl is in this effect's deps, so
    // the moment polling fills it in this runs again and playback starts.
    //
    // Guarded on the url having actually CHANGED: this effect re-runs when the
    // image lands too, and re-running the block below on live narration would
    // pause it and rewind it to 0 mid-sentence.
    const audioUrlChanged = appliedAudioUrlRef.current !== fullAudioUrl
    appliedAudioUrlRef.current = fullAudioUrl

    if (audioRef.current && !fullAudioUrl) {
      audioRef.current.pause()
      audioRef.current.removeAttribute('src')
    } else if (audioRef.current && audioUrlChanged) {
      audioRef.current.pause()
      audioRef.current.currentTime = 0
      audioRef.current.src = fullAudioUrl
      audioRef.current.load()
      audioRef.current.oncanplaythrough = () => {
        setGeneratingMessage('') // Clear waiting message once audio loads
        setAudioError(false)
        audioRef.current.playbackRate = playbackRateRef.current
        // Auto-start exactly once per scene, and never against the user's
        // wishes. This used to call play() unconditionally, so a paused story
        // resumed itself the moment the network re-buffered enough to re-fire
        // canplaythrough - the "pause doesn't work" report.
        if (userPausedRef.current || autoPlayedRef.current) return
        autoPlayedRef.current = true
        audioRef.current.play().catch(() => {
          setAudioError(true)
          setIsPlaying(false)
        })
      }
    }

    // Detach the handler with the scene it belongs to, or a late canplaythrough
    // from the previous src can fire against the new one.
    const el = audioRef.current
    return () => { if (el) el.oncanplaythrough = null }
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
      // The <audio> element is the single source of truth for playing state.
      // Without this, any pause the button didn't cause - a scene change, an
      // incoming call, unplugged headphones, a backgrounded tab - left the icon
      // showing Pause with nothing playing.
      setIsPlaying(false)
    }
    const handleEnded = () => {
      savedTimeRef.current = 0
      userPausedRef.current = false
      setIsPlaying(false)

      // Read the CURRENT scene list, not the one captured when this listener was
      // registered - urls keep landing while a scene plays, so the closure is
      // always stale by the time narration ends.
      const live = scenesRef.current
      const next = live[currentScene + 1]
      if (next && next.audio_url) {
        setGeneratingMessage('Preparing next scene...')
        setCurrentScene(s => s + 1)
      } else if (next) {
        // The page exists but has no narration yet. Turning to it would leave a
        // silent scene with a disabled play button and nothing to end, which
        // reads as the story having simply stopped. Wait here instead; the
        // effect below moves us the instant the audio url arrives.
        pendingAdvanceRef.current = true
        setGeneratingMessage('Ollie is still recording the next page...')
      } else if (live.length < actualTotal) {
        // Next scene isn't generated yet. Don't guess with a fixed-delay timer -
        // it used to check `scenes.length` 5s later from a closure captured right
        // now, which never saw scenes that arrived via polling in the meantime, so
        // it got stuck re-checking the same stale count forever ("scene 2 doesn't
        // work", "doesn't auto move on completion"). Instead just flag that we're
        // waiting; the effect below watches storyData/scenes.length directly and
        // advances the instant a real new scene shows up, however long that takes.
        pendingAdvanceRef.current = true
        setGeneratingMessage(`Waiting for remaining scenes... (${live.length}/${actualTotal} ready)`)
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

  // Fires whenever the scene list changes (driven by App.jsx's polling), not on
  // a fixed timer - so playback picks the next page up the moment it's real,
  // whether that's 2 seconds or 2 minutes after we started waiting, and reliably
  // catches up once the full story finishes even if we'd already been sitting on
  // the "waiting" message a while.
  //
  // The trigger is the next scene's AUDIO, not its existence: since the player
  // opens on text, the next scene is usually already in the array with null urls
  // long before there is anything to narrate. `scenes` is a fresh array on every
  // poll, which is what makes this re-check as assets trickle in; the ref guard
  // above keeps that to an early return in the normal case.
  useEffect(() => {
    if (!pendingAdvanceRef.current) return
    const next = scenes[currentScene + 1]
    if (next && next.audio_url) {
      pendingAdvanceRef.current = false
      setGeneratingMessage('Preparing next scene...')
      setCurrentScene(s => s + 1)
    } else if (!next && allScenesReady) {
      pendingAdvanceRef.current = false
      setGeneratingMessage('')
      setShowQuiz(true)
    }
  }, [scenes, allScenesReady, currentScene])

  // A detached <audio> can keep playing in Chrome, so leaving the player
  // mid-story used to narrate over whatever screen you navigated to.
  useEffect(() => {
    const audio = audioRef.current
    return () => {
      if (!audio) return
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }
  }, [])

  const togglePlay = () => {
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) {
      if (audioError) setAudioError(false)
      userPausedRef.current = false
      // Resume from wherever the element already is. It used to rewind to
      // savedTimeRef, which is written on pause but NOT on seek - so seeking
      // while paused and then pressing play silently threw the seek away.
      audio.play().catch(() => {
        setAudioError(true)
        setIsPlaying(false)
      })
    } else {
      savedTimeRef.current = audio.currentTime
      audio.pause()
      userPausedRef.current = true
    }
    // isPlaying is set by the element's own play/pause events, not here, so it
    // cannot claim playback that never actually started.
  }

  // Pointer events, so a drag scrubs instead of only a tap landing. The track
  // was bound to onClick AND onTouchStart, which double-fired on every touch,
  // and the 250ms throttle in handleTimeUpdate then swallowed the resulting
  // position change - which is what read as "seek doesn't work".
  const seekToClientX = (clientX, rect) => {
    const audio = audioRef.current
    if (!audio?.duration) return
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    const target = ratio * audio.duration
    audio.currentTime = target
    savedTimeRef.current = target
    lastUpdateRef.current = 0
    setProgress(ratio * 100)
    setCurrentTime(target)
  }

  const handleSeekStart = (e) => {
    if (!audioRef.current?.duration) return
    seekingRef.current = true
    e.currentTarget.setPointerCapture?.(e.pointerId)
    seekToClientX(e.clientX, e.currentTarget.getBoundingClientRect())
  }

  const handleSeekMove = (e) => {
    if (!seekingRef.current) return
    seekToClientX(e.clientX, e.currentTarget.getBoundingClientRect())
  }

  const handleSeekEnd = (e) => {
    if (!seekingRef.current) return
    seekingRef.current = false
    e.currentTarget.releasePointerCapture?.(e.pointerId)
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
    setTurnDir(idx >= currentScene ? 1 : -1)
    // isPlaying is no longer set optimistically here - the real 'play' event
    // (wired below) is the single source of truth, so the button can't get
    // stuck showing Pause when playback actually failed to start (blocked
    // autoplay, network hiccup, etc.) without anything ever rolling it back.
    setCurrentScene(idx)
  }

  // The circular arrow in the header now does what a circular arrow means:
  // replay from the top. It used to be wired to onRestart, which DELETES the
  // current unsaved story from the server and navigates away - a destructive,
  // irreversible action one mis-tap deep, behind an icon that reads as
  // "reload". "New Story" still exists, spelled out in words, in the app
  // drawer. Nothing here touches the network.
  const handleReplay = () => {
    const audio = audioRef.current
    userPausedRef.current = false
    setGeneratingMessage('')
    setTurnDir(-1)

    if (currentScene !== 0) {
      if (audio) audio.pause()
      setCurrentScene(0)
      return
    }

    // Already on scene 1: setCurrentScene(0) is a no-op, so the scene-change
    // effect never fires and nothing would restart. Rewind the element itself.
    if (!audio) return
    audio.currentTime = 0
    savedTimeRef.current = 0
    lastUpdateRef.current = 0
    setProgress(0)
    setCurrentTime(0)
    audio.play().catch(() => {
      setAudioError(true)
      setIsPlaying(false)
    })
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
        <button className="icon-btn" onClick={onHome} aria-label="Back to home"><Home size={18} /></button>
        <div className="header-title-area">
          <h1 className="story-title">{storyData?.title || 'Untitled Story'}</h1>
          <span className="scene-badge">
            Scene {currentScene + 1} of {actualTotal}
            {!allScenesReady && (
              <span className="scenes-ready-chip" aria-live="polite">
                <Loader2 size={11} className="scenes-ready-spinner" aria-hidden="true" />
                {readyCount}/{actualTotal} ready
              </span>
            )}
          </span>
        </div>
        {/* This slot used to hold a SECOND hamburger whose menu offered only
            Restart / Quiz / Save / Download - no navigation at all. On a phone
            it sat ~60px under the app's real hamburger, so tapping the obvious
            one gave a dead end ("navigation doesn't work while playing"). All
            four entries already exist elsewhere: Save and Download in the app
            drawer, Quiz as the button below, Restart as New Story. */}
        <button className="icon-btn" onClick={handleReplay} aria-label="Replay from the beginning"><RotateCw size={18} /></button>
      </header>

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
                turnDir={turnDir}
                pointerTiltRef={pointerTiltRef}
              />
            </Suspense>
          )}
          {!imageLoaded && !imageError && (
            <div className="image-skeleton">
              <div className="skeleton-pulse" />
              <span>
                <BookOpen size={18} aria-hidden="true" />
                {imagePending ? ' Ollie is painting this picture…' : ` Scene ${currentScene + 1}`}
              </span>
            </div>
          )}
          {imageError && (
            <div className="image-fallback">
              <BookOpen size={28} aria-hidden="true" />
              <span>Scene {currentScene + 1}</span>
            </div>
          )}
          {!show3D && fullImageUrl && (
            <AnimatePresence initial={false} custom={turnDir}>
              <motion.img
                key={fullImageUrl}
                src={fullImageUrl}
                alt={`Scene ${currentScene + 1}`}
                className="scene-image scene-page"
                custom={turnDir}
                variants={PAGE_TURN}
                initial="enter"
                animate="center"
                exit="exit"
                transition={PAGE_TURN_TRANSITION}
                style={{ transformOrigin: turnDir > 0 ? 'left center' : 'right center' }}
                onLoad={() => setImageLoaded(true)}
                onError={() => setImageError(true)}
              />
            </AnimatePresence>
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
              initial={{ opacity: 0, x: turnDir > 0 ? 26 : -26, filter: 'blur(4px)' }}
              animate={{ opacity: 1, x: 0, filter: 'blur(0px)' }}
              transition={{ duration: 0.45, ease: 'easeOut', delay: 0.12 }}
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
          <div
            className="progress-track"
            onPointerDown={handleSeekStart}
            onPointerMove={handleSeekMove}
            onPointerUp={handleSeekEnd}
            onPointerCancel={handleSeekEnd}
            style={{ touchAction: 'none' }}
          >
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
          <button
            className={`ctrl-btn play-btn ${isPlaying ? 'is-playing' : ''} ${audioPending ? 'is-waiting' : ''}`}
            onClick={togglePlay}
            disabled={audioPending}
            aria-label={audioPending ? 'Narration is still being recorded' : (isPlaying ? 'Pause' : 'Play story')}
            title={audioPending ? 'Narration is still being recorded' : undefined}
          >
            <span ref={playGlowRef} className="play-btn-glow" aria-hidden="true" />
            {isPlaying ? <Pause size={26} /> : <Play size={26} />}
          </button>
          <button
            className={`ctrl-btn ${nextNotReady ? 'is-waiting' : ''}`}
            onClick={() => goToScene(Math.min(scenes.length - 1, currentScene + 1))}
            disabled={(currentScene >= scenes.length - 1 && scenes.length >= actualTotal) || nextNotReady}
            aria-label={nextNotReady ? 'The next page is still being recorded' : 'Next scene'}
            title={nextNotReady ? 'The next page is still being recorded' : undefined}
          >
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
              onComplete={() => { setShowQuiz(false); onRestart?.() }}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
})

StoryPlayer.displayName = 'StoryPlayer'
export default StoryPlayer
