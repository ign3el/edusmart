// Aurora-like gradient planes
export function AuroraLayer() {
  const colors = ['#6366f1', '#06b6d4', '#10b981', '#818cf8', '#22d3ee']

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
