import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { ChevronLeft, Check, Sparkles, Tag, Loader2, Star } from 'lucide-react'
import { getPlans, getBillingBalance, redeemPromoCode, createCheckoutSession } from '../services/api'
import './PricingPage.css'

const cardVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: (i) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.08, duration: 0.35, ease: 'easeOut' }
  })
}

function PricingPage({ onBack }) {
  const [plans, setPlans] = useState([])
  const [balance, setBalance] = useState(null)
  const [loading, setLoading] = useState(true)
  const [checkoutTier, setCheckoutTier] = useState(null)
  const [promoCode, setPromoCode] = useState('')
  const [appliedPromo, setAppliedPromo] = useState(null) // { type, discount_value, message } once validated
  const [promoStatus, setPromoStatus] = useState({ text: '', type: '' })
  const [redeeming, setRedeeming] = useState(false)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setLoading(true)
    try {
      const [plansData, balanceData] = await Promise.all([getPlans(), getBillingBalance()])
      setPlans(plansData)
      setBalance(balanceData)
    } catch (err) {
      console.error('Failed to load pricing data:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleRedeemPromo = async () => {
    if (!promoCode.trim()) return
    setRedeeming(true)
    setPromoStatus({ text: '', type: '' })
    try {
      const result = await redeemPromoCode(promoCode.trim())
      if (result.type === 'free_credits') {
        setPromoStatus({ text: result.message, type: 'success' })
        setPromoCode('')
        setAppliedPromo(null)
        await loadData() // refresh balance to show the granted credits immediately
      } else {
        // percent_off - held for checkout, not redeemed yet
        setAppliedPromo({ code: promoCode.trim().toUpperCase(), ...result })
        setPromoStatus({ text: result.message, type: 'success' })
      }
    } catch (err) {
      setPromoStatus({ text: err.response?.data?.detail || 'Invalid promo code', type: 'error' })
    } finally {
      setRedeeming(false)
    }
  }

  const handleSubscribe = async (tierKey) => {
    setCheckoutTier(tierKey)
    try {
      const { checkout_url } = await createCheckoutSession(tierKey, appliedPromo?.code)
      window.location.href = checkout_url
    } catch (err) {
      setPromoStatus({
        text: err.response?.data?.detail || 'Could not start checkout. Please try again.',
        type: 'error'
      })
      setCheckoutTier(null)
    }
  }

  return (
    <div className="pricing-page">
      <div className="pricing-header">
        <button onClick={onBack} className="back-button">
          <ChevronLeft size={16} /> Back
        </button>
        <h1>Plans &amp; Credits</h1>
      </div>

      {balance && (
        <motion.div
          className="pricing-balance-card"
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          {/* Admins bypass the credit gate server-side, so a number here would
              be meaningless - the backend sends `unlimited` to say so. */}
          <span className="balance-count">{balance.unlimited ? '∞' : balance.credits_balance}</span>
          <span className="balance-label">
            {balance.unlimited
              ? 'unlimited story credits'
              : balance.credits_balance === 1 ? 'story credit remaining' : 'story credits remaining'}
            <span className="balance-tier"> · {balance.unlimited ? 'admin' : balance.subscription_tier} plan</span>
          </span>
        </motion.div>
      )}

      <div className="pricing-promo-box">
        <Tag size={16} aria-hidden="true" />
        <input
          type="text"
          placeholder="Have a promo code?"
          value={promoCode}
          onChange={(e) => setPromoCode(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleRedeemPromo()}
        />
        <button onClick={handleRedeemPromo} disabled={redeeming || !promoCode.trim()} aria-label="Apply promo code">
          {redeeming ? <Loader2 size={14} className="spin-icon" aria-hidden="true" /> : 'Apply'}
        </button>
      </div>
      {promoStatus.text && (
        <p className={`pricing-promo-status ${promoStatus.type}`}>{promoStatus.text}</p>
      )}

      {loading ? (
        <div className="pricing-loading">Loading plans...</div>
      ) : (
        <div className="pricing-grid">
          {plans.map((plan, i) => {
            const isCurrent = balance?.subscription_tier === plan.tier_key
            const isFree = plan.tier_key === 'free'
            return (
              <motion.div
                key={plan.tier_key}
                className={`pricing-card ${isCurrent ? 'current' : ''} ${plan.is_recommended ? 'recommended' : ''}`}
                custom={i}
                variants={cardVariants}
                initial="hidden"
                animate="visible"
              >
                {Boolean(plan.is_recommended) && !isCurrent && (
                  <span className="recommended-badge"><Star size={12} aria-hidden="true" /> Recommended</span>
                )}
                {isCurrent && <span className="current-badge">Current Plan</span>}
                <h3>{plan.display_name}</h3>
                {plan.description && <p className="pricing-description">{plan.description}</p>}
                <p className="pricing-price">{plan.price_display}</p>
                <p className="pricing-credits">
                  <Check size={14} aria-hidden="true" /> {plan.credits_included} stories
                  {plan.billing_mode === 'subscription' ? ' / month' : ''}
                </p>
                {plan.features && (
                  <ul className="pricing-features">
                    {plan.features.split('\n').map((line) => line.trim()).filter(Boolean).map((feature) => (
                      <li key={feature}><Check size={13} aria-hidden="true" /> {feature}</li>
                    ))}
                  </ul>
                )}
                {appliedPromo?.type === 'percent_off' && !isFree && (
                  <p className="pricing-discount-note">
                    <Sparkles size={12} aria-hidden="true" /> {appliedPromo.discount_value}% off applied at checkout
                  </p>
                )}
                {!isFree && (
                  <button
                    className="pricing-subscribe-btn"
                    onClick={() => handleSubscribe(plan.tier_key)}
                    disabled={checkoutTier === plan.tier_key || isCurrent}
                  >
                    {checkoutTier === plan.tier_key
                      ? 'Redirecting...'
                      : isCurrent
                        ? 'Active'
                        : plan.billing_mode === 'subscription' ? 'Subscribe' : 'Buy Top-Up'}
                  </button>
                )}
              </motion.div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default PricingPage
