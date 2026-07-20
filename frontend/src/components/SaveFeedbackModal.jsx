import { useEffect } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle2, AlertTriangle } from 'lucide-react'
import './SaveFeedbackModal.css'

const AUTO_DISMISS_MS = 2500

function SaveFeedbackModal({ variant = 'success', title, message, onDismiss }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, AUTO_DISMISS_MS)
    return () => clearTimeout(timer)
  }, [onDismiss])

  const isSuccess = variant === 'success'

  return (
    <motion.div
      className="feedback-modal-overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onDismiss}
    >
      <motion.div
        className={`feedback-modal-content ${isSuccess ? 'is-success' : 'is-error'}`}
        initial={{ scale: 0.7, y: 40, opacity: 0 }}
        animate={{ scale: 1, y: 0, opacity: 1 }}
        exit={{ scale: 0.8, y: 20, opacity: 0 }}
        transition={{ type: 'spring', stiffness: 380, damping: 24 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="feedback-icon-ring">
          <div className="feedback-icon-glow" aria-hidden="true" />
          {isSuccess
            ? <CheckCircle2 size={36} strokeWidth={2.2} aria-hidden="true" />
            : <AlertTriangle size={36} strokeWidth={2.2} aria-hidden="true" />}
        </div>
        <h2>{title}</h2>
        {message && <p>{message}</p>}
        <button className="feedback-dismiss-btn" onClick={onDismiss}>Got it</button>
      </motion.div>
    </motion.div>
  )
}

export default SaveFeedbackModal
