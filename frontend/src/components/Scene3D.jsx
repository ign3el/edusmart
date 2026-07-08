import { useRef, useState } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html } from '@react-three/drei'

export function Scene3D({ scene, imageUrl, imageLoaded, imageError, isMobile, sceneIndex }) {
  const meshRef = useRef()
  const [hovered, setHovered] = useState(false)

  // Subtle floating animation
  useFrame((state, delta) => {
    if (meshRef.current) {
      meshRef.current.rotation.y += delta * 0.05
      meshRef.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.5) * 0.02
      meshRef.current.position.y = Math.cos(state.clock.elapsedTime * 0.3) * 0.1
    }
  })

  const hasImage = !imageError && imageUrl

  return (
    <group ref={meshRef}>
      {/* Scene image plane */}
      <mesh
        onPointerOver={() => setHovered(true)}
        onPointerOut={() => setHovered(false)}
        position={[0, 0, 0]}
      >
        <planeGeometry args={[16, 9, 32, 18]} />
        {hasImage ? (
          <meshStandardMaterial
            color="#6366f1"
            transparent
            opacity={0.15}
            side={2}
            roughness={0.3}
            metalness={0.1}
          />
        ) : (
          <meshStandardMaterial color="#1a1a3e" roughness={0.8} />
        )}
      </mesh>

      {/* Image as HTML overlay on top of the 3D plane */}
      {hasImage && (
        <Html transform position={[0, 0, 0.05]} style={{
          width: '100%',
          height: '100%',
          pointerEvents: 'none',
        }}>
          <img 
            src={imageUrl} 
            alt={`Scene ${sceneIndex + 1}`}
            style={{
              width: '100%',
              height: '100%',
              objectFit: 'cover',
              borderRadius: '8px',
            }}
          />
        </Html>
      )}

      {/* Fallback when no image */}
      {!hasImage && (
        <Html transform position={[0, 0, 0.1]} style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: '100%', height: '100%', pointerEvents: 'none',
        }}>
          <div style={{ color: '#6366f1', fontSize: '1.5rem', textAlign: 'center', padding: '2rem' }}>
            📖 Scene {sceneIndex + 1}
          </div>
        </Html>
      )}

      {/* Glow border */}
      <mesh position={[0, 0, -0.05]} scale={1.02}>
        <planeGeometry args={[16, 9]} />
        <meshBasicMaterial
          color="#6366f1"
          transparent
          opacity={hovered ? 0.3 : 0.1}
          side={2}
          depthWrite={false}
        />
      </mesh>

      {/* Floating glow */}
      <Html transform position={[0, 5, 0]} style={{ pointerEvents: 'none', width: 200, height: 200 }}>
        <div style={{
          position: 'absolute', inset: 0,
          background: 'radial-gradient(circle at center, rgba(99,102,241,0.1) 0%, transparent 70%)',
          borderRadius: '50%', filter: 'blur(40px)',
          animation: 'pulse 3s ease-in-out infinite',
        }} />
      </Html>
    </group>
  )
}

export default Scene3D
