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
    return `${window.location.origin}${url}`
  }

  // For other relative paths, prepend the API domain from environment or current origin
  const apiDomain = import.meta.env.VITE_API_URL || window.location.origin
  return `${apiDomain}${url}`
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