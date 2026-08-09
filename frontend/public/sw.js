// Service Worker for LearnTale Stories
// Implements App Shell architecture with offline-first strategy

const CACHE_VERSION = 'build-20260809200033';
const APP_SHELL_CACHE = `edusmart-shell-${CACHE_VERSION}`;
const RUNTIME_CACHE = `edusmart-runtime-${CACHE_VERSION}`;

// App Shell: Core files needed to render the UI
const APP_SHELL_FILES = [
  '/',
  '/index.html',
  '/manifest.json',
  '/browserconfig.xml',
  '/icon-72.png',
  '/icon-96.png',
  '/icon-128.png',
  '/icon-144.png',
  '/icon-152.png',
  '/icon-192.png',
  '/icon-384.png',
  '/icon-512.png',
  // Self-hosted fonts. Only the latin subsets: latin-ext is a progressive
  // enhancement and is not worth blocking the install event on.
  '/fonts/fonts.css',
  '/fonts/baloo2-latin.woff2',
  '/fonts/nunito-latin.woff2',
];

// Install event: Pre-cache the app shell
self.addEventListener('install', (event) => {
  console.log('[Service Worker] Installing...');
  event.waitUntil(
    caches.open(APP_SHELL_CACHE).then((cache) => {
      console.log('[Service Worker] Caching app shell');
      return cache.addAll(APP_SHELL_FILES);
    }).then(() => {
      // Force the waiting service worker to become the active service worker
      return self.skipWaiting();
    })
  );
});

// Activate event: Clean up old caches
self.addEventListener('activate', (event) => {
  console.log('[Service Worker] Activating...');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== APP_SHELL_CACHE && cacheName !== RUNTIME_CACHE) {
            console.log('[Service Worker] Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => {
      // Take control of all pages immediately
      return self.clients.claim();
    })
  );
});

