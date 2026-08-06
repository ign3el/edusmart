import { Canvas } from '@react-three/fiber'
import { Suspense, useState } from 'react'
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
  // There was an isMobile breakpoint watcher here purely to downgrade antialias
  // and dpr on phones. Now that neither is downgraded (see the Canvas below) it
  // had no readers left, so the state and its resize listener go with it rather
  // than sitting around re-rendering the canvas on every resize for nothing.
  const [prefersReducedMotion] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
  const [webglOk] = useState(hasWebGL)

  if (prefersReducedMotion || !webglOk) return null

  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 0, pointerEvents: 'none' }}>
      <Canvas
        camera={{ position: [0, 0, 48], fov: 38 }}
        // Antialiasing and device-pixel resolution are NOT a mobile luxury here,
        // they are the feature. This canvas renders one slowly rotating textured
        // quad, and its four edges are hard geometry: with antialias off at dpr 1
        // those edges stair-step, and because the plane is always in motion the
        // steps crawl along the edge every frame - the "wavy borders" this used to
        // show on a phone. It was disabled as a blanket perf reflex, but the
        // canvas is ~340 CSS px square: even at dpr 2 with MSAA that is under half
        // a megapixel and 3 quads, which is nothing on any phone this app targets.
        // dpr stays capped at 2 rather than following a 3x-DPR screen - that is
        // where the real cost would be, and 2 is already past the point where the
        // edge reads as clean.
        gl={{ antialias: true, alpha: true }}
        style={{ width: '100%', height: '100%' }}
        dpr={[1, 2]}
        frameloop="always"
      >
        <CameraRig sceneIndex={sceneIndex} />
        <Suspense fallback={null}>{children}</Suspense>
      </Canvas>
    </div>
  )
}

export default StorySceneCanvas
