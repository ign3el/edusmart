import { useMemo } from 'react'
import { Float } from '@react-three/drei'

// Floating geometric shapes
export function ShapesLayer({ count = 12 }) {
  const shapes = useMemo(() =>
    Array.from({ length: count }, (_, i) => ({
      id: i,
      x: (Math.random() - 0.5) * 80,
      y: (Math.random() - 0.5) * 80,
      z: (Math.random() - 0.5) * 80,
      rotSpeed: Math.random() * 0.002 + 0.001,
      scale: Math.random() * 0.5 + 0.5,
      type: Math.floor(Math.random() * 3), // 0: box, 1: sphere, 2: torus
    }))
  , [count])

  return (
    <group>
      {shapes.map(({ id, x, y, z, rotSpeed, scale, type }) => (
        <Float key={id} rotationIntensity={0.5} floatIntensity={2} position={[x, y, z]}>
          <mesh rotation={[0, rotSpeed * performance.now() * 0.001, 0]} scale={scale}>
            {type === 0 && <boxGeometry args={[1, 1, 1]} />}
            {type === 1 && <sphereGeometry args={[0.8, 16, 16]} />}
            {type === 2 && <torusGeometry args={[0.6, 0.2, 16, 32]} />}
            <meshPhysicalMaterial
              color={['#6366f1', '#06b6d4', '#10b981'][id % 3]}
              transparent
              opacity={0.15}
              transmission={0.3}
              roughness={0.1}
              metalness={0.2}
              clearcoat={1}
              clearcoatRoughness={0.1}
            />
          </mesh>
        </Float>
      ))}
    </group>
  )
}
