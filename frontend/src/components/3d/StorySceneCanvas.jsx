import { Canvas } from '@react-three/fiber'
import { Suspense, useEffect, useState } from 'react'
import { CameraRig } from './CameraRig'

function hasWebGL() {
  if (typeof document === 'undefined') return false
  try {
    const canvas = document.createElement('canvas')
    return !!(canvas.getContext('webgl') || canvas.getContext('experimental-webgl'))
  } catch {
    return false
  }
}

// Container-scoped 3D canvas (absolute, not fixed) for embedding an ambient
// depth layer behind a specific piece of UI, e.g. the story player's scene image.
// Returns null when reduced-motion is requested or WebGL isn't available, so the
// caller can fall back to a flat <img>.
export function StorySceneCanvas({ children, sceneIndex = 0 }) {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' && window.innerWidth < 768
  )
  const [prefersReducedMotion] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
  const [webglOk] = useState(hasWebGL)

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  if (prefersReducedMotion || !webglOk) return null

  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 0, pointerEvents: 'none' }}>
      <Canvas
        camera={{ position: [0, 0, 30], fov: 60 }}
        gl={{ antialias: !isMobile, alpha: true }}
        style={{ width: '100%', height: '100%' }}
        dpr={isMobile ? 1 : [1, 2]}
        frameloop="always"
      >
        <CameraRig sceneIndex={sceneIndex} />
        <Suspense fallback={null}>{children}</Suspense>
      </Canvas>
    </div>
  )
}

export default StorySceneCanvas