// Fetch event: Implement caching strategies
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Skip Chrome extensions and other non-http(s) requests
  if (!url.protocol.startsWith('http')) {
    return;
  }

  // Strategy 1a: Network-First for the HTML shell. The shell references
  // hashed JS/CSS filenames that change every build, so it must never be
  // served stale - cache-first here was the bug that kept users frozen on
  // old bundles forever (nginx already sends no-cache for these, but that's
  // irrelevant to the Cache Storage API, which this fetch handler consults
  // instead of the network entirely under cache-first).
  if (
    request.destination === 'document' ||
    url.pathname === '/' ||
    url.pathname.endsWith('.html')
  ) {
    event.respondWith(
      fetch(request).then((response) => {
        if (response.status === 200) {
          const responseToCache = response.clone();
          caches.open(APP_SHELL_CACHE).then((cache) => {
            cache.put(request, responseToCache);
          });
        }
        return response;
      }).catch(() => {
        // Offline - fall back to whatever shell we last had cached.
        return caches.match(request).then((cached) => cached || caches.match('/index.html'));
      })
    );
    return;
  }

  // Strategy 1b: Cache-First for hashed JS/CSS chunks. Safe because Vite
  // gives every build's assets a new content-hashed filename - an old
  // cached chunk simply becomes unreferenced (and gets swept on the next
  // CACHE_VERSION bump) rather than ever being served in place of a new one.
  if (
    request.destination === 'script' ||
    request.destination === 'style' ||
    url.pathname.endsWith('.css') ||
    url.pathname.endsWith('.js')
  ) {
    event.respondWith(
      caches.match(request).then((cachedResponse) => {
        if (cachedResponse) {
          console.log('[Service Worker] Serving from cache:', request.url);
          return cachedResponse;
        }
        return fetch(request).then((response) => {
          if (response.status === 200) {
            const responseToCache = response.clone();
            caches.open(APP_SHELL_CACHE).then((cache) => {
              cache.put(request, responseToCache);
            });
          }
          return response;
        });
      })
    );
    return;
  }

  // Strategy 2: Network-First for API calls (with fallback to cache)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      // Add timeout to fetch to prevent hanging
      Promise.race([
        fetch(request).then((response) => {
          // Cache successful API responses for offline fallback
          if (response.status === 200) {
            const responseToCache = response.clone();
            caches.open(RUNTIME_CACHE).then((cache) => {
              cache.put(request, responseToCache).catch(err => {
                console.warn('[Service Worker] Cache put failed:', err);
              });
            });
          }
          return response;
        }).catch((error) => {
          console.warn('[Service Worker] API fetch failed:', error);
          throw error;
        }),
        // 10-second timeout
        new Promise((_, reject) => 
          setTimeout(() => reject(new Error('Service Worker: API timeout')), 10000)
        )
      ]).catch(() => {
        // Network failed or timed out, try cache
        return caches.match(request).then((cachedResponse) => {
          if (cachedResponse) {
            console.log('[Service Worker] API offline, serving from cache:', request.url);
            return cachedResponse;
          }
          // Return graceful error response
          return new Response(
            JSON.stringify({ 
              error: 'Offline', 
              message: 'No cached data available',
              timestamp: Date.now()
            }),
            { 
              status: 503, 
              headers: { 
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache'
              } 
            }
          );
        }).catch((cacheError) => {
          console.warn('[Service Worker] Cache lookup failed:', cacheError);
          // Final fallback
          return new Response(
            JSON.stringify({ 
              error: 'Service Unavailable', 
              message: 'Please check your connection and try again'
            }),
            { 
              status: 503, 
              headers: { 'Content-Type': 'application/json' } 
            }
          );
        });
      })
    );
    return;
  }

  // Strategy 3: Cache-First for images and media (with CORS handling)
  if (
    request.destination === 'image' ||
    request.destination === 'audio' ||
    request.destination === 'video' ||
    url.pathname.match(/\.(png|jpg|jpeg|gif|webp|svg|mp3|wav|ogg|mp4|webm)$/i)
  ) {
    // For API media files, strip cache-busting params for better caching
    const cacheKey = url.hostname === 'edusmart.ign3el.com' ? 
      new URL(url.origin + url.pathname) : request;
    
    event.respondWith(
      caches.match(cacheKey).then((cachedResponse) => {
        if (cachedResponse) {
          console.log('[Service Worker] Serving media from cache:', url.pathname);
          return cachedResponse;
        }
        
        // Add timeout for media fetches
        return Promise.race([
          fetch(request, { 
            mode: 'cors',
            credentials: 'omit'
          }).then((response) => {
            if (response.status === 200) {
              const responseToCache = response.clone();
              caches.open(RUNTIME_CACHE).then((cache) => {
                cache.put(cacheKey, responseToCache).catch(err => {
                  console.warn('[Service Worker] Media cache put failed:', err);
                });
              });
            }
            return response;
          }).catch((error) => {
            console.warn('[Service Worker] Media fetch failed:', error);
            throw error;
          }),
          // 30-second timeout for media
          new Promise((_, reject) => 
            setTimeout(() => reject(new Error('Service Worker: Media timeout')), 30000)
          )
        ]).catch(() => {
          // Return a placeholder or transparent pixel for images
          if (request.destination === 'image') {
            return new Response(
              new Blob([new Uint8Array([
                0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00, 
                0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0x21, 
                0xf9, 0x04, 0x01, 0x00, 0x00, 0x00, 0x00, 0x2c, 0x00, 0x00, 
                0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x02, 0x02, 0x44, 
                0x01, 0x00, 0x3b
              ])], { type: 'image/gif' }),
              { headers: { 'Content-Type': 'image/gif' } }
            );
          }
          // For audio/video, return empty response
          return new Response(null, { status: 404 });
        });
      })
    );
    return;
  }

  // Default: Network-First for everything else
  event.respondWith(
    fetch(request).catch(() => {
      return caches.match(request);
    })
  );
});

// Message handler for cache management from the app
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  
  if (event.data && event.data.type === 'CLEAR_CACHE') {
    event.waitUntil(
      caches.keys().then((cacheNames) => {
        return Promise.all(
          cacheNames.map((cacheName) => caches.delete(cacheName))
        );
      })
    );
  }
});
