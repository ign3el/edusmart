import { useRef, useState, useEffect, useMemo } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'

const TRANSITION_MS = 550
const BG_SCALE = 1.18
const BG_Z = -4.5
const BG_OPACITY = 0.34
const FG_TILT = 0.24
const BG_TILT = 0.08
const MAX_CACHED_TEXTURES = 16
// CameraRig continuously breathes camera z (BASE_Z=30 +/-1.5) and fov (BASE_FOV=60
// +/-2.2 deg) for per-scene "personality". That alone swings the true on-screen
// frustum size at the image plane's depth by up to ~9%, while fgW/fgH below is a
// fixed cover-fit snapshot taken once per texture - so the plane periodically
// undercovers the live (larger) frustum and exposes the blurred background layer
// at the edges. Add fixed headroom so the plane always outsizes the frustum
// across CameraRig's full breathing range, plus the smaller shrink tilt rotation
// itself causes. Reads as "distorted/blurry edges during 3D motion."
const TILT_OVERSCAN = 1.2
// How far a page hinges as it turns, in radians, and how far it slides sideways
// as a fraction of its own width. Tuned together: more yaw than this and the
// cover-fit plane turns edge-on and briefly disappears; more shift and it clears
// the frame before it has finished fading.
const PAGE_TURN_YAW = 0.85
const PAGE_TURN_SHIFT = 0.38

// Persistent texture cache, shared across scene changes and story replays for the
// life of the page. A story never exceeds 10 scenes, so this is small — LRU-capped
// so nothing unbounded accumulates across multiple stories played in one session.
// Without this, revisiting a scene (or replaying a fully-saved story) re-decoded
// and re-uploaded every image from scratch every single time.
const textureCache = new Map()
const cacheOrder = []
function touchCache(url) {
  const idx = cacheOrder.indexOf(url)
  if (idx !== -1) cacheOrder.splice(idx, 1)
  cacheOrder.push(url)
  while (cacheOrder.length > MAX_CACHED_TEXTURES) {
    const evictUrl = cacheOrder.shift()
    textureCache.get(evictUrl)?.dispose()
    textureCache.delete(evictUrl)
  }
}

// Loads a texture manually (not via drei's Suspense-based useTexture) so a scene
// change can crossfade the old texture out while the new one loads in, instead of
// the whole 3D layer blanking out while Suspense waits. Checks the persistent cache
// first — a cache hit resolves synchronously so the caller can skip the depth-swap
// fade entirely instead of replaying it for an image that's already fully loaded.
function useSceneTexture(url) {
  const [texture, setTexture] = useState(() => (url && textureCache.get(url)) || null)
  const wasCachedRef = useRef(!!(url && textureCache.get(url)))

  useEffect(() => {
    if (!url) { setTexture(null); return undefined }

    const cached = textureCache.get(url)
    if (cached) {
      touchCache(url)
      wasCachedRef.current = true
      setTexture(cached)
      return undefined
    }

    wasCachedRef.current = false
    let cancelled = false
    const loader = new THREE.TextureLoader()
    loader.setCrossOrigin('anonymous')
    loader.load(
      url,
      (tex) => {
        if (cancelled) { tex.dispose(); return }
        tex.colorSpace = THREE.SRGBColorSpace
        tex.minFilter = THREE.LinearFilter
        textureCache.set(url, tex)
        touchCache(url)
        setTexture(tex)
      },
      undefined,
      () => { if (!cancelled) setTexture(null) }
    )
    return () => { cancelled = true }
  }, [url])

  return [texture, wasCachedRef]
}

// Cover-fit, not contain-fit: generated scene images are square (512x512 on
// mobile), but the player's image container is a wide rectangle, so containing
// the square inside it left large letterbox gaps on the left/right that exposed
// the intentionally-blurred background parallax layer underneath — reported as
// "blurry edges," made worse by tilt animating that exposed boundary. Covering
// crops the square to fill the frame edge-to-edge instead, like object-fit: cover.
function fitPlaneSize(texture, viewport) {
  const img = texture?.image
  if (!img?.width || !img?.height) return [viewport.width, viewport.height]
  const imgAspect = img.width / img.height
  const viewAspect = viewport.width / viewport.height
  return imgAspect > viewAspect
    ? [viewport.height * imgAspect, viewport.height]
    : [viewport.width, viewport.width / imgAspect]
}

