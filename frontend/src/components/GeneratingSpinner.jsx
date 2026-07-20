import { useEffect, useRef } from 'react'
import { animate } from 'animejs'
import { Sparkles } from 'lucide-react'
import './GeneratingSpinner.css'

function GeneratingSpinner() {
  const haloRef = useRef(null)

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (prefersReducedMotion || !haloRef.current) return
    const animation = animate(haloRef.current, {
      opacity: [0.35, 0.85, 0.35],
      scale: [0.9, 1.12, 0.9],
      duration: 2200,
      easing: 'easeInOutSine',
      loop: true,
    })
    return () => animation.pause()
  }, [])

  return (
    <div className="generating-spinner">
      <div ref={haloRef} className="generating-spinner-halo" />
      <div className="generating-spinner-ring" />
      <Sparkles className="generating-spinner-icon" size={26} aria-hidden="true" />
    </div>
  )
}

export default GeneratingSpinner
