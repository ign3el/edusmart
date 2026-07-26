import { StoryDepthScene } from './3d/StoryDepthScene'

export function Scene3D({ imageUrl, prevImageUrl, isMobile, isPlaying, turnDir = 1, pointerTiltRef }) {
  return (
    <StoryDepthScene
      imageUrl={imageUrl}
      prevImageUrl={prevImageUrl}
      isMobile={isMobile}
      isPlaying={isPlaying}
      turnDir={turnDir}
      pointerTiltRef={pointerTiltRef}
    />
  )
}

export default Scene3D
