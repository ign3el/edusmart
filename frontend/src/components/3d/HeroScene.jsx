import { useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import * as THREE from 'three'

/**
 * The home screen's 3D backdrop: storybook pages drifting up through a violet
 * night, lit by soft coloured glows.
 *
 * Replaces Scene3DBackground on `home` only - it is NOT layered on top of it.
 * Two <Canvas> elements means two WebGL contexts, and mobile Chrome starts
 * evicting them (whichever it likes) once a few tabs are open.
 *
 * There is deliberately not a single light in this scene. Lighting costs a
 * shader branch per material per frame and buys nothing when every surface is
 * meant to emit rather than be lit; both textures below carry their own
 * shading, baked once into a canvas at startup.
 */

const PALETTE = ['#a78bfa', '#22d3ee', '#f472b6', '#fbbf24', '#8b5cf6']

/**
 * Radial-falloff sprite texture. This is the whole trick behind a soft glow:
 * a sphere with a flat material has a hard silhouette and reads as a grey ball,
 * because the edge terminates instantly. Alpha that decays to zero over the
 * full radius is what makes it read as light instead of geometry.
 */
function makeGlowTexture() {
  const c = document.createElement('canvas')
  c.width = c.height = 128
  const ctx = c.getContext('2d')
  const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64)
  g.addColorStop(0.0, 'rgba(255,255,255,0.95)')
  g.addColorStop(0.18, 'rgba(255,255,255,0.45)')
  g.addColorStop(0.45, 'rgba(255,255,255,0.13)')
  g.addColorStop(1.0, 'rgba(255,255,255,0)')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, 128, 128)
  const t = new THREE.CanvasTexture(c)
  t.needsUpdate = true
  return t
}

/**
 * A sheet of storybook paper, drawn once into a canvas: warm cream, rounded
 * corners, a spine shadow and a few ruled lines. The ruling is what makes a
 * 40px quad read as "a page" rather than "a rectangle" - without it these were
 * indistinguishable from debug geometry.
 */
function makePageTexture() {
  const c = document.createElement('canvas')
  c.width = 160
  c.height = 208
  const ctx = c.getContext('2d')

  ctx.fillStyle = '#fdf8ff'
  const r = 12
  ctx.beginPath()
  ctx.moveTo(r, 0)
  ctx.arcTo(160, 0, 160, 208, r)
  ctx.arcTo(160, 208, 0, 208, r)
  ctx.arcTo(0, 208, 0, 0, r)
  ctx.arcTo(0, 0, 160, 0, r)
  ctx.closePath()
  ctx.fill()

  // Spine shading down the left edge so the sheet has a direction.
  const spine = ctx.createLinearGradient(0, 0, 46, 0)
  spine.addColorStop(0, 'rgba(139,92,246,0.32)')
  spine.addColorStop(1, 'rgba(139,92,246,0)')
  ctx.fillStyle = spine
  ctx.fillRect(0, 0, 46, 208)

  // Ruled lines, with a short last line so it reads as a paragraph.
  ctx.strokeStyle = 'rgba(109,72,196,0.34)'
  ctx.lineWidth = 5
  ctx.lineCap = 'round'
  const lines = [58, 84, 110, 136, 162]
  lines.forEach((y, i) => {
    ctx.beginPath()
    ctx.moveTo(26, y)
    ctx.lineTo(i === lines.length - 1 ? 96 : 134, y)
    ctx.stroke()
  })

  const t = new THREE.CanvasTexture(c)
  t.needsUpdate = true
  return t
}

/** A single sheet, tumbling slowly as it rises. */
function Page({ seed, map }) {
  const ref = useRef()
  const cfg = useMemo(() => ({
    x: (seed.x - 0.5) * 36,
    z: -4 - seed.z * 20,
    speed: 0.75 + seed.s * 0.9,
    spin: (seed.r - 0.5) * 0.3,
    tilt: seed.t * Math.PI,
    scale: 1.7 + seed.sc * 1.6,
    offset: seed.o * 40,
    peak: 0.5 + seed.sc * 0.42,
  }), [seed])

  useFrame((state) => {
    const m = ref.current
    if (!m) return
    const t = state.clock.elapsedTime
    // Wrap through a 40-unit column rather than respawning: modulo is free, and
    // the loop is invisible because every page carries its own phase offset.
    const y = ((t * cfg.speed + cfg.offset) % 40) - 20
    m.position.set(cfg.x + Math.sin(t * 0.3 + cfg.offset) * 2, y, cfg.z)
    m.rotation.y = cfg.tilt + t * cfg.spin
    m.rotation.z = Math.sin(t * 0.4 + cfg.offset) * 0.22
    // Fade in low, out high, so nothing ever pops at the frame edge.
    m.material.opacity = Math.max(0, cfg.peak * (1 - Math.abs(y) / 20))
  })

  return (
    <mesh ref={ref} scale={cfg.scale}>
      <planeGeometry args={[1, 1.3]} />
      <meshBasicMaterial
        map={map}
        transparent
        opacity={0.5}
        side={THREE.DoubleSide}
        depthWrite={false}
        toneMapped={false}
      />
    </mesh>
  )
}

