import { useRef, useState } from 'react'
import { motion, useMotionValue, useSpring } from 'framer-motion'
import { Maximize2, RotateCcw } from 'lucide-react'
import './Flip3DCard.css'

const prefersReducedMotion =
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

// Shared 3D card primitive for the admin panel: mouse-only live tilt on the
// front face, tap-to-flip (via an explicit handle, not the whole card, so
// buttons/inputs on the back face don't fight the flip gesture) everywhere.
function Flip3DCard({ front, back, className = '', frontLabel = 'View details', backLabel = 'Back to summary' }) {
  const [flipped, setFlipped] = useState(false)
  const cardRef = useRef(null)

  const rotateX = useMotionValue(0)
  const rotateY = useMotionValue(0)
  const springRotateX = useSpring(rotateX, { stiffness: 200, damping: 22 })
  const springRotateY = useSpring(rotateY, { stiffness: 200, damping: 22 })

  const handlePointerMove = (e) => {
    if (prefersReducedMotion || flipped || e.pointerType !== 'mouse' || !cardRef.current) return
    const rect = cardRef.current.getBoundingClientRect()
    const px = (e.clientX - rect.left) / rect.width - 0.5
    const py = (e.clientY - rect.top) / rect.height - 0.5
    rotateY.set(px * 14)
    rotateX.set(-py * 14)
  }

  const resetTilt = () => {
    rotateX.set(0)
    rotateY.set(0)
  }

  const toggleFlip = () => {
    resetTilt()
    setFlipped((f) => !f)
  }

  return (
    <div className={`flip3d-outer ${flipped ? 'flip3d-outer--expanded' : ''} ${className}`}>
      <motion.div
        ref={cardRef}
        className="flip3d-tilt"
        style={{ rotateX: springRotateX, rotateY: springRotateY }}
        onPointerMove={handlePointerMove}
        onPointerLeave={resetTilt}
      >
        <motion.div
          className="flip3d-flip"
          animate={{ rotateY: flipped ? 180 : 0 }}
          transition={prefersReducedMotion ? { duration: 0 } : { type: 'spring', stiffness: 260, damping: 28 }}
        >
          <div className="flip3d-face flip3d-front">
            {front}
            <motion.button
              type="button"
              className="flip3d-handle"
              onClick={toggleFlip}
              whileTap={{ scale: 0.9 }}
              aria-label={frontLabel}
            >
              <Maximize2 size={15} />
            </motion.button>
          </div>
          <div className="flip3d-face flip3d-back">
            {back}
            <motion.button
              type="button"
              className="flip3d-handle"
              onClick={toggleFlip}
              whileTap={{ scale: 0.9 }}
              aria-label={backLabel}
            >
              <RotateCcw size={15} />
            </motion.button>
          </div>
        </motion.div>
      </motion.div>
    </div>
  )
}

export default Flip3DCard
