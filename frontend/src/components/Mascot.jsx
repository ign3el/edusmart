import { useEffect, useRef, useState, memo } from 'react'
import './Mascot.css'

/**
 * Ollie - the LearnTale owl.
 *
 * Pure inline SVG on purpose: no image request, no sprite sheet, scales from a
 * 32px nav chip to a 260px hero without a second asset, and recolours straight
 * from the palette tokens. The whole character is ~40 nodes, which is cheaper to
 * animate than a PNG of the same size is to decode.
 *
 * Motion is deliberately split by mechanism:
 *   - idle bob / wing wave  -> CSS animation (compositor-only transforms, so it
 *     keeps running smoothly even while React is busy re-rendering a story)
 *   - blinking              -> setTimeout + state, because it must be irregular;
 *     a fixed CSS keyframe blink reads as a machine, not a creature
 *   - pupil tracking        -> direct DOM writes inside rAF, never state, so a
 *     pointer move can't trigger a React render on every frame
 *
 * Every one of those is disabled under prefers-reduced-motion, which leaves a
 * perfectly readable static owl.
 */

const MOODS = ['idle', 'thinking', 'happy', 'reading', 'sleepy']

function prefersReducedMotion() {
  return typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function Mascot({
  mood = 'idle',
  size = 120,
  /** Pupils follow the cursor. Off by default: only worth it for a hero-sized owl. */
  trackPointer = false,
  /** Renders the speech bubble above the owl when non-empty. */
  message = '',
  className = '',
  ...rest
}) {
  const safeMood = MOODS.includes(mood) ? mood : 'idle'
  const svgRef = useRef(null)
  const pupilsRef = useRef(null)
  const frameRef = useRef(0)
  const [blinking, setBlinking] = useState(false)
  const [reduced] = useState(prefersReducedMotion)

  // --- irregular blink -----------------------------------------------------
  useEffect(() => {
    // 'happy' and 'sleepy' already draw closed eyes, so a blink on top of them
    // is invisible work.
    if (reduced || safeMood === 'happy' || safeMood === 'sleepy') return
    let openTimer
    let closeTimer
    const schedule = () => {
      // Humans blink every 2-6s and sometimes twice in a row. The jitter is the
      // entire point - a metronome blink is what makes cheap mascots look dead.
      openTimer = setTimeout(() => {
        setBlinking(true)
        closeTimer = setTimeout(() => {
          setBlinking(false)
          schedule()
        }, 130)
      }, 1800 + Math.random() * 3600)
    }
    schedule()
    return () => { clearTimeout(openTimer); clearTimeout(closeTimer) }
  }, [reduced, safeMood])

  // --- pupil tracking ------------------------------------------------------
  useEffect(() => {
    if (reduced || !trackPointer || safeMood === 'happy' || safeMood === 'sleepy') return
    const el = pupilsRef.current
    const svg = svgRef.current
    if (!el || !svg) return

    const onMove = (e) => {
      cancelAnimationFrame(frameRef.current)
      frameRef.current = requestAnimationFrame(() => {
        const box = svg.getBoundingClientRect()
        if (!box.width) return
        const cx = box.left + box.width / 2
        const cy = box.top + box.height * 0.42 // eye line, not box centre
        const dx = e.clientX - cx
        const dy = e.clientY - cy
        const dist = Math.hypot(dx, dy) || 1
        // Clamp to the eye white's radius so a pupil can never escape its socket.
        const reach = Math.min(dist / 220, 1) * 3.4
        el.style.transform = `translate(${(dx / dist) * reach}px, ${(dy / dist) * reach}px)`
      })
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      window.removeEventListener('pointermove', onMove)
      cancelAnimationFrame(frameRef.current)
    }
  }, [reduced, trackPointer, safeMood])

  const closedEyes = safeMood === 'happy' || safeMood === 'sleepy' || blinking
  const uid = useRef(`ollie-${Math.random().toString(36).slice(2, 8)}`).current

  return (
    <div
      className={`mascot mascot-${safeMood} ${reduced ? 'mascot-still' : ''} ${className}`}
      style={{ width: size, height: size }}
      {...rest}
    >
      {message && (
        <div className="mascot-bubble" role="status">
          {message}
        </div>
      )}

      <svg
        ref={svgRef}
        viewBox="0 0 120 120"
        className="mascot-svg"
        role="img"
        aria-label={`Ollie the owl, ${safeMood}`}
      >
        <defs>
          <linearGradient id={`${uid}-body`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#a78bfa" />
            <stop offset="100%" stopColor="#7c3aed" />
          </linearGradient>
          <linearGradient id={`${uid}-belly`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#ede9fe" />
            <stop offset="100%" stopColor="#c4b5fd" />
          </linearGradient>
          <radialGradient id={`${uid}-glow`}>
            <stop offset="0%" stopColor="#8b5cf6" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Ambient glow so the owl reads against the violet page without a hard outline */}
        <circle cx="60" cy="64" r="52" fill={`url(#${uid}-glow)`} className="mascot-glow" />

        <g className="mascot-body-group">
          {/* feet */}
          <path d="M46 100 l-6 8 M46 100 l0 9 M46 100 l6 8" className="mascot-foot" />
          <path d="M74 100 l-6 8 M74 100 l0 9 M74 100 l6 8" className="mascot-foot" />

          {/* ear tufts */}
          <path d="M30 40 L26 20 L44 32 Z" fill={`url(#${uid}-body)`} />
          <path d="M90 40 L94 20 L76 32 Z" fill={`url(#${uid}-body)`} />

          {/* body */}
          <ellipse cx="60" cy="62" rx="38" ry="42" fill={`url(#${uid}-body)`} />
          {/* belly */}
          <ellipse cx="60" cy="72" rx="25" ry="29" fill={`url(#${uid}-belly)`} />

          {/* wings - the left one is the waver */}
          <g className="mascot-wing mascot-wing-left">
            <path d="M24 56 q-10 18 2 34 q10 6 12 -6 q-8 -14 -4 -28 Z" fill="#7c3aed" />
          </g>
          <g className="mascot-wing mascot-wing-right">
            <path d="M96 56 q10 18 -2 34 q-10 6 -12 -6 q8 -14 4 -28 Z" fill="#7c3aed" />
          </g>

          {/* a tiny book, only in 'reading' */}
          <g className="mascot-book">
            <path d="M42 84 h16 v16 h-16 Z" fill="#22d3ee" />
            <path d="M62 84 h16 v16 h-16 Z" fill="#0ea5e9" />
            <path d="M60 83 v18" stroke="#150d33" strokeWidth="1.6" />
          </g>

          {/* face */}
          <g className="mascot-face">
            {/* eye whites */}
            <circle cx="47" cy="56" r="13" fill="#fdfcff" />
            <circle cx="73" cy="56" r="13" fill="#fdfcff" />

            {closedEyes ? (
              <g className="mascot-eyes-closed">
                {safeMood === 'happy' ? (
                  <>
                    <path d="M39 58 q8 -10 16 0" className="mascot-lash" />
                    <path d="M65 58 q8 -10 16 0" className="mascot-lash" />
                  </>
                ) : (
                  <>
                    <path d="M39 56 q8 6 16 0" className="mascot-lash" />
                    <path d="M65 56 q8 6 16 0" className="mascot-lash" />
                  </>
                )}
              </g>
            ) : (
              <g ref={pupilsRef} className="mascot-pupils">
                <circle cx="47" cy="56" r="6.4" fill="#1d1147" />
                <circle cx="73" cy="56" r="6.4" fill="#1d1147" />
                <circle cx="49.4" cy="53.4" r="2.2" fill="#fdfcff" />
                <circle cx="75.4" cy="53.4" r="2.2" fill="#fdfcff" />
              </g>
            )}

            {/* beak */}
            <path d="M60 64 l-7 8 h14 Z" fill="#fbbf24" />

            {/* blush */}
            <ellipse cx="35" cy="70" rx="6" ry="4" fill="#f472b6" opacity="0.5" />
            <ellipse cx="85" cy="70" rx="6" ry="4" fill="#f472b6" opacity="0.5" />
          </g>

          {/* thought sparkles, only in 'thinking' */}
          <g className="mascot-think">
            <circle cx="96" cy="30" r="3" fill="#22d3ee" />
            <circle cx="105" cy="20" r="4.5" fill="#22d3ee" opacity="0.7" />
            <circle cx="113" cy="9" r="6" fill="#22d3ee" opacity="0.45" />
          </g>

          {/* celebration sparks, only in 'happy' */}
          <g className="mascot-spark">
            <path d="M18 26 l2.4 5.6 l5.6 2.4 l-5.6 2.4 l-2.4 5.6 l-2.4 -5.6 l-5.6 -2.4 l5.6 -2.4 Z" fill="#fbbf24" />
            <path d="M100 76 l1.8 4.2 l4.2 1.8 l-4.2 1.8 l-1.8 4.2 l-1.8 -4.2 l-4.2 -1.8 l4.2 -1.8 Z" fill="#f472b6" />
          </g>
        </g>
      </svg>
    </div>
  )
}

export default memo(Mascot)