/** Soft coloured light. Additive sprite, so it blends into the violet ground. */
function Glow({ color, position, scale, phase, map }) {
  const ref = useRef()
  useFrame((state) => {
    const s = ref.current
    if (!s) return
    const t = state.clock.elapsedTime
    s.position.y = position[1] + Math.sin(t * 0.35 + phase) * 3.2
    s.position.x = position[0] + Math.cos(t * 0.22 + phase) * 2.6
    const k = scale * (1 + Math.sin(t * 0.5 + phase) * 0.09)
    s.scale.set(k, k, 1)
  })
  return (
    <sprite ref={ref} position={position} scale={[scale, scale, 1]}>
      <spriteMaterial
        map={map}
        color={color}
        transparent
        opacity={0.85}
        blending={THREE.AdditiveBlending}
        depthWrite={false}
        depthTest={false}
        toneMapped={false}
      />
    </sprite>
  )
}

/** Sparse drifting dust. One draw call for the lot. */
function Dust({ count, map }) {
  const ref = useRef()
  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3)
    for (let i = 0; i < count * 3; i += 3) {
      arr[i] = (Math.random() - 0.5) * 62
      arr[i + 1] = (Math.random() - 0.5) * 46
      arr[i + 2] = (Math.random() - 0.5) * 26 - 4
    }
    return arr
  }, [count])

  useFrame((state) => {
    if (ref.current) ref.current.rotation.y = state.clock.elapsedTime * 0.015
  })

  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial
        map={map}
        size={0.55}
        color="#ddd6fe"
        transparent
        opacity={0.9}
        sizeAttenuation
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  )
}

/** Eases the whole scene toward the pointer. Parallax sells depth for free. */
function Parallax({ children, strength }) {
  const ref = useRef()

  useFrame((state) => {
    const g = ref.current
    if (!g) return
    g.rotation.y += (state.pointer.x * strength * 0.06 - g.rotation.y) * 0.04
    g.rotation.x += (-state.pointer.y * strength * 0.05 - g.rotation.x) * 0.04
  })

  return <group ref={ref}>{children}</group>
}

function HeroContent({ mobile }) {
  // Both textures are built once and shared by every instance. Building them
  // per-mesh would mean 16 canvas uploads to the GPU on mount.
  const glowMap = useMemo(makeGlowTexture, [])
  const pageMap = useMemo(makePageTexture, [])

  const pages = useMemo(
    () => Array.from({ length: mobile ? 9 : 16 }, () => ({
      x: Math.random(), z: Math.random(), s: Math.random(), r: Math.random(),
      t: Math.random(), sc: Math.random(), o: Math.random(),
    })),
    [mobile]
  )

  const glows = useMemo(() => PALETTE.map((color, i) => ({
    color,
    position: [(i - 2) * 12, i % 2 ? 8 : -8, -16 - i * 2],
    scale: 26 + (i % 3) * 8,
    phase: i * 1.3,
  })), [])

  return (
    <Parallax strength={mobile ? 0.4 : 1}>
      {glows.map((g, i) => <Glow key={i} {...g} map={glowMap} />)}
      <Dust count={mobile ? 90 : 240} map={glowMap} />
      {pages.map((seed, i) => <Page key={i} seed={seed} map={pageMap} />)}
    </Parallax>
  )
}

export function HeroScene({ className = '' }) {
  const reduced = typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const mobile = typeof window !== 'undefined' && window.innerWidth < 768

  // A static violet wash is a perfectly good hero. Spinning up a WebGL context
  // to render nothing for someone who asked for no motion is not.
  if (reduced) return <div className={`${className} hero-scene-static`} />

  return (
    <div className={className}>
      <Canvas
        camera={{ position: [0, 0, 26], fov: 55 }}
        // Capped DPR: a OnePlus 15 reports devicePixelRatio 3, which would push
        // ~9x the fragments of a 1x render for a backdrop of soft gradients.
        dpr={mobile ? 1 : [1, 1.75]}
        gl={{ antialias: !mobile, alpha: true, powerPreference: 'high-performance' }}
        style={{ width: '100%', height: '100%', display: 'block' }}
      >
        <color attach="background" args={['#150d33']} />
        {/* Fog starts well past the pages. Pulled any closer it desaturates the
            whole scene toward the background and everything turns grey. */}
        <fog attach="fog" args={['#150d33', 34, 88]} />
        <HeroContent mobile={mobile} />
      </Canvas>
    </div>
  )
}

export default HeroScene
