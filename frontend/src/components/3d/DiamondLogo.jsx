import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Float } from '@react-three/drei'

export function DiamondLogo({ position = [0, 3, 5], scale = 1 }) {
  const meshRef = useRef()
  const glowRef = useRef()

  useFrame((state) => {
    if (meshRef.current) {
      meshRef.current.rotation.y = state.clock.elapsedTime * 0.3
      meshRef.current.rotation.z = Math.sin(state.clock.elapsedTime * 0.5) * 0.1
    }
    if (glowRef.current) {
      glowRef.current.material.opacity = 0.1 + Math.sin(state.clock.elapsedTime * 2) * 0.05
    }
  })

  return (
    <Float speed={1.5} rotationIntensity={0.2} floatIntensity={0.5}>
      <group position={position} scale={scale}>
        <mesh ref={meshRef}>
          <octahedronGeometry args={[1, 0]} />
          <meshPhysicalMaterial
            color="#6366f1" metalness={0.8} roughness={0.1}
            transmission={0.3} clearcoat={1}
            emissive="#6366f1" emissiveIntensity={0.2}
          />
        </mesh>
        <mesh ref={glowRef} scale={1.5}>
          <sphereGeometry args={[1, 16, 16]} />
          <meshBasicMaterial color="#6366f1" transparent opacity={0.1} side={2} depthWrite={false} />
        </mesh>
      </group>
    </Float>
  )
}
