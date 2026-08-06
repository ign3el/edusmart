import { useRef, useState, useEffect, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'

const TRANSITION_MS = 550
// Every depth below is in world units and was tuned against a camera 30 units
// out. CameraRig now sits at 48 (it traded a 60-degree lens for a 38-degree one
// to kill the tilt keystoning), so each one is scaled by 48/30 to keep the same
// parallax separation. Left unscaled they would all collapse toward the picture
// and the layered-depth look would flatten out.
const BG_Z = -7.2
const FLY_IN_Z = -13
const GLOW_Z_OFFSET = -1.0
const BG_OPACITY = 0.22
// The backdrop is a magnified, dimmed copy of the same picture - it exists to
// fill the letterbox margin around a contained image with something that belongs
// to the image, instead of a hard edge against the panel colour.
const BG_TINT = '#7b84c4'
const FG_TILT = 0.16
const BG_TILT = 0.05
// How far the tilted near edge may push past the frame before the picture is
// scaled down to keep it in. Zero here is the trap the previous pass fell into:
// a tilting rectangle can never fit exactly inside a same-shaped window, so
// demanding "no pixel ever leaves the frame" means the picture is permanently
// smaller than its frame - and on a phone the mobile auto-drift below never
// returns to zero tilt, so the inset never went away. Measured, it sat at 0.92
// of the frame for its entire life, which is the visible margin in the report.
// Allowing 6% of overhang instead lets the fit stay at exactly 1.0 through the
// whole drift cycle: the picture fills its frame, and the most that is ever
// clipped is ~5% of ONE edge at the extreme of a 20-second sweep.
const TILT_BLEED = 0.06
const MAX_CACHED_TEXTURES = 16
// The backdrop must never undercover the frame, or its own edge becomes the
// visible artifact - it is cover-fit against the live frustum already, so 1.15
// would be enough slack for its (much smaller) tilt. It is pushed far past that
// for a second reason: at low magnification the margin just reads as a legible
// second copy of the picture (the dragon's tail appearing again beside the
// dragon), which is the "duplicated/distorted border" complaint in another form.
// Blowing the texture up this far is what turns it into an out-of-focus colour
// field instead - there is no blur pass to lean on here, magnification IS the
// blur.
const BACKDROP_OVERSCAN = 3.6
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

function textureAspect(texture) {
  const img = texture?.image
  return img?.width && img?.height ? img.width / img.height : 1
}

// Half-extents of the frame at the image plane's depth, measured from the LIVE
// camera every frame.
//
// This replaces useThree().viewport, which is a snapshot recomputed only on
// resize. CameraRig breathes camera distance (30 +/-1.5) and fov (60 +/-2.2 deg)
// continuously, so the real frame at z=0 swings ~9% while `viewport` reports a
// constant - and every size derived from it is wrong for most of the cycle. The
// previous code papered over that with a fixed 1.2x oversize on a cover-fit
// plane, which guaranteed coverage by throwing away 17% of the picture's width
// and 37% of its height. Measuring the camera instead removes the guess, which
// is what makes a contain-fit (nothing cropped) plane safe here.
//
// The camera lookAt()s the origin, so the frame stays centred on the plane and
// the half-extents are symmetric; distance is the full vector length because
// CameraRig also offsets x/y.
function frustumHalfExtents(camera) {
  const tanHalfFov = Math.tan((camera.fov * Math.PI) / 360)
  const halfH = camera.position.length() * tanHalfFov
  return [halfH * camera.aspect, halfH, tanHalfFov]
}

// Largest scale a contain-fit plane can take, at THIS instant, without the tilt
// pushing an edge outside the frame.
//
// Tilting rotates the plane's near edge toward the camera, where the frustum is
// narrower - so a plane that fills the frame exactly gets its near edge clipped
// the moment it moves. The obvious answer is a constant safety inset, and that
// is what a first pass here used (0.9). It is the wrong answer: the tilt is zero
// most of the time, so a constant inset means the picture sits permanently 10%
// too small in its own frame to pay for a worst case that is rarely happening.
// Solving for the actual current tilt instead gives a full-bleed picture at rest
// that gives back a few percent only while it is actually moving, which is
// indistinguishable from the breathing scale already applied below.
function tiltFitScale(halfExtentH, halfExtentW, halfH, halfW, tanHalfFov, tiltX, tiltY) {
  const cx = Math.cos(tiltX)
  const cy = Math.cos(tiltY)
  // How far the nearest corner travels toward the camera, per unit of scale.
  const nearPerScale = halfExtentH * Math.abs(Math.sin(tiltX)) + halfExtentW * Math.abs(Math.sin(tiltY))
  const vertical = halfH / (halfExtentH * cx + tanHalfFov * nearPerScale)
  const horizontal = halfW / (halfExtentW * cy + tanHalfFov * camAspectOf(halfW, halfH) * nearPerScale)
  // TILT_BLEED is what lets this return a true 1 instead of asymptotically
  // approaching it - see the constant for why exact containment is the wrong
  // target. The Math.min(1, ...) still caps it: bleed buys the picture the right
  // to fill its frame, never the right to grow past it.
  return Math.min(1, vertical * (1 + TILT_BLEED), horizontal * (1 + TILT_BLEED))
}

function camAspectOf(halfW, halfH) {
  return halfH > 0 ? halfW / halfH : 1
}

// A single image, rendered as background (soft, offset, low-parallax) + foreground
// (crisp, full-parallax) copies for a cheap layered-depth look from one texture,
// plus a hue-drifting additive "contact glow" behind/below it for grounding.
function ImageLayer({ texture, tiltRef, entering, isPlaying, skipTransition, turnDir = 1 }) {
  const fgRef = useRef()
  const bgRef = useRef()
  const glowRef = useRef()
  const startRef = useRef(performance.now())
  const aspect = useMemo(() => textureAspect(texture), [texture])

  // A cache-hit texture is already fully loaded — pre-warm the clock so the eased
  // transition below evaluates as already-complete on the first frame instead of
  // replaying the full fly-in for an image that has nothing left to wait for.
  useEffect(() => {
    startRef.current = skipTransition ? performance.now() - TRANSITION_MS : performance.now()
  }, [texture, skipTransition])

  useFrame((state) => {
    if (!fgRef.current || !bgRef.current || !texture) return

    const [halfW, halfH, tanHalfFov] = frustumHalfExtents(state.camera)
    const tiltX = tiltRef.current.y * FG_TILT
    const tiltY = tiltRef.current.x * FG_TILT

    // CONTAIN, against the live frame: the whole picture is on screen, always.
    // Cover-fit used to crop the top and bottom off every scene - subjects lost
    // their heads - and the container is square now (see StoryPlayer.css), so
    // containing a square image in it leaves no margin to give away either.
    let halfExtentH = halfH
    let halfExtentW = halfExtentH * aspect
    if (halfExtentW > halfW) {
      halfExtentW = halfW
      halfExtentH = halfExtentW / aspect
    }
    const fit = tiltFitScale(halfExtentH, halfExtentW, halfH, halfW, tanHalfFov, tiltX, tiltY)
    const fgH = halfExtentH * 2 * fit
    const fgW = halfExtentW * 2 * fit

    // COVER, against the same live frame, plus slack. Sized off the frame and
    // not off the foreground plane: with contain-fit the foreground is SMALLER
    // than the frame, so a backdrop scaled from it (the old BG_SCALE * fgW) would
    // stop short of the frame edge and put its own hard border on screen - which
    // is the "distortion at the image borders" this layer was accused of.
    let bgW = halfW * 2
    let bgH = bgW / aspect
    if (bgH < halfH * 2) {
      bgH = halfH * 2
      bgW = bgH * aspect
    }
    bgW *= BACKDROP_OVERSCAN
    bgH *= BACKDROP_OVERSCAN

    const elapsed = performance.now() - startRef.current
    const t = Math.min(1, elapsed / TRANSITION_MS)
    const eased = 1 - Math.pow(1 - t, 3)
    // Entering plane flies in from depth; the outgoing one (rendered by the
    // sibling ImageLayer for prevTexture) recedes using the same curve mirrored.
    const z = entering ? THREE.MathUtils.lerp(FLY_IN_Z, 0, eased) : THREE.MathUtils.lerp(0, FLY_IN_Z, eased)
    const opacity = entering ? eased : 1 - eased

    // Breathes DOWN from the fitted size, never up: the fit above is now exact
    // rather than inset, so anything above 1.0 here would push the picture's
    // edge back outside the frame and clip it on every pulse.
    const breathe = 1 - Math.abs(Math.sin(state.clock.elapsedTime * 1.6)) * (isPlaying ? 0.014 : 0.005)

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
    fgRef.current.scale.set(fgW * breathe, fgH * breathe, 1)
    fgRef.current.material.opacity = opacity

    bgRef.current.scale.set(bgW, bgH, 1)
    // The backdrop stays put: it is the room, not a second page. It used to
    // inherit the page-turn shift and yaw, which slid a plane that only just
    // covered the frame far enough to uncover a corner mid-turn. It keeps a
    // fraction of the tilt so it still parallaxes against the foreground.
    bgRef.current.position.z = BG_Z
    bgRef.current.rotation.x = tiltX * (BG_TILT / FG_TILT)
    bgRef.current.rotation.y = tiltY * (BG_TILT / FG_TILT)
    bgRef.current.material.opacity = opacity * BG_OPACITY

    if (glowRef.current) {
      // Never wider than the picture. This is an untextured additive plane, so
      // every edge of it is a hard seam; the only reason it looks like a glow is
      // that the picture itself covers three of them. Overhang it and the top
      // edge runs out into the side margin as a bright horizontal line across
      // the backdrop - which is precisely what it did on the first pass here.
      glowRef.current.scale.set(fgW, fgH * 0.55, 1)
      glowRef.current.position.y = -fgH * 0.4
      glowRef.current.position.z = z + GLOW_Z_OFFSET
      const pulse = 1 + Math.sin(state.clock.elapsedTime * 1.6) * 0.15
      glowRef.current.material.opacity = opacity * (isPlaying ? 0.26 : 0.16) * pulse
      const hue = 0.72 + Math.sin(state.clock.elapsedTime * 0.12) * 0.1
      glowRef.current.material.color.setHSL(hue, 0.85, 0.6)
    }
  })

  return (
    <group>
      {/* Scales and positions for all three meshes are set in the frame loop
          above, from the live camera - there is no correct static value for
          them while CameraRig is breathing the frustum. */}
      <mesh ref={glowRef}>
        <planeGeometry args={[1, 1]} />
        <meshBasicMaterial color="#a78bfa" transparent opacity={0} blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
      </mesh>
      <mesh ref={bgRef}>
        <planeGeometry args={[1, 1]} />
        <meshBasicMaterial map={texture} transparent opacity={0} color={BG_TINT} toneMapped={false} depthWrite={false} />
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
  const [texture, wasCachedRef] = useSceneTexture(imageUrl)
  const [prevTexture] = useSceneTexture(prevImageUrl)
  const tiltRef = useTiltRef(pointerTiltRef, isMobile)

  // Texture lifecycle is now owned by the module-level LRU cache (see
  // useSceneTexture above), not per-mount — nothing to dispose here anymore.

  if (!texture) return null

  return (
    <group>
      {prevTexture && prevTexture !== texture && (
        <ImageLayer texture={prevTexture} tiltRef={tiltRef} entering={false} isPlaying={isPlaying} turnDir={turnDir} />
      )}
      <ImageLayer texture={texture} tiltRef={tiltRef} entering isPlaying={isPlaying} skipTransition={wasCachedRef.current} turnDir={turnDir} />
    </group>
  )
}

export default StorySceneImagePlane
