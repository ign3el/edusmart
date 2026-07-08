import { useMemo } from 'react'

// Floating particles in the background
export function ParticlesLayer({ count = 200, spread = 50, speed = 0.05 }) {
  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3)
    for (let i = 0; i < count * 3; i += 3) {
      arr[i] = (Math.random() - 0.5) * spread
      arr[i + 1] = (Math.random() - 0.5) * spread
      arr[i + 2] = (Math.random() - 0.5) * spread
    }
    return arr
  }, [count, spread])
  return (
    <points>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" array={positions} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial
        size={0.3}
        transparent
        opacity={0.6}
        color="#6366f1"
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  )
}
