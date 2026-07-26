import Scene3D from './Scene3D'
import StorySceneCanvas from './3d/StorySceneCanvas'

// Lazy-loaded boundary: keeps three.js/@react-three/fiber out of the main bundle
// until a story is actually being played.
function StoryScene3DLayer({ imageUrl, prevImageUrl, isMobile, isPlaying, sceneIndex, turnDir = 1, pointerTiltRef }) {
  return (
    <StorySceneCanvas sceneIndex={sceneIndex}>
      <Scene3D
        imageUrl={imageUrl}
        prevImageUrl={prevImageUrl}
        isMobile={isMobile}
        isPlaying={isPlaying}
        turnDir={turnDir}
        pointerTiltRef={pointerTiltRef}
      />
    </StorySceneCanvas>
  )
}

export default StoryScene3DLayer
