import { useEffect, useRef } from 'react'
import { animate } from 'animejs'

/**
 * App wordmark with a continuous idle glow, driven by Anime.js (rAF-based)
 * rather than a Framer Motion loop or CSS @keyframes — both of those either
 * freeze under prefers-reduced-motion / mobile Chrome, or risk clobbering
 * the Tailwind entry stylesheet if added as global keyframes.
 */
function BrandMark({ className = '' }) {
  const glowRef = useRef(null)

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (prefersReducedMotion || !glowRef.current) return

    const animation = animate(glowRef.current, {
      opacity: [0.55, 1, 0.55],
      filter: ['drop-shadow(0 0 6px rgba(99,102,241,0.35))', 'drop-shadow(0 0 16px rgba(6,182,212,0.55))', 'drop-shadow(0 0 6px rgba(99,102,241,0.35))'],
      duration: 3200,
      easing: 'easeInOutSine',
      loop: true,
    })

    return () => animation.pause()
  }, [])

  return (
    <h1 ref={glowRef} className={`brand-mark ${className}`}>
      EduSmart
    </h1>
  )
}

export default BrandMark
