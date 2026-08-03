import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Trophy, RefreshCw, BookOpen, FileText, Brain, Check, X,
  MapPin, Lightbulb, CheckCircle2, AlertTriangle
} from 'lucide-react';
import { markQuizComplete } from '../services/api';
import ScoreBurst from './ScoreBurst';
import Mascot from './Mascot';
import './Quiz.css';

// Progress is keyed per-story so closing the quiz (or losing the tab to a
// refresh, now that App.jsx also survives that) and reopening it later picks
// up on the same question with the same score, instead of starting over.
const progressKey = (storyId) => `edusmart_quiz_progress_${storyId}`;

// correct_answer arrives as a bare letter ("B") while options are full
// prefixed strings ("B. A diet that..."). Comparing them directly is always
// false, so everything that decides correctness must go through here first.
// Returns null when no letter can be read, and null never equals null here
// because callers compare against a real letter.
const letterOf = (value) => {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim().toUpperCase();
  const prefixed = trimmed.match(/^([A-D])[.)\s]/);
  if (prefixed) return prefixed[1];
  return /^[A-D]$/.test(trimmed) ? trimmed : null;
};

function loadProgress(storyId) {
  if (!storyId) return null;
  try {
    const raw = localStorage.getItem(progressKey(storyId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

const Quiz = ({ questions, onComplete, onClose, onBackToStory, storyId }) => {
  const saved = loadProgress(storyId);
  const [currentQuestion, setCurrentQuestion] = useState(saved?.currentQuestion ?? 0);
  const [score, setScore] = useState(saved?.score ?? 0);
  const [showResults, setShowResults] = useState(saved?.showResults ?? false);
  const [selectedOption, setSelectedOption] = useState(null);
  const [isCorrect, setIsCorrect] = useState(null);
  const [userAnswers, setUserAnswers] = useState(saved?.userAnswers ?? []);
  const [reviewMode, setReviewMode] = useState(saved?.reviewMode ?? false);
  const [isLoading, setIsLoading] = useState(false);

  // Persist on every change so an interrupted quiz can resume later. Cleared
  // once results are first shown - a finished quiz doesn't need "resume",
  // and this avoids reopening straight into stale results after a retake.
  useEffect(() => {
    if (!storyId) return;
    if (showResults) {
      localStorage.removeItem(progressKey(storyId));
      return;
    }
    localStorage.setItem(progressKey(storyId), JSON.stringify({
      currentQuestion, score, userAnswers, showResults, reviewMode,
    }));
  }, [storyId, currentQuestion, score, userAnswers, showResults, reviewMode]);

  // Handle missing or invalid quiz data
  const validQuestions = Array.isArray(questions) && questions.length > 0 ? questions : [];

  // Check for quiz data issues
  React.useEffect(() => {
    if (!questions || !Array.isArray(questions) || questions.length === 0) {
      console.warn('Quiz component received no questions data');
      if (import.meta.env.DEV) console.log('Expected: Array of question objects, received:', questions);
    }
  }, [questions]);

  const retakeQuiz = () => {
    setCurrentQuestion(0);
    setScore(0);
    setShowResults(false);
    setSelectedOption(null);
    setIsCorrect(null);
    setUserAnswers([]);
    setReviewMode(false);
  };

  const handleAnswer = (option) => {
    if (selectedOption !== null) return; // Prevent double clicking

    setSelectedOption(option);
    const currentQ = questions[currentQuestion];
    // Handle both old and new quiz structure
    const correctAnswer = currentQ.correct_answer || currentQ.answer;
    const questionText = currentQ.question_text || currentQ.question;

    const selectedLetter = letterOf(option);
    const correctLetter = letterOf(correctAnswer);

    const correct = selectedLetter !== null && selectedLetter === correctLetter;
    setIsCorrect(correct);

    // Track user answer with proper state update
    const newAnswer = {
      question: questionText,
      selected: option,
      correct: correctAnswer,
      explanation: currentQ.explanation || "No explanation provided.",
      isCorrect: correct,
      // Include additional metadata if available
      source: currentQ.source,
      document_section: currentQ.document_section,
      // Without this the why_correct block in review mode never renders,
      // even though the backend supplies the field.
      why_correct: currentQ.why_correct
    };

    setUserAnswers(prev => [...prev, newAnswer]);
    if (correct) {
      setScore(prev => prev + 1);
    }

    setTimeout(async () => {
      if (currentQuestion + 1 < questions.length) {
        setCurrentQuestion(currentQuestion + 1);
        setSelectedOption(null);
        setIsCorrect(null);
      } else {
        setShowResults(true);
        // Mark quiz as completed
        if (storyId) {
          try {
            await markQuizComplete(storyId);
            if (import.meta.env.DEV) console.log('Quiz marked as completed');
          } catch (error) {
            console.error('Failed to mark quiz complete:', error);
          }
        }
      }
    }, 1500);
  };

  if (showResults && !reviewMode) {
    return (
      <motion.div className="quiz-container results" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        <h2><Trophy size={22} aria-hidden="true" /> Learning Complete!</h2>
        <div className="quiz-mascot">
          {/* Half marks or better is a win worth celebrating; below that Ollie
              thinks rather than cheers, so the praise still means something. */}
          <Mascot
            mood={score >= questions.length / 2 ? 'happy' : 'thinking'}
            size={92}
            message={
              score === questions.length ? 'A perfect run!'
                : score >= questions.length / 2 ? 'Nicely done!'
                : "Let's read that again together."
            }
          />
        </div>
        <div className="score-circle">
          <ScoreBurst />
          <span>{score}</span> / {questions.length}
        </div>
        <p>{score === questions.length
          ? "Perfect score - you read every page properly."
          : score >= questions.length / 2
            ? "Good work. Review the ones you missed and try again."
            : "Worth another read. The answers are all in the story."}</p>
        <div className="result-buttons">
          <button onClick={retakeQuiz} className="retake-btn"><RefreshCw size={16} aria-hidden="true" /> Retake Quiz</button>
          <button onClick={() => setReviewMode(true)} className="review-btn"><BookOpen size={16} aria-hidden="true" /> Review Answers</button>
          {onBackToStory && <button onClick={onBackToStory} className="story-btn"><BookOpen size={16} aria-hidden="true" /> Back to Story</button>}
          <button onClick={onComplete} className="finish-btn">Back to Library</button>
        </div>
      </motion.div>
    );
  }

  if (reviewMode) {
    return (
      <motion.div className="quiz-container review-mode" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        <h2><BookOpen size={22} aria-hidden="true" /> Answer Review</h2>
        <div className="review-list">
          {userAnswers.map((answer, index) => {
            const currentQ = questions[index];
            const options = currentQ?.options || [];
            return (
              <motion.div
                key={index}
                className={`review-item ${answer.isCorrect ? 'correct-answer' : 'wrong-answer'}`}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.1 }}
              >
                <div className="review-header">
                  <span className="review-number">Question {index + 1}</span>
                  {answer.source && (
                    <span className="source-badge">
                      {answer.source === 'extracted' ? <><FileText size={14} aria-hidden="true" /> From Document</> : <><Brain size={14} aria-hidden="true" /> Generated</>}
                    </span>
                  )}
                  <span className={`review-badge ${answer.isCorrect ? 'badge-correct' : 'badge-wrong'}`}>
                    {answer.isCorrect ? <><Check size={14} aria-hidden="true" /> Correct</> : <><X size={14} aria-hidden="true" /> Wrong</>}
                  </span>
                </div>
                <h4>{answer.question}</h4>
                <div className="review-answers">
                  <div className="options-review-list">
                    {options.map((opt, i) => {
                      const letter = String.fromCharCode(65 + i);
                      const isSelected = letterOf(answer.selected) === letter;
                      const isCorrectOption = letterOf(answer.correct) === letter;
                      let rowClass = 'option-review';
                      if (isCorrectOption) rowClass += ' option-review-correct';
                      else if (isSelected && !isCorrectOption) rowClass += ' option-review-wrong';

                      // Strip leading letter prefix if present (e.g., "A. Option" -> "Option")
                      const optText = opt.replace(/^[A-D]\.\s*/, '');

                      return (
                        <div key={i} className={rowClass}>
                          <span className="option-letter">{letter}.</span>
                          <span className="option-text">{optText}</span>
                          {isCorrectOption && <span className="option-tag">Correct</span>}
                          {isSelected && !isCorrectOption && <span className="option-tag wrong-tag">Your answer</span>}
                        </div>
                      );
                    })}
                  </div>
                </div>
                {answer.source === 'extracted' && answer.document_section && (
                  <div className="document-section">
                    <strong><MapPin size={14} aria-hidden="true" /> Section:</strong> {answer.document_section}
                  </div>
                )}
                <div className="explanation">
                  <strong><Lightbulb size={14} aria-hidden="true" /> Explanation:</strong>
                  <p>{answer.explanation}</p>
                </div>
                {answer.why_correct && (
                  <div className="why-correct">
                    <strong><CheckCircle2 size={14} aria-hidden="true" /> Why this is correct:</strong>
                    <p>{answer.why_correct}</p>
                  </div>
                )}
              </motion.div>
            );
          })}
        </div>
        <div className="review-footer">
          <div className="final-score">
            Final Score: <strong>{score} / {questions.length}</strong>
            ({Math.round((score / questions.length) * 100)}%)
          </div>
          {onBackToStory && <button onClick={onBackToStory} className="story-btn"><BookOpen size={16} aria-hidden="true" /> Back to Story</button>}
          <button onClick={onComplete} className="finish-btn">Back to Library</button>
        </div>
      </motion.div>
    );
  }

  // Guard: Handle missing or invalid questions
  if (!validQuestions.length) {
    return (
      <motion.div
        className="quiz-container error"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
      >
        <div className="error-state">
          <h2><AlertTriangle size={22} aria-hidden="true" /> Quiz Unavailable</h2>
          <p>The quiz data for this story is not yet available.</p>
          <p className="small">Please ensure the story generation completed successfully.</p>
          {onBackToStory && <button onClick={onBackToStory} className="finish-btn">Back to Story</button>}
          <button onClick={onComplete} className="finish-btn">Back to Library</button>
        </div>
      </motion.div>
    );
  }

  const q = validQuestions[currentQuestion];
  // Handle both old and new quiz structure
  const questionText = q.question_text || q.question;
  const options = q.options || [];

  return (
    <div className="quiz-container">
      <div className="quiz-progress">
        Question {currentQuestion + 1} of {validQuestions.length}
        {q.source && (
          <span className="source-indicator">
            {q.source === 'extracted' ? <FileText size={16} aria-hidden="true" /> : <Brain size={16} aria-hidden="true" />}
          </span>
        )}
        {onClose && (
          <button type="button" className="quiz-close-btn" onClick={onClose} aria-label="Close quiz, keep my progress">
            <X size={18} aria-hidden="true" />
          </button>
        )}
      </div>

      <AnimatePresence mode="wait">
        <motion.div
          key={currentQuestion}
          initial={{ x: 50, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: -50, opacity: 0 }}
          className="question-card"
        >
          <h3>{questionText}</h3>
          {q.source === 'extracted' && q.document_section && (
            <div className="document-section-info">
              <MapPin size={14} aria-hidden="true" /> From: {q.document_section}
            </div>
          )}
          <div className="options-grid">
            {options.map((option, index) => (
              <motion.button
                key={index}
                onClick={() => handleAnswer(option)}
                className={`option-btn ${selectedOption === option
                  ? (isCorrect ? 'correct' : 'wrong')
                  : ''
                  }`}
                disabled={selectedOption !== null}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.06, duration: 0.25 }}
              >
                {option}
              </motion.button>
            ))}
          </div>
        </motion.div>
      </AnimatePresence>
    </div>
  );
};

export default Quiz;
