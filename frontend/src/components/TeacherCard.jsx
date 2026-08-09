import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { animate } from 'animejs';
import {
  Play, Check, Star, Heart, Headphones, GraduationCap, Sun, BookOpen,
  Shield, Crown, Sparkles, Award, ScrollText, Moon, Smile, Target, BookMarked
} from 'lucide-react';
import { useDialog } from '../context/DialogContext';
import './TeacherCard.css';

const TEACHERS = [
  {
    id: "af_bella",
    name: "Bella",
    personality: "Warm & Expressive",
    description: "Enthusiastic storyteller with natural emotion and varied pitch",
    lang: "en",
    sample: "Hi there! I'm Bella. Let me take you on an amazing learning adventure!",
    Icon: Star
  },
  {
    id: "af_heart",
    name: "Heart",
    personality: "Gentle & Caring",
    description: "Soft, nurturing voice perfect for younger learners",
    lang: "en",
    sample: "Hello sweetie! I'm Heart. Let's discover wonderful things together!",
    Icon: Heart
  },
  {
    id: "af_nicole",
    name: "Nicole",
    personality: "Energetic & Fun",
    description: "Dynamic voice with playful enthusiasm for active learning",
    lang: "en",
    sample: "Hey! I'm Nicole! Ready to explore and have some fun learning?",
    Icon: Headphones
  },
  {
    id: "af_sarah",
    name: "Sarah",
    personality: "Professional & Clear",
    description: "Calm, articulate educator for focused instruction",
    lang: "en",
    sample: "Hello! I'm Sarah, your educational guide. I'll help make learning fun and engaging.",
    Icon: GraduationCap
  },
  {
    id: "af_sky",
    name: "Sky",
    personality: "Bright & Cheerful",
    description: "Light, optimistic voice that encourages curiosity",
    lang: "en",
    sample: "Hi! I'm Sky! Let's explore the world of learning together!",
    Icon: Sun
  },
  {
    id: "am_michael",
    name: "Michael",
    personality: "Wise Narrator",
    description: "Mature male voice with authoritative storytelling",
    lang: "en",
    sample: "Greetings! I'm Michael. Let me guide you through fascinating stories.",
    Icon: BookOpen
  },
  {
    id: "am_fenrir",
    name: "Fenrir",
    personality: "Strong & Confident",
    description: "Powerful male voice for adventurous narratives",
    lang: "en",
    sample: "Hello! I'm Fenrir. Get ready for exciting tales of learning!",
    Icon: Shield
  },
  {
    id: "bf_emma",
    name: "Emma",
    personality: "British Elegance",
    description: "Refined British accent with graceful storytelling",
    lang: "en",
    sample: "Good day! I'm Emma. Allow me to share marvelous stories with you.",
    Icon: Crown
  },
  {
    id: "bf_isabella",
    name: "Isabella",
    personality: "British Charm",
    description: "Warm British accent perfect for engaging young learners",
    lang: "en",
    sample: "Hello there! I'm Isabella. Shall we explore wonderful stories together?",
    Icon: Sparkles
  },
  {
    id: "bm_george",
    name: "George",
    personality: "British Gentleman",
    description: "Distinguished British male voice with clear articulation",
    lang: "en",
    sample: "Good afternoon! I'm George. Let me guide you through fascinating tales.",
    Icon: Award
  },
  {
    id: "bm_fable",
    name: "Fable",
    personality: "British Storyteller",
    description: "Expressive British male narrator for captivating adventures",
    lang: "en",
    sample: "Greetings! I'm Fable. Prepare for enchanting stories of learning!",
    Icon: ScrollText
  },
  {
    id: "ar_teacher",
    name: "Nour",
    personality: "Arabic Educator",
    description: "Clear Modern Standard Arabic with warm delivery",
    lang: "ar",
    sample: "مرحباً! أنا نور، معلمتك العربية. سأساعدك في رحلة تعليمية ممتعة.",
    Icon: Moon
  },
  {
    id: "hf_alpha",
    name: "Priya",
    personality: "Hindi Teacher",
    description: "Professional Hindi educator with clear pronunciation",
    lang: "hi",
    sample: "नमस्ते! मैं प्रिया हूँ। आइए साथ में सीखें!",
    Icon: GraduationCap
  },
  {
    id: "hf_beta",
    name: "Anjali",
    personality: "Warm & Friendly",
    description: "Gentle Hindi storyteller with expressive delivery",
    lang: "hi",
    sample: "नमस्कार! मैं अंजलि हूँ। चलिए एक अद्भुत कहानी सुनते हैं!",
    Icon: Smile
  },
  {
    id: "hm_omega",
    name: "Arjun",
    personality: "Strong Narrator",
    description: "Confident male Hindi voice for engaging stories",
    lang: "hi",
    sample: "नमस्ते! मैं अर्जुन हूँ। आइए रोमांचक कहानियाँ सुनें!",
    Icon: Target
  },
  {
    id: "hm_psi",
    name: "Vikram",
    personality: "Wise Teacher",
    description: "Authoritative male Hindi educator for learning",
    lang: "hi",
    sample: "नमस्कार! मैं विक्रम हूँ। चलिए ज्ञान की यात्रा शुरू करें!",
    Icon: BookMarked
  }
];

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const gridVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.05 } }
};

