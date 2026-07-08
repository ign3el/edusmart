import { useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import { Float } from '@react-three/drei'

export function FloatingBooks() {
  const books = useMemo(() => 
    Array.from({ length: 8 }, (_, i) => ({
      id: i,
      x: (Math.random() - 0.5) * 60,
      y: (Math.random() - 0.5) * 60,
      z: (Math.random() - 0.5) * 60,
      rotX: Math.random() * 0.3 - 0.15,
      rotY: Math.random() * Math.PI * 2,
      rotZ: Math.random() * 0.3 - 0.15,
      floatSpeed: 0.5 + Math.random() * 0.5,
      floatOffset: Math.random() * Math.PI * 2,
      scale: 0.8 + Math.random() * 0.4,
      color: ['#6366f1', '#06b6d4', '#10b981', '#818cf8', '#22d3ee', '#f59e0b', '#10b981', '#f97316'][i % 8],
    }))
  , [])

  return (
    <group>
      {books.map(({ 
        id, x, y, z, rotX, rotY, rotZ, floatSpeed, floatOffset, scale, color 
      }) => (
        <Float 
          key={id} 
          position={[x, y, z]}
          rotationIntensity={0.3}
          floatIntensity={1.5}
          speed={floatSpeed}
          phaseOffset={floatOffset}
        >
          <Book 
            rotation={[rotX, rotY, rotZ]} 
            scale={scale}
            color={color}
          />
        </Float>
      ))}
    </group>
  )
}

function Book({ rotation, scale, color }) {
  return (
    <group rotation={rotation} scale={scale}>
      {/* Book cover */}
      <mesh position={[0, 0, 0.15]} rotation={[0, 0, 0]}>
        <boxGeometry args={[1.2, 1.6, 0.3]} />
        <meshStandardMaterial
          color={color}
          roughness={0.3}
          metalness={0.1}
          clearcoat={0.5}
          clearcoatRoughness={0.2}
        />
      </mesh>
      
      {/* Book pages */}
      <mesh position={[0, 0, -0.15]}>
        <boxGeometry args={[1.15, 1.55, 0.25]} />
        <meshStandardMaterial
          color="#f5f0e1"
          roughness={0.8}
          metalness={0}
        />
      </mesh>
      
      {/* Spine */}
      <mesh position={[0.6, 0, 0]} rotation={[0, Math.PI / 2, 0]}>
        <boxGeometry args={[0.3, 1.6, 0.05]} />
        <meshStandardMaterial
          color={color}
          roughness={0.3}
          metalness={0.1}
        />
      </mesh>
    </group>
  )
}

export default FloatingBooks