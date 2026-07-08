import { useMemo, useState } from 'react'
import { useFrame } from '@react-three/fiber'

export function AuroraBackground() {
  const colors = ['#6366f1', '#06b6d4', '#10b981', '#818cf8', '#22d3ee']
  
  // Create multiple gradient planes at different depths
  const planes = useMemo(() => 
    colors.map((color, i) => ({
      color,
      z: -15 - i * 5,
      scale: 40 + i * 10,
      opacity: 0.02 + i * 0.01,
      speed: 0.1 + i * 0.05,
    }))
  , [])

  return (
    <group>
      {planes.map(({ color, z, scale, opacity, speed }, i) => (
        <AuroraPlane
          key={i}
          color={color}
          z={z}
          scale={scale}
          opacity={opacity}
          speed={speed}
        />
      ))}
    </group>
  )
}

function AuroraPlane({ color, z, scale, opacity, speed }) {
  const [phase, setPhase] = useState(0)
  
  useFrame((state, delta) => {
    setPhase(p => p + delta * speed)
  })

  return (
    <mesh 
      position={[0, 0, z]} 
      rotation={[-Math.PI / 2, 0, 0]} 
      scale={scale}
    >
      <planeGeometry args={[1, 1, 64, 64]} />
      <meshBasicMaterial
        color={color}
        transparent
        opacity={opacity}
        side={2}
        depthWrite={false}
      />
    </mesh>
  )
}

export default AuroraBackground