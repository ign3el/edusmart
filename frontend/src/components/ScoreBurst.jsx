import { useEffect, useRef } from 'react'
import { animate } from 'animejs'

const COLORS = ['#8b5cf6', '#22d3ee', '#fbbf24', '#f472b6', '#c4b5fd']
const PARTICLE_COUNT = 16

function ScoreBurst() {
  const containerRef = useRef(null)

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (prefersReducedMotion || !containerRef.current) return

    const particles = containerRef.current.querySelectorAll('.score-burst-particle')
    particles.forEach((particle, i) => {
      const angle = (i / particles.length) * Math.PI * 2
      const distance = 70 + Math.random() * 40
      animate(particle, {
        translateX: Math.cos(angle) * distance,
        translateY: Math.sin(angle) * distance,
        scale: [0, 1, 0],
        opacity: [1, 1, 0],
        duration: 900 + Math.random() * 400,
        delay: i * 15,
        easing: 'easeOutCubic',
      })
    })
  }, [])

  return (
    <div ref={containerRef} className="score-burst" aria-hidden="true">
      {Array.from({ length: PARTICLE_COUNT }).map((_, i) => (
        <span
          key={i}
          className="score-burst-particle"
          style={{ background: COLORS[i % COLORS.length] }}
        />
      ))}
    </div>
  )
}

export default ScoreBurst
