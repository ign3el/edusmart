import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import './SocialAuthButtons.css'

// Public identifiers, inlined by Vite at build time. Blank means "not
// configured", and the corresponding button is never rendered - better than
// showing a button that can only fail.
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || ''
const FACEBOOK_APP_ID = import.meta.env.VITE_FACEBOOK_APP_ID || ''

const GOOGLE_SRC = 'https://accounts.google.com/gsi/client'
const FB_SRC = 'https://connect.facebook.net/en_US/sdk.js'
const FB_VERSION = 'v19.0'

// One promise per URL, shared across mounts. Login and Signup both render this
// component, and switching between them must not inject the SDK twice.
const scriptPromises = new Map()

function loadScript(src) {
  if (scriptPromises.has(src)) return scriptPromises.get(src)
  const promise = new Promise((resolve, reject) => {
    const el = document.createElement('script')
    el.src = src
    el.async = true
    el.defer = true
    el.onload = () => resolve()
    el.onerror = () => {
      // Let a later mount retry rather than caching the failure forever.
      scriptPromises.delete(src)
      reject(new Error(`Could not load ${src}`))
    }
    document.head.appendChild(el)
  })
  scriptPromises.set(src, promise)
  return promise
}

export default function SocialAuthButtons({ mode = 'login' }) {
  const { socialLogin } = useAuth()
  const googleSlot = useRef(null)
  const alive = useRef(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  const anyConfigured = Boolean(GOOGLE_CLIENT_ID || FACEBOOK_APP_ID)

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  const finish = useCallback(async (provider, token) => {
    setError('')
    setBusy(provider)
    try {
      await socialLogin(provider, token)
      // On success the auth context flips isAuthenticated and the app moves on;
      // this component unmounts, so there is nothing to reset here.
    } catch (err) {
      if (!alive.current) return
      const detail = err?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'Sign-in failed. Please try again.')
      setBusy('')
    }
  }, [socialLogin])

  // --- Google -------------------------------------------------------------
  useEffect(() => {
    if (!GOOGLE_CLIENT_ID || !googleSlot.current) return

    let cancelled = false
    loadScript(GOOGLE_SRC)
      .then(() => {
        if (cancelled || !googleSlot.current || !window.google?.accounts?.id) return
        window.google.accounts.id.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: (response) => {
            if (response?.credential) finish('google', response.credential)
          },
        })
        // GIS needs a pixel width and caps at 400. Track the real container so
        // the button matches the form on a phone instead of overflowing it.
        const width = Math.min(400, Math.round(googleSlot.current.offsetWidth) || 320)
        googleSlot.current.innerHTML = ''
        window.google.accounts.id.renderButton(googleSlot.current, {
          theme: 'filled_black',
          size: 'large',
          shape: 'rectangular',
          text: mode === 'signup' ? 'signup_with' : 'continue_with',
          logo_alignment: 'center',
          width,
        })
      })
      .catch(() => {
        if (!cancelled) setError('Could not reach Google. Check your connection.')
      })

    return () => { cancelled = true }
  }, [finish, mode])

  // --- Facebook -----------------------------------------------------------
  const handleFacebook = useCallback(async () => {
    setError('')
    try {
      await loadScript(FB_SRC)
    } catch {
      setError('Could not reach Facebook. Check your connection.')
      return
    }
    if (!window.FB) {
      setError('Could not reach Facebook. Check your connection.')
      return
    }
    window.FB.init({ appId: FACEBOOK_APP_ID, cookie: false, xfbml: false, version: FB_VERSION })
    window.FB.login((response) => {
      const token = response?.authResponse?.accessToken
      if (!token) {
        // Closing the popup is a normal thing to do, not an error worth shouting about.
        if (response?.status !== 'unknown') setError('Facebook sign-in was cancelled.')
        return
      }
      finish('facebook', token)
    }, { scope: 'email' })
  }, [finish])

  if (!anyConfigured) return null

  return (
    <div className="social-auth">
      <div className="social-divider"><span>or continue with</span></div>

      {GOOGLE_CLIENT_ID && (
        <div className="social-google-slot" ref={googleSlot} aria-busy={busy === 'google'} />
      )}

      {FACEBOOK_APP_ID && (
        <button
          type="button"
          className="social-btn social-btn--facebook"
          onClick={handleFacebook}
          disabled={Boolean(busy)}
        >
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
            <path
              fill="currentColor"
              d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"
            />
          </svg>
          <span>{busy === 'facebook' ? 'Signing in…' : 'Continue with Facebook'}</span>
        </button>
      )}

      {error && <p className="social-error" role="alert">{error}</p>}
    </div>
  )
}
