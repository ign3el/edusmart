import { useMemo } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Stars, Html, Float } from '@react-three/drei'
import { useEffect, useRef } from 'react'

// Floating particles in the background
function Particles({ count = 200, spread = 50, speed = 0.05 }) {
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
        color="#8b5cf6"
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  )
}

// Aurora-like gradient planes
function AuroraPlanes() {
  const colors = ['#8b5cf6', '#22d3ee', '#fbbf24', '#f472b6', '#c4b5fd']
  
  return (
    <group>
      {colors.map((color, i) => (
        <mesh key={i} position={[0, 0, -15 - i * 5]} rotation={[-Math.PI / 2, 0, 0]} scale={40 + i * 10}>
          <planeGeometry args={[1, 1, 64, 64]} />
          <meshBasicMaterial
            color={color}
            transparent
            opacity={0.03}
            side={2}
            depthWrite={false}
          />
        </mesh>
      ))}
    </group>
  )
}

// Individual floating shape with animated rotation
function FloatingShape({ rotSpeed, scale, type, color }) {
  const meshRef = useRef()
  useFrame((state) => {
    if (meshRef.current) {
      meshRef.current.rotation.y = state.clock.elapsedTime * rotSpeed
      meshRef.current.rotation.x = Math.sin(state.clock.elapsedTime * rotSpeed * 0.5) * 0.3
    }
  })
  return (
    <mesh ref={meshRef} scale={scale}>
      {type === 0 && <boxGeometry args={[1, 1, 1]} />}
      {type === 1 && <sphereGeometry args={[0.8, 16, 16]} />}
      {type === 2 && <torusGeometry args={[0.6, 0.2, 16, 32]} />}
      <meshStandardMaterial
        color={color}
        transparent
        opacity={0.15}
        roughness={0.1}
        metalness={0.2}
      />
    </mesh>
  )
}

// Floating geometric shapes
function FloatingShapes() {
  const shapes = useMemo(() => 
    Array.from({ length: 12 }, (_, i) => ({
      id: i,
      x: (Math.random() - 0.5) * 80,
      y: (Math.random() - 0.5) * 80,
      z: (Math.random() - 0.5) * 80,
      rotSpeed: Math.random() * 2 + 1,
      scale: Math.random() * 0.5 + 0.5,
      type: Math.floor(Math.random() * 3), // 0: box, 1: sphere, 2: torus
      color: ['#8b5cf6', '#f472b6', '#fbbf24'][i % 3],
    }))
  , [])

  return (
    <group>
      {shapes.map(({ id, x, y, z, rotSpeed, scale, type, color }) => (
        <Float key={id} rotationIntensity={0.5} floatIntensity={2} position={[x, y, z]}>
          <FloatingShape rotSpeed={rotSpeed} scale={scale} type={type} color={color} />
        </Float>
      ))}
    </group>
  )
}



export function Scene3DBackground({ className = '', style = {} }) {
  const prefersReducedMotion = typeof window !== 'undefined' && 
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768

  if (prefersReducedMotion) {
    return (
      <div className={className} style={{ ...style, width: '100%', height: '100%' }}>
      </div>
    )
  }

  return (
    <div className={className} style={{ ...style, width: '100%', height: '100%' }}>
      <Canvas
        camera={{ position: [0, 0, 30], fov: 60 }}
        style={{ width: '100%', height: '100%', display: 'block' }}
        // Uncapped DPR on a 3x phone screen means ~9x the fragments for a
        // backdrop nobody is inspecting, and antialiasing costs another full
        // resolve pass that is invisible on soft particles.
        dpr={isMobile ? 1 : [1, 1.75]}
        gl={{ antialias: !isMobile, alpha: true, preserveDrawingBuffer: false }}
      >
        <color attach="background" args={['#1d1147']} />
        <fog attach="fog" args={['#1d1147', 10, 100]} />
        
        {/* Stars from drei */}
        <Stars 
          radius={100} 
          depth={100} 
          count={isMobile ? 700 : 2000} 
          saturation={0.2} 
          factor={4} 
          size={0.5}
          color="#8b5cf6"
        />
        
        {/* Custom particles */}
        <Particles count={isMobile ? 120 : 300} spread={80} speed={0.02} />
        
        {/* Aurora gradient planes */}
        <AuroraPlanes />
        
        {/* Floating geometric shapes */}
        <FloatingShapes />
        
        {/* Subtle ambient light */}
        <ambientLight intensity={0.3} color="#8b5cf6" />
        <directionalLight position={[10, 10, 5]} intensity={0.5} color="#8b5cf6" />
        <pointLight position={[-10, -10, 10]} intensity={0.3} color="#22d3ee" />
        

      </Canvas>
    </div>
  )
}

export default Scene3DBackground