import { motion } from 'framer-motion'

export function HomeCard({ emoji, title, description, onClick, index = 0 }) {
  return (
    <motion.button className="home-btn" onClick={onClick}
      initial={{ opacity: 0, y: 40, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.6, delay: 0.2 + index * 0.15, ease: [0.22, 1, 0.36, 1] }}
      whileHover={{ scale: 1.02, y: -4 }}
      whileTap={{ scale: 0.98 }}
    >
      <div className="emoji">{emoji}</div>
      <strong>{title}</strong>
      <span>{description}</span>
    </motion.button>
  )
}
