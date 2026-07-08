import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { FiPlay, FiPause, FiSkipForward, FiSkipBack, FiRotateCw, FiLoader } from 'react-icons/fi';
import apiClient from '../services/api';
import './StoryPlayer.css';

/**
 * Progressive Story Player
 * - Displays scenes as they become available
 * - Polls story status for progressive loading
 * - Shows skeleton/spinner for incomplete scenes
 */
function ProgressiveStoryPlayer({ storyId, avatar, onRestart, onSave, isSaved = false }) {
  const [scenes, setScenes] = useState([]);
  const [currentScene, setCurrentScene] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [storyStatus, setStoryStatus] = useState('processing');
  const [imageLoaded, setImageLoaded] = useState(false);
  const audioRef = useRef(null);
  
  // Poll for story status
  useEffect(() => {
    let pollInterval;
    
    const fetchStatus = async () => {
      try {
        const response = await apiClient.get(`/api/story/${storyId}/status`);
        const data = response.data;
        setStoryStatus(data.status);
        setScenes(data.scenes || []);
        
        // Stop polling when complete
        if (data.status === 'completed') {
          clearInterval(pollInterval);
        }
      } catch (error) {
        console.error('Failed to fetch story status:', error);
      }
    };
    
    fetchStatus(); // Initial fetch
    pollInterval = setInterval(fetchStatus, 3000); // Poll every 3s
    
    return () => clearInterval(pollInterval);
  }, [storyId]);
  
  const scene = scenes[currentScene];
  const isSceneReady = scene && 
    scene.image_status === 'completed' && 
    scene.audio_status === 'completed';
  
  // Handle audio playback
  useEffect(() => {
    if (!audioRef.current || !isSceneReady) return;
    
    if (isPlaying) {
      audioRef.current.play().catch(err => {
        console.error('Audio play failed:', err);
        setIsPlaying(false);
      });
    } else {
      audioRef.current.pause();
    }
  }, [isPlaying, isSceneReady]);
  
  // Reset on scene change
  useEffect(() => {
    setIsPlaying(false);
    setImageLoaded(false);
    
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      if (isSceneReady) {
        audioRef.current.src = `${apiClient.defaults.baseURL}${scene.audio_url}`;
        audioRef.current.load();
      }
    }
  }, [currentScene, isSceneReady, scene?.audio_url]);
  
  const handleNext = () => {
    if (currentScene < scenes.length - 1) {
      setCurrentScene(currentScene + 1);
    }
  };
  
  const handlePrev = () => {
    if (currentScene > 0) {
      setCurrentScene(currentScene - 1);
    }
  };
  
  if (!scene) {
    return (
      <div className="story-player">
        <div className="loading-state">
          <FiLoader className="spinner" />
          <p>Generating your story...</p>
        </div>
      </div>
    );
  }
  
  return (
    <div className="story-player">
      {/* Progress indicator */}
      <div className="story-progress-bar">
        <div 
          className="progress-fill" 
          style={{ width: `${((currentScene + 1) / scenes.length) * 100}%` }}
        />
      </div>
      
      <div className="scene-container">
        {/* Image with skeleton loading */}
        <div className="scene-image-wrapper">
          {!isSceneReady || !imageLoaded ? (
            <div className="skeleton-image">
              <FiLoader className="spinner" />
              <p>Generating scene {currentScene + 1}...</p>
            </div>
          ) : (
            <motion.img
              key={currentScene}
              src={`${apiClient.defaults.baseURL}${scene.image_url}`}
              alt={`Scene ${currentScene + 1}`}
              className="scene-image"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              onLoad={() => setImageLoaded(true)}
            />
          )}
          
          {/* Avatar overlay */}
          {avatar && imageLoaded && (
            <div className="avatar-overlay">
              <img src={`/avatars/${avatar}.png`} alt="Avatar" />
            </div>
          )}
        </div>
        
        {/* Text */}
        <div className="scene-text">
          <AnimatePresence mode="wait">
            <motion.p
              key={currentScene}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
            >
              {scene.text || 'Loading...'}
            </motion.p>
          </AnimatePresence>
        </div>
        
        {/* Controls */}
        <div className="player-controls">
          <button onClick={handlePrev} disabled={currentScene === 0} aria-label="Previous scene">
            <FiSkipBack />
          </button>
          
          <button 
            onClick={() => setIsPlaying(!isPlaying)} 
            disabled={!isSceneReady}
            className="play-btn"
            aria-label={isPlaying ? 'Pause' : 'Play story'}
          >
            {isPlaying ? <FiPause /> : <FiPlay />}
          </button>
          
          <button onClick={handleNext} disabled={currentScene === scenes.length - 1} aria-label="Next scene">
            <FiSkipForward />
          </button>
        </div>
        
        {/* Scene counter */}
        <div className="scene-counter">
          Scene {currentScene + 1} / {scenes.length}
        </div>
        
        {/* Action buttons */}
        <div className="action-buttons">
          <button onClick={onRestart} aria-label="New story">
            <FiRotateCw /> New Story
          </button>
          {!isSaved && storyStatus === 'completed' && (
            <button onClick={onSave} className="save-btn" aria-label="Save story">
              Save Story
            </button>
          )}
        </div>
      </div>
      
      {/* Hidden audio element */}
      {isSceneReady && (
        <audio ref={audioRef} style={{ display: 'none' }} />
      )}
    </div>
  );
}

export default ProgressiveStoryPlayer;
