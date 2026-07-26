import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { AdditiveBlending } from 'three'
import { SceneParticles } from './SceneParticles'
import { StorySceneImagePlane } from './StorySceneImagePlane'

// Depth-layered scene for story player: background wash, mid glow ring, the real
// scene image as a tilting/parallax 3D plane (StorySceneImagePlane), ambient particles.
export function StoryDepthScene({ imageUrl, prevImageUrl, isMobile, isPlaying, turnDir = 1, pointerTiltRef }) {
  const groupRef = useRef()

  // Subtle floating animation — bounded oscillation, not unbounded spin, since the
  // real scene image now lives in this group and needs to stay legible/front-facing
  // (StorySceneImagePlane already provides its own tilt/parallax motion).
  useFrame((state) => {
    if (groupRef.current) {
      groupRef.current.rotation.y = Math.sin(state.clock.elapsedTime * 0.15) * 0.04
      groupRef.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.5) * 0.02
      groupRef.current.position.y = Math.cos(state.clock.elapsedTime * 0.3) * 0.1
    }
  })

  return (
    <>
      {/* Ambient sway - decorative backdrop only. The scene image plane used to
          live inside this same swaying group, so its constant rotation/position
          bob compounded with the image plane's own tilt rotation (parent and
          child transforms multiply) - silently eating into the coverage margin
          computed for that tilt alone, and unlike user-driven tilt this sway
          never stops, so the edges looked "distorted" even sitting still.
          Pulled the image out to its own group below so it only ever moves by
          its own, already-accounted-for tilt. */}
      <group ref={groupRef}>
        {/* BACKGROUND LAYER - blurred gradient backdrop */}
        <mesh position={[0, 0, -3]} scale={1.3}>
          <planeGeometry args={[18, 10]} />
          <meshBasicMaterial
            color="#1d1147"
            transparent
            opacity={0.6}
            side={2}
          />
        </mesh>

        {/* MID LAYER - two layered glow rings in the app's accent tones, brighten
            while the story is playing, so the image reads as lit rather than pasted
            onto a flat backdrop. */}
        <mesh position={[0, 0, -1.2]} scale={1.12}>
          <planeGeometry args={[16.5, 9.5]} />
          <meshBasicMaterial
            color="#8b5cf6"
            transparent
            opacity={isPlaying ? 0.13 : 0.05}
            side={2}
            depthWrite={false}
            blending={AdditiveBlending}
          />
        </mesh>
        <mesh position={[0, 0, -0.9]} scale={1.02}>
          <planeGeometry args={[15.5, 8.8]} />
          <meshBasicMaterial
            color="#f472b6"
            transparent
            opacity={isPlaying ? 0.1 : 0.04}
            side={2}
            depthWrite={false}
            blending={AdditiveBlending}
          />
        </mesh>
      </group>

      {/* FOREGROUND - the real scene image as a tilting/parallax 3D plane,
          outside the ambient-sway group on purpose (see comment above) */}
      <StorySceneImagePlane
        imageUrl={imageUrl}
        prevImageUrl={prevImageUrl}
        turnDir={turnDir}
        isMobile={isMobile}
        isPlaying={isPlaying}
        pointerTiltRef={pointerTiltRef}
      />

      {/* PARTICLE LAYER - ambient floating particles */}
      <SceneParticles count={isMobile ? 22 : 50} />
    </>
  )
}
