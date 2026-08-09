import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, HelpCircle, Info } from 'lucide-react'
import './ConfirmDialog.css'

/**
 * Themed stand-in for window.alert()/window.confirm(). Same overlay/dialog
 * shell as ShareLinkModal and VideoExportModal so a destructive prompt or an
 * error message doesn't suddenly drop into an unstyled OS dialog that
 * breaks the dark theme. Rendered by DialogContext, not used directly -
 * call useDialog()'s confirm()/alert() instead.
 */
function ConfirmDialog({
  mode = 'confirm', // 'confirm' | 'alert'
  variant = 'default', // 'default' | 'danger'
  title,
  message,
  confirmLabel,
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
}) {
  const confirmRef = useRef(null)
  const dismiss = mode === 'alert' ? onConfirm : onCancel

  useEffect(() => {
    confirmRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') dismiss?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dismiss])

  const Icon = variant === 'danger' ? AlertTriangle : mode === 'alert' ? Info : HelpCircle

  return (
    <motion.div
      className="confirm-dialog-overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      onClick={dismiss}
    >
      <motion.div
        className="confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-message"
        initial={{ opacity: 0, y: 24, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 12, scale: 0.98 }}
        transition={{ duration: 0.22, ease: 'easeOut' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={`confirm-dialog-icon confirm-dialog-icon--${variant === 'danger' ? 'danger' : 'default'}`}>
          <Icon size={22} aria-hidden="true" />
        </div>
        {title && <h2 className="confirm-dialog-title">{title}</h2>}
        <p id="confirm-dialog-message" className="confirm-dialog-message">{message}</p>

        <div className="confirm-dialog-actions">
          {mode === 'confirm' && (
            <button type="button" className="confirm-dialog-btn confirm-dialog-btn--secondary" onClick={onCancel}>
              {cancelLabel}
            </button>
          )}
          <button
            ref={confirmRef}
            type="button"
            className={`confirm-dialog-btn ${variant === 'danger' ? 'confirm-dialog-btn--danger' : 'confirm-dialog-btn--primary'}`}
            onClick={onConfirm}
          >
            {confirmLabel || (mode === 'alert' ? 'OK' : 'Confirm')}
          </button>
        </div>
      </motion.div>
    </motion.div>
  )
}

export default ConfirmDialog
