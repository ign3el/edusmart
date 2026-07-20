// Service Worker registration utility
// Handles installation, updates, and offline detection

// Update UX is owned entirely by updateService.js's version.json polling and
// App.jsx's non-native modal (see the comment on handleCheckForUpdate there).
// This function used to also run its own competing native confirm()-dialog
// update flow, plus an unconditional controllerchange listener that force-
// reloaded the page the instant any new service worker activated - with no
// warning, that could yank a story or quiz out from under someone mid-
// session. Registration here now just keeps the worker itself current;
// sw.js's own install/activate handlers (skipWaiting + clients.claim)
// already swap it in cleanly in the background.
export function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker
        .register('/sw.js')
        .then((registration) => {
          console.log('✅ Service Worker registered:', registration.scope);

          // Check for updates periodically
          setInterval(() => {
            registration.update();
          }, 60000); // Check every minute
        })
        .catch((error) => {
          console.error('❌ Service Worker registration failed:', error);
        });
    });
  } else {
    console.warn('⚠️ Service Workers not supported in this browser');
  }
}

export function unregisterServiceWorker() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.ready
      .then((registration) => {
        registration.unregister();
        console.log('Service Worker unregistered');
      })
      .catch((error) => {
        console.error('Error unregistering Service Worker:', error);
      });
  }
}

// Check if app is running in standalone mode (installed as PWA)
export function isStandalone() {
  return (
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true
  );
}

// Check online status
export function isOnline() {
  return navigator.onLine;
}

// Listen for online/offline events
export function onConnectionChange(callback) {
  window.addEventListener('online', () => callback(true));
  window.addEventListener('offline', () => callback(false));
  
  // Return cleanup function
  return () => {
    window.removeEventListener('online', callback);
    window.removeEventListener('offline', callback);
  };
}
// PWA install prompt is now handled in NavigationMenu.jsx
// Removed duplicate handler to prevent conflicts
export function promptInstall() {
  console.log('💾 PWA install prompt setup delegated to NavigationMenu');
  
  // Track successful installation
  window.addEventListener('appinstalled', () => {
    console.log('✅ PWA installed successfully');
  });
}
