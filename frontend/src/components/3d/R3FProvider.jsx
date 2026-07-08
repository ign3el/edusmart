import { Canvas } from '@react-three/fiber'
import { Suspense } from 'react'

export function R3FProvider({ children }) {
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none' }}>
      <Canvas
        camera={{ position: [0, 0, 30], fov: 60 }}
        gl={{ antialias: !isMobile, alpha: true }}
        style={{ width: '100%', height: '100%' }}
        dpr={isMobile ? 1 : [1, 2]}
      >
        <Suspense fallback={null}>{children}</Suspense>
      </Canvas>
    </div>
  )
}
