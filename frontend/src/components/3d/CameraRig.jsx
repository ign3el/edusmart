import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'

// A long lens, not a wide one. fov was 60, which is a 3mm-equivalent view: a
// plane that fills the frame keystones violently the instant it tilts, its near
// edge ballooning outside the frustum. That perspective error is what forced
// StorySceneImagePlane to shrink the picture ~8% just to keep it on screen (the
// "image doesn't fill the div" complaint), and the ballooning itself is the
// "distortion at the borders" one. Narrowing to 38 and backing the camera off to
// hold the same framing cuts the error by ~40% and lets the picture sit
// full-bleed. Distance and fov move together — halfExtent = dist * tan(fov/2) —
// so the composition is unchanged; only the amount of wide-angle stretch is.
const BASE_Z = 48
const BASE_FOV = 38

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
    // Every amplitude below is the old one scaled by 48/30, so the camera sweeps
    // the same ANGLE as before from its new distance and the bob reads identically.
    // Scaling them is not optional: left at the old absolute values they would
    // shrink to a third of their former effect at this distance and the rig would
    // look frozen.
    const targetX = sceneOffset(sceneIndex, 1.7) * 1.36 + Math.cos(t * 0.3) * 0.32
    const targetY = sceneOffset(sceneIndex, 3.1) * 0.88 + Math.sin(t * 0.4) * 0.32
    const targetZ = BASE_Z + Math.sin(t * 0.17) * 2.4
    const targetFov = BASE_FOV + Math.sin(t * 0.22) * 1.4

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
