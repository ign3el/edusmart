import { motion, AnimatePresence } from 'framer-motion'
import { Sparkles, X } from 'lucide-react'
import './UpgradeModal.css'

function UpgradeModal({ isOpen, onClose, onViewPlans, message }) {
  if (!isOpen) return null

  return (
    <AnimatePresence>
      <motion.div
        className="modal-overlay"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      >
        <motion.div
          className="upgrade-modal modal-content"
          initial={{ opacity: 0, scale: 0.9, y: 20 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.9, y: 20 }}
          transition={{ type: 'spring', damping: 25, stiffness: 300 }}
          onClick={(e) => e.stopPropagation()}
        >
          <button onClick={onClose} className="upgrade-modal-close" aria-label="Close">
            <X size={18} aria-hidden="true" />
          </button>
          <div className="upgrade-modal-icon"><Sparkles size={32} aria-hidden="true" /></div>
          <h2>Out of Story Credits</h2>
          <p>{message || "You've used up your story credits for now. Upgrade your plan or grab a top-up to keep generating."}</p>
          <div className="upgrade-modal-actions">
            <button onClick={onClose} className="upgrade-cancel-btn">Not Now</button>
            <button onClick={onViewPlans} className="upgrade-view-plans-btn">View Plans</button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}

export default UpgradeModal
