import { Canvas } from '@react-three/fiber'
import { useState, useEffect, Suspense } from 'react'

export function R3FProvider({ children }) {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' && window.innerWidth < 768
  )

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none' }}>
      <Canvas
        camera={{ position: [0, 0, 30], fov: 60 }}
        gl={{ antialias: !isMobile, alpha: true }}
        style={{ width: '100%', height: '100%' }}
        dpr={isMobile ? 1 : [1, 2]}
        frameloop={isMobile ? 'demand' : 'always'}
      >
        <Suspense fallback={null}>{children}</Suspense>
      </Canvas>
    </div>
  )
}
