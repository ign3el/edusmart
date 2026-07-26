import { useRef, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'

// Small floating particles around the scene
export function SceneParticles({ count = 50 }) {
  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3)
    for (let i = 0; i < count * 3; i += 3) {
      arr[i] = (Math.random() - 0.5) * 30
      arr[i + 1] = (Math.random() - 0.5) * 20
      arr[i + 2] = (Math.random() - 0.5) * 15 - 5
    }
    return arr
  }, [count])

  const ref = useRef()
  useFrame((state) => {
    if (ref.current) {
      ref.current.rotation.y = state.clock.elapsedTime * 0.02
    }
  })

  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" array={positions} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial
        size={0.15}
        transparent
        opacity={0.4}
        color="#c4b5fd"
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  )
}