// A single image, rendered as background (soft, offset, low-parallax) + foreground
// (crisp, full-parallax) copies for a cheap layered-depth look from one texture,
// plus a hue-drifting additive "contact glow" behind/below it for grounding.
function ImageLayer({ texture, viewport, tiltRef, entering, isPlaying, skipTransition, turnDir = 1 }) {
  const fgRef = useRef()
  const bgRef = useRef()
  const glowRef = useRef()
  const startRef = useRef(performance.now())
  const [fgW, fgH] = useMemo(() => fitPlaneSize(texture, viewport), [texture, viewport])

  // A cache-hit texture is already fully loaded — pre-warm the clock so the eased
  // transition below evaluates as already-complete on the first frame instead of
  // replaying the full fly-in for an image that has nothing left to wait for.
  useEffect(() => {
    startRef.current = skipTransition ? performance.now() - TRANSITION_MS : performance.now()
  }, [texture, skipTransition])

  useFrame((state) => {
    if (!fgRef.current || !bgRef.current || !texture) return
    const elapsed = performance.now() - startRef.current
    const t = Math.min(1, elapsed / TRANSITION_MS)
    const eased = 1 - Math.pow(1 - t, 3)
    // Entering plane flies in from depth; the outgoing one (rendered by the
    // sibling ImageLayer for prevTexture) recedes using the same curve mirrored.
    const z = entering ? THREE.MathUtils.lerp(-8, 0, eased) : THREE.MathUtils.lerp(0, -8, eased)
    const opacity = entering ? eased : 1 - eased

    const tiltX = tiltRef.current.y * FG_TILT
    const tiltY = tiltRef.current.x * FG_TILT
    const breathe = 1 + Math.sin(state.clock.elapsedTime * 1.6) * (isPlaying ? 0.014 : 0.005)

    // swing: 1 at the far end of the transition, 0 at rest. The entering page
    // arrives from the leading edge, the outgoing one leaves past the trailing
    // one, so a back-navigation visibly turns the other way.
    const swing = entering ? 1 - eased : eased
    const side = entering ? 1 : -1
    const yaw = turnDir * side * swing * PAGE_TURN_YAW
    const shift = turnDir * side * swing * fgW * PAGE_TURN_SHIFT

    fgRef.current.position.z = z
    fgRef.current.position.x = shift
    fgRef.current.rotation.x = tiltX
    fgRef.current.rotation.y = tiltY + yaw
    fgRef.current.scale.set(fgW * breathe * TILT_OVERSCAN, fgH * breathe * TILT_OVERSCAN, 1)
    fgRef.current.material.opacity = opacity

    bgRef.current.position.z = z + BG_Z
    // The blurred backing layer swings less, so it reads as the page behind the
    // page rather than a second sheet glued to the first.
    bgRef.current.position.x = shift * 0.45
    bgRef.current.rotation.x = tiltX * (BG_TILT / FG_TILT)
    bgRef.current.rotation.y = tiltY * (BG_TILT / FG_TILT) + yaw * 0.45
    bgRef.current.material.opacity = opacity * BG_OPACITY

    if (glowRef.current) {
      glowRef.current.position.z = z - 0.6
      const pulse = 1 + Math.sin(state.clock.elapsedTime * 1.6) * 0.15
      glowRef.current.material.opacity = opacity * (isPlaying ? 0.26 : 0.16) * pulse
      const hue = 0.72 + Math.sin(state.clock.elapsedTime * 0.12) * 0.1
      glowRef.current.material.color.setHSL(hue, 0.85, 0.6)
    }
  })

  return (
    <group>
      <mesh ref={glowRef} scale={[fgW * 1.4, fgH * 0.55, 1]} position={[0, -fgH * 0.4, 0]}>
        <planeGeometry args={[1, 1]} />
        <meshBasicMaterial color="#a78bfa" transparent opacity={0} blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
      </mesh>
      <mesh ref={bgRef} scale={[fgW * BG_SCALE * TILT_OVERSCAN, fgH * BG_SCALE * TILT_OVERSCAN, 1]}>
        <planeGeometry args={[1, 1]} />
        <meshBasicMaterial map={texture} transparent opacity={0} color="#aab4ff" toneMapped={false} depthWrite={false} />
      </mesh>
      <mesh ref={fgRef}>
        <planeGeometry args={[1, 1]} />
        {/* depthWrite MUST stay false. This plane spends most of its life at
            opacity 0 (the outgoing page never unmounts - prevImageUrl keeps it
            alive), and a transparent plane that writes depth still occludes
            whatever is behind it. While the transition only moved planes along
            z the two stayed concentric so it never showed; the moment the page
            turn shifts one sideways, the invisible outgoing page punches a
            hard-edged hole through the incoming one. */}
        <meshBasicMaterial map={texture} transparent opacity={0} toneMapped={false} depthWrite={false} />
      </mesh>
    </group>
  )
}

// Ambient ownership of the shared tilt target: mouse-driven on desktop (via a ref
// mutated from a DOM pointermove handler up in StoryPlayer), a slow auto-drift on
// mobile where continuous drag would fight page scroll.
function useTiltRef(pointerTiltRef, isMobile) {
  const smoothed = useRef({ x: 0, y: 0 })
  useFrame((state) => {
    const target = isMobile
      ? { x: Math.sin(state.clock.elapsedTime * 0.35) * 0.6, y: Math.cos(state.clock.elapsedTime * 0.27) * 0.4 }
      : pointerTiltRef.current
    smoothed.current.x = THREE.MathUtils.lerp(smoothed.current.x, target.x, 0.04)
    smoothed.current.y = THREE.MathUtils.lerp(smoothed.current.y, target.y, 0.04)
  })
  return smoothed
}

export function StorySceneImagePlane({ imageUrl, prevImageUrl, isMobile, isPlaying, turnDir = 1, pointerTiltRef }) {
  const { viewport } = useThree()
  const [texture, wasCachedRef] = useSceneTexture(imageUrl)
  const [prevTexture] = useSceneTexture(prevImageUrl)
  const tiltRef = useTiltRef(pointerTiltRef, isMobile)

  // Texture lifecycle is now owned by the module-level LRU cache (see
  // useSceneTexture above), not per-mount — nothing to dispose here anymore.

  if (!texture) return null

  return (
    <group>
      {prevTexture && prevTexture !== texture && (
        <ImageLayer texture={prevTexture} viewport={viewport} tiltRef={tiltRef} entering={false} isPlaying={isPlaying} turnDir={turnDir} />
      )}
      <ImageLayer texture={texture} viewport={viewport} tiltRef={tiltRef} entering isPlaying={isPlaying} skipTransition={wasCachedRef.current} turnDir={turnDir} />
    </group>
  )
}

export default StorySceneImagePlane
