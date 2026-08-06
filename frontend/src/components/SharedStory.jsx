import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Loader2, Link2Off, ArrowRight } from 'lucide-react'
import { getSharedStory } from '../services/api'
import StoryPlayer from './StoryPlayer'
import BrandMark from './BrandMark'
import './SharedStory.css'

/**
 * Public read-only story page (/s/:token). No authentication anywhere in here -
 * the token in the URL is the whole credential, which is the point: this is the
 * link you send to someone who has never heard of LearnTale.
 *
 * Layout follows the app's own shell rather than inventing a second one: a slim
 * sticky bar carrying the wordmark and ONE call to action, with the existing
 * StoryPlayer filling the rest. The bar's height is published as
 * --app-header-h, which is the variable StoryPlayer already subtracts from
 * 100dvh - so the player fits the viewport exactly with no magic numbers and no
 * scrolling on a phone.
 */
function SharedStory() {
  const { token } = useParams()
  const [story, setStory] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      setLoading(true)
      setError('')
      try {
        const data = await getSharedStory(token)
        if (!cancelled) setStory(data)
      } catch (err) {
        if (cancelled) return
        // 404 is the expected, non-alarming case: the owner revoked the link or
        // deleted the story. Anything else is a real fault worth wording
        // differently, so the visitor knows whether retrying is pointless.
        setError(
          err?.response?.status === 404
            ? 'This link is no longer available. The person who shared it may have turned sharing off.'
            : 'We could not load this story right now. Please try again in a moment.'
        )
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [token])

  return (
    <div className="shared-story">
      <header className="shared-bar">
        <Link to="/" className="shared-brand" aria-label="LearnTale home">
          <BrandMark className="shared-brand-mark" />
        </Link>
        <Link to="/" className="shared-cta">
          Try free <ArrowRight size={16} aria-hidden="true" />
        </Link>
      </header>

      {loading && (
        <div className="shared-state" role="status" aria-live="polite">
          <Loader2 size={28} className="shared-spinner" aria-hidden="true" />
          <p>Loading story…</p>
        </div>
      )}

      {!loading && error && (
        <motion.div
          className="shared-state"
          role="alert"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25, ease: 'easeOut' }}
        >
          <Link2Off size={30} aria-hidden="true" />
          <p className="shared-state-title">Story unavailable</p>
          <p className="shared-state-body">{error}</p>
          <Link to="/" className="shared-state-action">
            Make your own story <ArrowRight size={16} aria-hidden="true" />
          </Link>
        </motion.div>
      )}

      {!loading && !error && story && (
        <StoryPlayer
          storyData={story.story_data}
          shareMode
          totalScenes={story.story_data?.scenes?.length || 0}
        />
      )}
    </div>
  )
}

export default SharedStory
