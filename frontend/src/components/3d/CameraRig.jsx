import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'

const BASE_Z = 30
const BASE_FOV = 60

// Deterministic per-scene camera personality (no authored data needed) blended
// with a continuous gentle bob, so every scene feels alive and slightly distinct
// without ever moving so much it fights the image-plane tilt.
function sceneOffset(sceneIndex, seed) {
  return Math.sin((sceneIndex + 1) * 2.399 * seed)
}

export function CameraRig({ sceneIndex = 0 }) {
  const smoothed = useRef({ x: 0, y: 0, z: BASE_Z, fov: BASE_FOV })

  useFrame(({ camera, clock }) => {
    const t = clock.elapsedTime
    const targetX = sceneOffset(sceneIndex, 1.7) * 0.85 + Math.cos(t * 0.3) * 0.2
    const targetY = sceneOffset(sceneIndex, 3.1) * 0.55 + Math.sin(t * 0.4) * 0.2
    const targetZ = BASE_Z + Math.sin(t * 0.17) * 1.5
    const targetFov = BASE_FOV + Math.sin(t * 0.22) * 2.2

    smoothed.current.x = THREE.MathUtils.lerp(smoothed.current.x, targetX, 0.03)
    smoothed.current.y = THREE.MathUtils.lerp(smoothed.current.y, targetY, 0.03)
    smoothed.current.z = THREE.MathUtils.lerp(smoothed.current.z, targetZ, 0.02)
    smoothed.current.fov = THREE.MathUtils.lerp(smoothed.current.fov, targetFov, 0.03)

    camera.position.set(smoothed.current.x, smoothed.current.y, smoothed.current.z)
    camera.lookAt(0, 0, 0)
    camera.fov = smoothed.current.fov
    camera.updateProjectionMatrix()
  })

  return null
}

export default CameraRig
