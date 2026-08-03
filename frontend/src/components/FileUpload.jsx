import { useCallback, useEffect, useRef } from 'react'
import { useDropzone } from 'react-dropzone'
import { motion } from 'framer-motion'
import { animate } from 'animejs'
import { UploadCloud, File as FileIcon, FileText } from 'lucide-react'
import './FileUpload.css'

function FileUpload({ onUpload, gradeLevel, onGradeLevelChange, isReuploading }) {
  const iconRef = useRef(null)

  const onDrop = useCallback((acceptedFiles) => {
    if (acceptedFiles.length > 0) {
      onUpload(acceptedFiles[0])
    }
  }, [onUpload])

  const { getRootProps, getInputProps, isDragActive, acceptedFiles } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
      'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['.pptx'],
    },
    maxFiles: 1
  })

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (prefersReducedMotion || !iconRef.current) return
    const animation = animate(iconRef.current, {
      translateY: [-4, 4, -4],
      duration: 2600,
      easing: 'easeInOutSine',
      loop: true,
    })
    return () => animation.pause()
  }, [])

  return (
    <div className="file-upload">
      <h2><FileText size={22} aria-hidden="true" /> Upload Learning Material</h2>
      <p className="subtitle">Upload a PDF, Word, or PowerPoint document to get started</p>

      <div className="grade-selector">
        <label htmlFor="grade">Select Grade Level:</label>
        <select
          id="grade"
          value={gradeLevel}
          onChange={(e) => onGradeLevelChange(e.target.value)}
        >
          <option value="KG1">KG-1</option>
          <option value="KG2">KG-2</option>
          <option value="1">Grade 1</option>
          <option value="2">Grade 2</option>
          <option value="3">Grade 3</option>
          <option value="4">Grade 4</option>
          <option value="5">Grade 5</option>
          <option value="6">Grade 6</option>
          <option value="7">Grade 7</option>
          <option value="8">Grade 8</option>
          <option value="9">Grade 9</option>
          <option value="10">Grade 10</option>
        </select>
      </div>

      <div
        {...getRootProps()}
        className={`dropzone ${isDragActive ? 'active' : ''}`}
      >
        <input {...getInputProps()} />
        <div ref={iconRef} className="upload-icon">
          <UploadCloud size={40} aria-hidden="true" />
        </div>
        {isDragActive ? (
          <p>Drop your file here...</p>
        ) : (
          <>
            <p>Drag & drop a file here, or click to browse</p>
            <span className="file-types">PDF, DOCX, PPTX</span>
          </>
        )}
      </div>

      {acceptedFiles.length > 0 && (
        <motion.div
          className="file-preview"
          initial={{ opacity: 0, scale: 0.9, y: 8 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={{ type: 'spring', damping: 22, stiffness: 300 }}
        >
          <FileIcon size={20} aria-hidden="true" />
          <span>{acceptedFiles[0].name}</span>
          <span className="file-size">
            ({(acceptedFiles[0].size / 1024 / 1024).toFixed(2)} MB)
          </span>
        </motion.div>
      )}
    </div>
  )
}

export default FileUpload
