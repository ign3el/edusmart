import { StoryDepthScene } from './3d/StoryDepthScene'

export function Scene3D({ scene, imageUrl, imageLoaded, imageError, isMobile, sceneIndex }) {
  return (
    <StoryDepthScene
      scene={scene}
      imageUrl={imageUrl}
      imageLoaded={imageLoaded}
      imageError={imageError}
      isMobile={isMobile}
      sceneIndex={sceneIndex}
    />
  )
}

export default Scene3D
