// Story media requires auth + ownership on the backend. <img>/<audio> tags can't
// send an Authorization header, so the token is passed as a query param instead.
// /api/outputs/ (the live progressive-generation cache and legacy story media)
// belongs on this list too - it was missing, which is why a host-level nginx
// alias was serving that whole directory unauthenticated as a workaround.
const withAuthToken = (url) => {
  if (!/^\/api\/((saved|generated)-stories|outputs)\//.test(url)) {
    return url
  }
  const token = localStorage.getItem('auth_token')
  if (!token) {
    return url
  }
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}token=${encodeURIComponent(token)}`
}

// Helper function to build full URL, handling absolute URLs, data URLs, and API paths
export const buildFullUrl = (url) => {
  if (!url) {
    return ''
  }

  // Check if already absolute (http/https) or data URL
  if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('data:')) {
    return url
  }

  // Check if it's an API path (starts with /api/)
  if (url.startsWith('/api/')) {
    // Use the current window's origin to build the full URL
    return `${window.location.origin}${withAuthToken(url)}`
  }

  // For other relative paths, prepend the API domain from environment or current origin
  const apiDomain = import.meta.env.VITE_API_URL || window.location.origin
  return `${apiDomain}${withAuthToken(url)}`
}

export const debounce = (fn, delay) => {
  let timeoutId
  return (...args) => {
    clearTimeout(timeoutId)
    timeoutId = setTimeout(() => fn(...args), delay)
  }
}

export const throttle = (fn, limit) => {
  let inThrottle
  return (...args) => {
    if (!inThrottle) {
      fn(...args)
      inThrottle = true
      setTimeout(() => (inThrottle = false), limit)
    }
  }
}

export const formatTime = (time) => {
  if (isNaN(time)) return '0:00'
  const minutes = Math.floor(time / 60)
  const seconds = Math.floor(time % 60)
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}