const cardVariants = {
  hidden: { opacity: 0, y: 14 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut' } }
};

function TeacherCardItem({ teacher, isActive, isPlaying, onSelect, onPlay }) {
  const cardRef = useRef(null)
  const wasActive = useRef(isActive)

  useEffect(() => {
    if (isActive && !wasActive.current && cardRef.current) {
      const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      if (!prefersReducedMotion) {
        animate(cardRef.current, {
          boxShadow: [
            '0 0 0 4px rgba(139, 92, 246, 0.45), 0 12px 40px rgba(139, 92, 246, 0.35)',
            '0 0 0 4px rgba(139, 92, 246, 0.2), 0 12px 40px rgba(139, 92, 246, 0.3)',
          ],
          duration: 500,
          easing: 'easeOutQuad',
        })
      }
    }
    wasActive.current = isActive
  }, [isActive])

  const { Icon } = teacher

  return (
    <motion.div
      ref={cardRef}
      variants={cardVariants}
      className={`teacher-card ${isActive ? 'teacher-card-active' : ''}`}
      onClick={() => onSelect(teacher.id)}
    >
      <div className="teacher-card-header">
        <div className="teacher-icon">
          <Icon size={30} aria-hidden="true" />
        </div>
        {isActive && (
          <div className="teacher-selected-badge">
            <Check size={16} aria-hidden="true" />
          </div>
        )}
      </div>

      <h4 className="teacher-name">{teacher.name}</h4>

      <div className="teacher-personality-badge">
        {teacher.personality}
      </div>

      <p className="teacher-description">{teacher.description}</p>

      <button
        className={`teacher-play-btn ${isPlaying ? 'playing' : ''}`}
        onClick={(e) => onPlay(e, teacher)}
        disabled={isPlaying}
      >
        <Play size={14} aria-hidden="true" />
        <span>{isPlaying ? 'Playing...' : 'Preview Voice'}</span>
      </button>
    </motion.div>
  )
}

function TeacherCard({ activeVoice = "af_sarah", onVoiceSelect, detectedLanguage = "en" }) {
  const { alert: showAlert } = useDialog();
  const [playingVoice, setPlayingVoice] = useState(null);
  const [audioCache, setAudioCache] = useState({});
  // The sample used to be a bare local `new Audio(...)` inside playSample, so
  // nothing outside that one call could ever reach it again. A detached
  // HTMLAudioElement keeps playing after React unmounts its component, which is
  // why a preview carried straight through "Generate Story" and talked over the
  // generating screen. Holding it here makes it stoppable.
  const previewRef = useRef(null);
  const previewRequestRef = useRef(0);

  const stopPreview = () => {
    const audio = previewRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
      previewRef.current = null;
    }
    setPlayingVoice(null);
  };

  // Leaving the screen for any reason - Generate Story, back, the drawer -
  // silences the sample. This is the cleanup that was missing entirely.
  useEffect(() => () => {
    // Bumping the token first invalidates any fetch still in flight, so a
    // sample that finishes downloading after this screen is gone never starts.
    previewRequestRef.current++;
    const audio = previewRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
      previewRef.current = null;
    }
  }, []);

  // Filter teachers based on detected language
  const filteredTeachers = TEACHERS.filter(teacher => {
    if (detectedLanguage === 'ar') {
      return teacher.lang === 'ar';
    }
    if (detectedLanguage === 'hi') {
      return teacher.lang === 'hi';
    }
    return teacher.lang === 'en';
  });

  // Auto-select default voice based on language
  const defaultVoice = detectedLanguage === 'ar' ? 'ar_teacher'
    : detectedLanguage === 'hi' ? 'hf_alpha'
    : 'af_bella';

  // If no voice is selected or current voice doesn't match language, use default
  const currentActiveVoice = filteredTeachers.find(t => t.id === activeVoice)
    ? activeVoice
    : defaultVoice;

  const handleCardClick = (teacherId) => {
    onVoiceSelect(teacherId);
  };

  const playSample = async (e, teacher) => {
    e.stopPropagation(); // Prevent card selection when clicking play

    // Switching voices silences the one already talking. The old guard was
    // `if (playingVoice) return`, which meant a preview that never fired its
    // onended - or was still being fetched - locked out every other sample
    // until the screen was left entirely.
    //
    // Note the button carrying this handler is disabled={isPlaying}, so "tap
    // the same voice again" is not a reachable path and is deliberately not
    // handled here: a tap-to-stop toggle was tried, and with the button enabled
    // the tap started a SECOND playback of the same clip instead of stopping the
    // first (observed live: two <audio> elements, same src, pause() never
    // called). Not worth the regression for a nicety nobody asked for.
    stopPreview();

    // Claims this attempt. Anything started before it - a slow fetch still in
    // flight - fails the check below and is discarded instead of playing over
    // whatever the user moved on to. TTS cold-starts on RunPod take seconds,
    // which is exactly long enough to hit Generate Story first.
    const requestId = ++previewRequestRef.current;
    const stillWanted = () => previewRequestRef.current === requestId;

    setPlayingVoice(teacher.id);

    const start = (src) => {
      if (!stillWanted()) return;
      const audio = new Audio(src);
      previewRef.current = audio;
      const finish = () => {
        clearTimeout(watchdog);
        if (previewRef.current === audio) previewRef.current = null;
        setPlayingVoice((current) => (current === teacher.id ? null : current));
      };
      audio.onended = finish;
      // A bad/undecodable source (confirmed live 2026-08-09: a 200 response
      // that wasn't actually audio) can leave an <audio> element neither
      // erroring nor ending - play() just never settles, and the button was
      // stuck on "Playing..." for minutes with no way out but a refresh. This
      // guarantees the button always recovers even if a future source is bad
      // in some new way onerror doesn't catch.
      audio.onerror = () => {
        finish();
        showAlert(`Unable to play voice sample for ${teacher.name}. Please try again.`);
      };
      const watchdog = setTimeout(() => {
        if (previewRef.current === audio) {
          finish();
          showAlert(`Voice sample for ${teacher.name} took too long to play. Please try again.`);
        }
      }, 15000);
      audio.play().catch(() => { clearTimeout(watchdog); stopPreview(); });
    };

    try {
      // Check cache first
      if (audioCache[teacher.id]) {
        start(audioCache[teacher.id]);
        return;
      }

      // Sample text is fixed per voice (see TEACHERS above) - there is no
      // reason to ever regenerate it via TTS. A pre-rendered file at this
      // path is checked first; only a voice missing one (e.g. newly added,
      // not yet regenerated) falls through to the live TTS endpoint below.
      const staticUrl = `/voice-samples/${teacher.id}.mp3`;
      const staticCheck = await fetch(staticUrl, { method: 'HEAD' });
      if (staticCheck.ok) {
        setAudioCache(prev => ({ ...prev, [teacher.id]: staticUrl }));
        start(staticUrl);
        return;
      }

      // Use backend proxy endpoint
      const response = await fetch(`${API_URL}/api/upload/tts-preview`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('auth_token')}`
        },
        body: JSON.stringify({
          text: teacher.sample,
          voice: teacher.id,
          speed: 1.0
        })
      });

      if (!response.ok) {
        throw new Error(`TTS API error: ${response.status}`);
      }

      const audioBlob = await response.blob();
      const audioUrl = URL.createObjectURL(audioBlob);

      // Cached even when this attempt is no longer wanted - the bytes are paid
      // for either way, and the next tap should be instant.
      setAudioCache(prev => ({ ...prev, [teacher.id]: audioUrl }));

      start(audioUrl);

    } catch (error) {
      console.error('Error playing sample:', error);
      if (!stillWanted()) return;
      stopPreview();

      // Fallback: Show error message
      showAlert(`Unable to play voice sample for ${teacher.name}. Please try again.`);
    }
  };

  return (
    <div className="teacher-card-wrapper">
      <h3 className="teacher-card-title">
        {detectedLanguage === 'ar' ? (
          <><Moon size={18} aria-hidden="true" /> Select Arabic Teacher</>
        ) : (
          <><GraduationCap size={18} aria-hidden="true" /> Choose Your Teacher</>
        )}
      </h3>
      <motion.div
        className="teacher-grid"
        variants={gridVariants}
        initial="hidden"
        animate="visible"
      >
        {filteredTeachers.map(teacher => (
          <TeacherCardItem
            key={teacher.id}
            teacher={teacher}
            isActive={currentActiveVoice === teacher.id}
            isPlaying={playingVoice === teacher.id}
            onSelect={handleCardClick}
            onPlay={playSample}
          />
        ))}
      </motion.div>

      {filteredTeachers.length === 0 && (
        <div className="no-teachers">
          <p>No teachers available for this language.</p>
        </div>
      )}
    </div>
  );
}

export default TeacherCard;
