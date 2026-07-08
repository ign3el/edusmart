import { Stars } from '@react-three/drei'
import { ParticlesLayer } from './ParticlesLayer'
import { AuroraLayer } from './AuroraLayer'
import { ShapesLayer } from './ShapesLayer'

export function SceneContent() {
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768
  return (
    <>
      <color attach="background" args={['#0b0f1a']} />
      <fog attach="fog" args={['#0b0f1a', 10, 100]} />
      <Stars radius={100} depth={100} count={isMobile ? 500 : 2000} saturation={0.2} factor={4} size={0.5} color="#6366f1" />
      <ParticlesLayer count={isMobile ? 100 : 300} />
      <AuroraLayer />
      <ShapesLayer count={isMobile ? 6 : 12} />
      <ambientLight intensity={0.3} color="#6366f1" />
      <directionalLight position={[10, 10, 5]} intensity={0.5} color="#6366f1" />
      <pointLight position={[-10, -10, 10]} intensity={0.3} color="#06b6d4" />
    </>
  )
}
