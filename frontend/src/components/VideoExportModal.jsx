import { useState, useEffect, useRef, useCallback } from 'react'
import { motion } from 'framer-motion'
import { X, Video, Download, Loader2, AlertTriangle, RotateCw } from 'lucide-react'
import { generateVideo, getVideoStatus, fetchVideoBlob } from '../services/api'
import { usePauseMediaOnHidden } from '../hooks/usePauseMediaOnHidden'
import { useDialog } from '../context/DialogContext'
import './VideoExportModal.css'

// Polling backoff mirrors App.jsx's upload poller (fast while young, slower
// once it's clearly a longer render) without that poller's full stall-timeout
// machinery - a render is bounded by the backend's own per-scene ffmpeg
// timeout, so a wedged job settles into 'failed' on its own.
const POLL_FAST_MS = 2500
const POLL_SLOW_MS = 6000
const POLL_SLOW_AFTER_MS = 45000

/**
 * Owner-facing control for rendering a story into a narrated video with
 * burned-in captions. Mirrors ShareLinkModal's shape (lazy-loaded, same
 * overlay/dialog visual language) but adds a progress state in between
 * "nothing yet" and "done", since rendering takes real time.
 */
function VideoExportModal({ storyId, storyTitle, onClose }) {
  const { confirm } = useDialog()
  const [status, setStatus] = useState('loading') // loading | none | queued | processing | completed | failed
  const [progressScene, setProgressScene] = useState(0)
  const [totalScenes, setTotalScenes] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [videoObjectUrl, setVideoObjectUrl] = useState('')
  const [videoLoading, setVideoLoading] = useState(false)
  const closeRef = useRef(null)
  const pollTimerRef = useRef(null)
  const startedAtRef = useRef(0)
  const objectUrlRef = useRef('')
  const videoFetchStartedRef = useRef(false)
  const videoElRef = useRef(null)

  // Confirmed via screen recording: leaving the app (Home button) left this
  // exact <video> playing in Android's auto Picture-in-Picture, narrating
  // over whatever the user did next. disablePictureInPicture on the element
  // below stops Chrome from offering that PiP window at all; this covers
  // backgrounding without PiP too (tab switch, other browsers).
  usePauseMediaOnHidden(videoElRef)

  const applyStatus = useCallback((data) => {
    setStatus(data.status)
    setProgressScene(data.progress_scene || 0)
    setTotalScenes(data.total_scenes || 0)
    if (data.status === 'failed') setError(data.error || 'Rendering failed. Please try again.')
  }, [])

  const stopPolling = () => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }

  const poll = useCallback(async () => {
    try {
      const data = await getVideoStatus(storyId)
      applyStatus(data)
      if (data.status === 'queued' || data.status === 'processing') {
        const delay = Date.now() - startedAtRef.current > POLL_SLOW_AFTER_MS ? POLL_SLOW_MS : POLL_FAST_MS
        pollTimerRef.current = setTimeout(poll, delay)
      }
    } catch {
      // Transient network hiccup - keep trying on the slow cadence rather
      // than dropping the user into a dead spinner.
      pollTimerRef.current = setTimeout(poll, POLL_SLOW_MS)
    }
  }, [storyId, applyStatus])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const data = await getVideoStatus(storyId)
        if (cancelled) return
        applyStatus(data)
        if (data.status === 'queued' || data.status === 'processing') {
          startedAtRef.current = Date.now()
          pollTimerRef.current = setTimeout(poll, POLL_FAST_MS)
        }
      } catch {
        if (!cancelled) {
          setStatus('none')
          setError('Could not check video status. Please try again.')
        }
      }
    }
    load()
    return () => {
      cancelled = true
      stopPolling()
    }
  }, [storyId, applyStatus, poll])

  // Once the render is done, fetch the file as an authenticated blob and hand
  // the player an object URL - see api.js's fetchVideoBlob for why a plain
  // <video src> pointing at the API can't work here.
  //
  // Guarded with a ref, not the videoLoading STATE: setVideoLoading(true)
  // below used to sit in this same effect's own dependency array, which made
  // React clean up and re-run the effect the instant that state update
  // landed - the cleanup set `cancelled = true` on the closure holding the
  // real in-flight fetch, so its `.then`/`.finally` silently dropped the
  // result and the spinner never cleared even though the request succeeded
  // (confirmed via curl through the full Cloudflare/nginx/backend chain -
  // the network path was never the problem). A ref doesn't trigger a
  // re-render, so it can guard against a duplicate start without also being
  // a dependency that cancels the first attempt.
  useEffect(() => {
    if (status !== 'completed' || videoObjectUrl || videoFetchStartedRef.current) return
    videoFetchStartedRef.current = true
    let cancelled = false
    setVideoLoading(true)
    fetchVideoBlob(storyId)
      .then((blob) => {
        if (cancelled) return
        const url = URL.createObjectURL(blob)
        objectUrlRef.current = url
        setVideoObjectUrl(url)
      })
      .catch(() => {
        if (!cancelled) setError('Video finished rendering but could not be loaded for preview.')
      })
      .finally(() => {
        if (!cancelled) setVideoLoading(false)
      })
    return () => { cancelled = true }
  }, [status, storyId, videoObjectUrl])

  useEffect(() => () => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
  }, [])

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const handleGenerate = async () => {
    setBusy(true)
    setError('')
    try {
      const data = await generateVideo(storyId)
      applyStatus(data)
      startedAtRef.current = Date.now()
      stopPolling()
      // Regenerating from 'completed': the backend overwrites video.mp4 on
      // the next successful render, so the old preview blob is about to go
      // stale. Drop it and reset the fetch guard now - otherwise once
      // status flips back to 'completed' the blob-fetch effect below would
      // see a still-set videoObjectUrl/ref and never fetch the new file.
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current)
        objectUrlRef.current = ''
      }
      videoFetchStartedRef.current = false
      setVideoObjectUrl('')
      pollTimerRef.current = setTimeout(poll, POLL_FAST_MS)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not start the video render. Please try again.')
    }
    setBusy(false)
  }

  const handleRegenerate = async () => {
    const ok = await confirm('Regenerate this video? The current version will be replaced.', { confirmLabel: 'Regenerate' })
    if (!ok) return
    handleGenerate()
  }

  const handleDownload = () => {
    if (!videoObjectUrl) return
    const a = document.createElement('a')
    a.href = videoObjectUrl
    a.download = `${(storyTitle || 'story').trim() || 'story'}.mp4`
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  return (
    <motion.div
      className="video-modal-overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      onClick={onClose}
    >
      <motion.div
        className="video-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="video-modal-title"
        initial={{ opacity: 0, y: 24, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 12, scale: 0.98 }}
        transition={{ duration: 0.22, ease: 'easeOut' }}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="video-modal-header">
          <h2 id="video-modal-title">Story video</h2>
          <button
            ref={closeRef}
            type="button"
            className="video-icon-btn"
            onClick={onClose}
            aria-label="Close video export"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        {status === 'loading' && (
          <div className="video-modal-loading" role="status" aria-live="polite">
            <Loader2 size={20} className="video-spinner" aria-hidden="true" />
            <span>Checking…</span>
          </div>
        )}

        {status === 'none' && (
          <div className="video-modal-body">
            <p className="video-modal-copy">
              Turn this story into a narrated video with the narration burned in
              as on-screen captions - ready to share with teachers, students or
              parents who'd rather watch than click through the story.
            </p>
            <button
              type="button"
              className="video-primary-btn"
              onClick={handleGenerate}
              disabled={busy}
            >
              {busy
                ? <><Loader2 size={18} className="video-spinner" aria-hidden="true" /> Starting…</>
                : <><Video size={18} aria-hidden="true" /> Generate video</>}
            </button>
          </div>
        )}

        {(status === 'queued' || status === 'processing') && (
          <div className="video-modal-body">
            <p className="video-modal-copy">
              {status === 'queued'
                ? 'Waiting for a render slot…'
                : `Rendering scene ${progressScene} of ${totalScenes || '?'}…`}
            </p>
            <div className="video-progress-track" role="progressbar"
              aria-valuenow={progressScene} aria-valuemin={0} aria-valuemax={totalScenes || 1}>
              <div
                className="video-progress-fill"
                style={{ width: totalScenes ? `${Math.min(100, (progressScene / totalScenes) * 100)}%` : '8%' }}
              />
            </div>
            <p className="video-modal-hint">This usually takes a minute or two - feel free to close this and check back.</p>
          </div>
        )}

        {status === 'completed' && (
          <div className="video-modal-body">
            {videoLoading && (
              <div className="video-modal-loading" role="status" aria-live="polite">
                <Loader2 size={20} className="video-spinner" aria-hidden="true" />
                <span>Loading preview…</span>
              </div>
            )}
            {videoObjectUrl && (
              <video
                ref={videoElRef}
                className="video-preview"
                src={videoObjectUrl}
                controls
                playsInline
                disablePictureInPicture
              />
            )}
            <div className="video-actions">
              <button type="button" className="video-primary-btn" onClick={handleDownload} disabled={!videoObjectUrl}>
                <Download size={18} aria-hidden="true" /> Download
              </button>
              <button type="button" className="video-secondary-btn" onClick={handleRegenerate} disabled={busy}>
                {busy
                  ? <><Loader2 size={18} className="video-spinner" aria-hidden="true" /> Starting…</>
                  : <><RotateCw size={18} aria-hidden="true" /> Regenerate</>}
              </button>
            </div>
            <p className="video-modal-hint">Anyone with this story's share link automatically sees the video too.</p>
          </div>
        )}

        {status === 'failed' && (
          <div className="video-modal-body">
            <button
              type="button"
              className="video-primary-btn"
              onClick={handleGenerate}
              disabled={busy}
            >
              {busy
                ? <><Loader2 size={18} className="video-spinner" aria-hidden="true" /> Retrying…</>
                : <><RotateCw size={18} aria-hidden="true" /> Try again</>}
            </button>
          </div>
        )}

        {error && (
          <p className="video-modal-error" role="alert">
            <AlertTriangle size={16} aria-hidden="true" /> {error}
          </p>
        )}
      </motion.div>
    </motion.div>
  )
}

export default VideoExportModal
