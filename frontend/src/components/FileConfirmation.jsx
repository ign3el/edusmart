import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import {
  File as FileIcon, Edit3, BookOpen, Search,
  ArrowLeft, ArrowRight, CheckCircle2, Globe, ListChecks, AlertTriangle
} from 'lucide-react'
import TeacherCard from './TeacherCard'
import './FileConfirmation.css'

// Must match QUIZ_SIZE_OPTIONS / DEFAULT_QUIZ_SIZE in backend/services/story_service.py.
// The backend re-normalises whatever arrives, so a mismatch degrades to the
// nearest offered size rather than failing the upload.
const QUIZ_SIZES = [5, 10, 15, 20]
const DEFAULT_QUIZ_SIZE = 10

const cardVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: (i) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.08, duration: 0.35, ease: 'easeOut' }
  })
}

function FileConfirmation({ file, gradeLevel, onConfirm, onBack, onReupload, onEditGrade }) {
  // New state for Kokoro TTS settings
  const [voice, setVoice] = useState('af_sarah');
  const [detectedLanguage, setDetectedLanguage] = useState('en');
  const [isDetectingLanguage, setIsDetectingLanguage] = useState(false);

  const [showGradeSelector, setShowGradeSelector] = useState(false)

  const [quizSize, setQuizSize] = useState(DEFAULT_QUIZ_SIZE)
  // null = the backend had no opinion (too little native text to judge, e.g. a
  // scanned PDF that only the vision pass can read). Not the same as zero.
  const [capacity, setCapacity] = useState(null)
  const [showCapacityDialog, setShowCapacityDialog] = useState(false)

  // Detect language on mount
  useEffect(() => {
    detectLanguageFromFile();
  }, [file]);

  const detectLanguageFromFile = async () => {
    setIsDetectingLanguage(true);
    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch('/api/upload/extract-text', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('auth_token')}`
        },
        body: formData
      });

      if (response.ok) {
        const data = await response.json();
        const lang = data.language_code || 'en';
        setDetectedLanguage(lang);
        setCapacity(
          typeof data.estimated_questions === 'number' ? data.estimated_questions : null
        );

        // Auto-select appropriate voice based on language
        if (lang === 'ar') {
          setVoice('ar_teacher');
        } else {
          setVoice('af_sarah');
        }
      }
    } catch (error) {
      console.error('Language detection error:', error);
      setDetectedLanguage('en');
    } finally {
      setIsDetectingLanguage(false);
    }
  };

  const gradeLabels = {
    KG1: 'KG-1',
    KG2: 'KG-2',
    1: 'Grade 1',
    2: 'Grade 2',
    3: 'Grade 3',
    4: 'Grade 4',
    5: 'Grade 5',
    6: 'Grade 6',
    7: 'Grade 7',
    8: 'Grade 8',
    9: 'Grade 9',
    10: 'Grade 10'
  }

  const gradeOrder = ['KG1', 'KG2', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10']

  const languageLabels = {
    en: 'English',
    ar: 'Arabic',
    hi: 'Hindi'
  }

  // The document cannot support what was asked for. Known BEFORE a credit is
  // spent, so the user gets the choice rather than a short quiz after the fact.
  const isOverCapacity = capacity !== null && quizSize > capacity

  const handleConfirm = () => {
    if (isOverCapacity) {
      setShowCapacityDialog(true)
      return
    }
    onConfirm({ voice, quizSize })
  }

  // "Continue" honours the reduced count rather than the original request, so
  // the user is not charged for a target the document was never going to meet.
  const handleContinueReduced = () => {
    setShowCapacityDialog(false)
    setQuizSize(capacity)
    onConfirm({ voice, quizSize: capacity })
  }

  const handleGradeChange = (newGrade) => {
    onEditGrade(newGrade)
    setShowGradeSelector(false)
  }

  return (
    <div className="file-confirmation">
      <h2><CheckCircle2 size={22} aria-hidden="true" /> Confirm Your Story Settings</h2>
      <p className="subtitle">Review your selections before generating the story</p>

      <motion.div
        className="confirmation-card"
        initial="hidden"
        animate="visible"
      >
        <motion.div className="file-info" custom={0} variants={cardVariants}>
          <FileIcon className="file-icon" size={26} aria-hidden="true" />
          <div>
            <h3>Uploaded File</h3>
            <p className="filename">{file.name}</p>
            <p className="filesize">({(file.size / 1024 / 1024).toFixed(2)} MB)</p>
          </div>
          <button
            className="edit-icon-btn"
            onClick={onReupload}
            title="Re-upload file"
            aria-label="Re-upload file"
          >
            <Edit3 size={16} aria-hidden="true" />
          </button>
        </motion.div>

        <motion.div className="grade-info" custom={1} variants={cardVariants}>
          <BookOpen className="info-icon" size={26} aria-hidden="true" />
          <div>
            <h3>Grade Level</h3>
            <p>{gradeLabels[gradeLevel]}</p>
          </div>
          <button
            className="edit-icon-btn"
            onClick={() => setShowGradeSelector(!showGradeSelector)}
            title="Change grade level"
          >
            <Edit3 size={16} aria-hidden="true" />
          </button>

          {showGradeSelector && (
            <div className="grade-selector-dropdown">
              {gradeOrder.map((grade) => (
                <button
                  key={grade}
                  className={`grade-option ${String(gradeLevel) === grade ? 'selected' : ''}`}
                  onClick={() => handleGradeChange(grade)}
                >
                  {gradeLabels[grade]}
                </button>
              ))}
            </div>
          )}
        </motion.div>

        {!isDetectingLanguage && (
          <motion.div
            className="language-badge"
            initial={{ opacity: 0, scale: 0.85 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: 'spring', damping: 20, stiffness: 300, delay: 0.15 }}
          >
            <Globe size={14} aria-hidden="true" />
            <span>Detected language: {languageLabels[detectedLanguage] || 'English'}</span>
          </motion.div>
        )}
      </motion.div>

      <div className="quiz-size-block">
        <div className="quiz-size-head">
          <ListChecks size={18} aria-hidden="true" />
          <div>
            <h3 id="quiz-size-label">Quiz Questions</h3>
            <p>How many questions to ask at the end</p>
          </div>
        </div>
        <div className="quiz-size-options" role="radiogroup" aria-labelledby="quiz-size-label">
          {QUIZ_SIZES.map((n) => (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={quizSize === n}
              className={`quiz-size-option${quizSize === n ? ' selected' : ''}${
                capacity !== null && n > capacity ? ' over-capacity' : ''
              }`}
              onClick={() => setQuizSize(n)}
            >
              {n}
            </button>
          ))}
        </div>
        {isOverCapacity && (
          <p className="quiz-size-warning" role="status">
            <AlertTriangle size={14} aria-hidden="true" />
            <span>
              This document has enough material for about <strong>{capacity}</strong> questions.
            </span>
          </p>
        )}
        <p className="quiz-size-hint">
          Page count is set by the document, not by this &mdash; a shorter quiz still gets the full story.
        </p>
      </div>

      <div className="voice-selection">
        {isDetectingLanguage ? (
          <div className="detecting-language">
            <p><Search size={18} aria-hidden="true" /> Detecting document language...</p>
          </div>
        ) : (
          <TeacherCard
            activeVoice={voice}
            onVoiceSelect={setVoice}
            detectedLanguage={detectedLanguage}
          />
        )}
      </div>

      <div className="confirmation-actions">
        <button className="back-btn" onClick={onBack}>
          <ArrowLeft size={16} aria-hidden="true" /> Back
        </button>
        <button className="confirm-btn" onClick={handleConfirm}>
          Confirm & Generate Story <ArrowRight size={16} aria-hidden="true" />
        </button>
      </div>

      {showCapacityDialog && (
        <div
          className="capacity-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="capacity-title"
        >
          <motion.div
            className="capacity-dialog"
            initial={{ opacity: 0, scale: 0.94, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={{ type: 'spring', damping: 24, stiffness: 320 }}
          >
            <AlertTriangle className="capacity-icon" size={30} aria-hidden="true" />
            <h3 id="capacity-title">Not enough material for {quizSize} questions</h3>
            <p>
              This document can support about <strong>{capacity}</strong> good questions.
              Pushing for {quizSize} would mean repeating the same points.
            </p>
            <p className="capacity-note">Nothing has been charged yet.</p>
            <div className="capacity-actions">
              <button
                className="capacity-cancel"
                onClick={() => setShowCapacityDialog(false)}
              >
                Go Back
              </button>
              <button className="capacity-continue" onClick={handleContinueReduced}>
                Continue with {capacity}
              </button>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  )
}

export default FileConfirmation
