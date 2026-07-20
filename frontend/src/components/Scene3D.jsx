import { StoryDepthScene } from './3d/StoryDepthScene'

export function Scene3D({ imageUrl, prevImageUrl, isMobile, isPlaying, pointerTiltRef }) {
  return (
    <StoryDepthScene
      imageUrl={imageUrl}
      prevImageUrl={prevImageUrl}
      isMobile={isMobile}
      isPlaying={isPlaying}
      pointerTiltRef={pointerTiltRef}
    />
  )
}

export default Scene3D
