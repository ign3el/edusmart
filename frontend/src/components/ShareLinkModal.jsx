import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, Copy, Check, Share2, Link2Off, Loader2, AlertTriangle } from 'lucide-react'
import { getShareLink, createShareLink, revokeShareLink } from '../services/api'
import './ShareLinkModal.css'

/**
 * Owner-facing control for a story's public link.
 *
 * Two states, one screen: not shared (single "Create link" action) or shared
 * (the link, Copy, Share, and a revoke that asks first). Revoking is
 * destructive from the recipient's point of view - somebody's open tab stops
 * working - so it goes through a confirmation step rather than firing on the
 * first tap.
 */
function ShareLinkModal({ storyId, storyTitle, onClose }) {
  const [shareUrl, setShareUrl] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [confirmingRevoke, setConfirmingRevoke] = useState(false)
  const [error, setError] = useState('')
  const closeRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const data = await getShareLink(storyId)
        if (!cancelled) setShareUrl(data.share_url || '')
      } catch {
        if (!cancelled) setError('Could not load the sharing status for this story.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [storyId])

  // Focus the close control on open so the dialog is immediately dismissible
  // from the keyboard, and give Escape the same job.
  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const handleCreate = async () => {
    setBusy(true)
    setError('')
    try {
      const data = await createShareLink(storyId)
      setShareUrl(data.share_url)
    } catch {
      setError('Could not create a link. Please try again.')
    }
    setBusy(false)
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      setError('Copying failed. You can select the link above and copy it manually.')
    }
  }

  // navigator.share is the native sheet on Android/iOS - the fastest path from
  // "I made a story" to "it is in a WhatsApp message". Desktop browsers mostly
  // lack it, hence the Copy button always being present too.
  const canNativeShare = typeof navigator !== 'undefined' && !!navigator.share

  const handleNativeShare = async () => {
    try {
      await navigator.share({
        title: storyTitle || 'A LearnTale story',
        text: storyTitle ? `Watch "${storyTitle}" on LearnTale` : 'Watch this story on LearnTale',
        url: shareUrl,
      })
    } catch {
      /* The user dismissed the sheet - not an error worth reporting. */
    }
  }

  const handleRevoke = async () => {
    setBusy(true)
    setError('')
    try {
      await revokeShareLink(storyId)
      setShareUrl('')
      setConfirmingRevoke(false)
    } catch {
      setError('Could not turn sharing off. Please try again.')
    }
    setBusy(false)
  }

  return (
    <motion.div
      className="share-modal-overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      onClick={onClose}
    >
      <motion.div
        className="share-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-modal-title"
        initial={{ opacity: 0, y: 24, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 12, scale: 0.98 }}
        transition={{ duration: 0.22, ease: 'easeOut' }}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="share-modal-header">
          <h2 id="share-modal-title">Share this story</h2>
          <button
            ref={closeRef}
            type="button"
            className="share-icon-btn"
            onClick={onClose}
            aria-label="Close sharing options"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        {loading && (
          <div className="share-modal-loading" role="status" aria-live="polite">
            <Loader2 size={20} className="share-spinner" aria-hidden="true" />
            <span>Checking…</span>
          </div>
        )}

        {!loading && !shareUrl && (
          <div className="share-modal-body">
            <p className="share-modal-copy">
              Create a link that anyone can open — no account needed. They can
              read the story and take the quiz, but not change or delete it.
            </p>
            <button
              type="button"
              className="share-primary-btn"
              onClick={handleCreate}
              disabled={busy}
            >
              {busy
                ? <><Loader2 size={18} className="share-spinner" aria-hidden="true" /> Creating…</>
                : <><Share2 size={18} aria-hidden="true" /> Create share link</>}
            </button>
          </div>
        )}

        {!loading && shareUrl && (
          <div className="share-modal-body">
            <label className="share-field-label" htmlFor="share-url-input">
              Anyone with this link can read the story
            </label>
            <input
              id="share-url-input"
              className="share-url-input"
              type="text"
              value={shareUrl}
              readOnly
              onFocus={(e) => e.target.select()}
            />

            <div className="share-actions">
              <button type="button" className="share-primary-btn" onClick={handleCopy}>
                {copied
                  ? <><Check size={18} aria-hidden="true" /> Copied</>
                  : <><Copy size={18} aria-hidden="true" /> Copy link</>}
              </button>
              {canNativeShare && (
                <button type="button" className="share-secondary-btn" onClick={handleNativeShare}>
                  <Share2 size={18} aria-hidden="true" /> Share
                </button>
              )}
            </div>

            <AnimatePresence mode="wait">
              {!confirmingRevoke ? (
                <motion.button
                  key="revoke-start"
                  type="button"
                  className="share-revoke-btn"
                  onClick={() => setConfirmingRevoke(true)}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                >
                  <Link2Off size={16} aria-hidden="true" /> Turn off sharing
                </motion.button>
              ) : (
                <motion.div
                  key="revoke-confirm"
                  className="share-revoke-confirm"
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.18, ease: 'easeOut' }}
                >
                  <p>
                    <AlertTriangle size={16} aria-hidden="true" />
                    Everyone you already sent this link to will lose access.
                  </p>
                  <div className="share-actions">
                    <button
                      type="button"
                      className="share-secondary-btn"
                      onClick={() => setConfirmingRevoke(false)}
                      disabled={busy}
                    >
                      Keep sharing
                    </button>
                    <button
                      type="button"
                      className="share-danger-btn"
                      onClick={handleRevoke}
                      disabled={busy}
                    >
                      {busy
                        ? <><Loader2 size={16} className="share-spinner" aria-hidden="true" /> Turning off…</>
                        : <>Turn off</>}
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {error && (
          <p className="share-modal-error" role="alert">
            <AlertTriangle size={16} aria-hidden="true" /> {error}
          </p>
        )}
      </motion.div>
    </motion.div>
  )
}

export default ShareLinkModal
