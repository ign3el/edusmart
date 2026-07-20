import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Save, CheckCircle2 } from 'lucide-react'
import './SaveStoryModal.css'

function SaveStoryModal({ jobId, onSave, onCancel }) {
  const [storyName, setStoryName] = useState('')
  const [saving, setSaving] = useState(false)
  const [downloadMessage, setDownloadMessage] = useState('')
  const [downloadProgress, setDownloadProgress] = useState(0)

  const handleSave = async () => {
    if (!storyName.trim()) return
    
    setSaving(true)
    setDownloadMessage('Saving story...')
    setDownloadProgress(15)
    
    try {
      const formData = new FormData()
      formData.append('story_name', storyName.trim())
      
      setDownloadProgress(35)
      
      const response = await fetch(`/api/save-story/${jobId}`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('auth_token')}`
        },
        body: formData
      })
      
      setDownloadProgress(65)
      
      if (!response.ok) throw new Error('Failed to save story')
      
      const result = await response.json()
      
      setDownloadProgress(95)
      setDownloadMessage('Story saved successfully!')
      
      setTimeout(() => {
        setDownloadProgress(100)
        onSave(result.story_id, storyName)
      }, 400)
    } catch (error) {
      setDownloadMessage(`Error: ${error.message}`)
      setTimeout(() => {
        setDownloadMessage('')
        setSaving(false)
        setDownloadProgress(0)
      }, 3000)
    }
  }

  return (
    <motion.div 
      className="modal-overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onCancel}
    >
      <motion.div 
        className="modal-content"
        initial={{ scale: 0.8, y: 50 }}
        animate={{ scale: 1, y: 0 }}
        exit={{ scale: 0.8, y: 50 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2><Save size={22} /> Save Your Story</h2>
        <p className="modal-description">Give your adventure a name so you can find it later.</p>
        <input
          type="text"
          id="story-name"
          name="story-name"
          placeholder="Enter story name..."
          value={storyName}
          onChange={(e) => setStoryName(e.target.value)}
          maxLength={50}
          autoFocus
          onKeyPress={(e) => e.key === 'Enter' && handleSave()}
          disabled={saving}
        />
        
        <AnimatePresence>
          {downloadMessage && (
            <motion.div 
              className="save-progress"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 10 }}
            >
              <p className="progress-message">
                {downloadProgress >= 100 && <CheckCircle2 size={16} />} {downloadMessage}
              </p>
              <div className="progress-bar-container">
                <div 
                  className="progress-bar" 
                  style={{ width: `${downloadProgress}%` }}
                />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        
        <div className="modal-buttons">
          <button className="cancel-btn" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button 
            className="save-btn" 
            onClick={handleSave}
            disabled={!storyName.trim() || saving}
          >
            {saving ? 'Saving...' : 'Save Story'}
          </button>
        </div>
      </motion.div>
    </motion.div>
  )
}

export default SaveStoryModal
