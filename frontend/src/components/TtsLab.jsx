import React, { useState, useRef, useEffect } from 'react';
import { generateTestSpeech } from '../services/api';
import VoiceSettings from './VoiceSettings';
import './TtsLab.css';

const TtsLab = () => {
    const [text, setText] = useState("Hello, this is a test of the Kokoro text to speech engine.");
    const [voice, setVoice] = useState('af_sarah');
    const [speed, setSpeed] = useState(1.0);

    const [isPlaying, setIsPlaying] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState('');

    // Refs for Web Audio API
    const audioContextRef = useRef(null);
    const sourceNodeRef = useRef(null);
    const generationRef = useRef(0);

    useEffect(() => {
      return () => {
        if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
          audioContextRef.current.close();
        }
      };
    }, []);

    const handlePlay = async () => {
        if (isPlaying) {
            handleAbort();
            return;
        }

        setIsLoading(true);
        setError('');
        
        try {
            generationRef.current++;
            const currentGen = generationRef.current;

            // Initialize AudioContext on user interaction
            if (!audioContextRef.current) {
                audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)();
            }
            const audioContext = audioContextRef.current;

            const audioData = await generateTestSpeech({ text, voice, speed });
            const audioBuffer = await audioContext.decodeAudioData(audioData);

            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(audioContext.destination);
            source.start(0);

            source.onended = () => {
                if (generationRef.current === currentGen) {
                    setIsPlaying(false);
                    sourceNodeRef.current = null;
                }
            };

            sourceNodeRef.current = source;
            setIsPlaying(true);

        } catch (err) {
            setError(err.message || "Failed to generate or play audio.");
            console.error(err);
        } finally {
            setIsLoading(false);
        }
    };

    const handleAbort = () => {
        if (sourceNodeRef.current) {
            sourceNodeRef.current.stop(); // This triggers the 'onended' event
            sourceNodeRef.current = null;
        }
        setIsPlaying(false);
    };

    return (
        <div className="tts-lab">
            <p className="tts-lab-description">
                Use this tool to test the Kokoro TTS voices and settings in real-time.
            </p>
            <div className="tts-lab-main">
                <div className="tts-lab-textarea-container">
                    <textarea
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        placeholder="Enter text to synthesize..."
                        className="tts-lab-textarea"
                        disabled={isLoading}
                    />
                </div>
                <div className="tts-lab-controls">
                    <VoiceSettings
                        voice={voice}
                        setVoice={setVoice}
                        speed={speed}
                        setSpeed={setSpeed}
                    />
                    <div className="tts-lab-actions">
                        <button 
                            className={`play-button ${isPlaying ? 'abort' : ''}`}
                            onClick={handlePlay}
                            disabled={isLoading}
                        >
                            {isLoading ? (
                                <span className="spinner-small"></span>
                            ) : isPlaying ? (
                                'Abort'
                            ) : (
                                'Play'
                            )}
                        </button>
                        {error && <div className="tts-lab-error">{error}</div>}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default TtsLab;
