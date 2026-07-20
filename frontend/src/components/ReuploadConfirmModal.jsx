import { motion } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'
import './ReuploadConfirmModal.css'

function ReuploadConfirmModal({ onConfirm, onCancel }) {
  return (
    <motion.div 
      className="modal-overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onCancel}
    >
      <motion.div 
        className="modal-content reupload-modal"
        initial={{ scale: 0.8, y: 50 }}
        animate={{ scale: 1, y: 0 }}
        exit={{ scale: 0.8, y: 50 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-icon"><AlertTriangle size={32} aria-hidden="true" /></div>
        <h2>Re-upload File?</h2>
        <p>Changing the file will reset your current selections. Are you sure you want to proceed?</p>
        
        <div className="modal-actions">
          <button className="cancel-btn" onClick={onCancel}>
            Keep Current
          </button>
          <button className="confirm-btn" onClick={onConfirm}>
            Yes, Change File
          </button>
        </div>
      </motion.div>
    </motion.div>
  )
}

export default ReuploadConfirmModal
