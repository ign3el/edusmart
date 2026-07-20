// Service to check for app updates
const UPDATE_CHECK_INTERVAL = 60 * 60 * 1000; // Check every 1 hour
const CURRENT_VERSION = '1.0.0';

class UpdateService {
  constructor() {
    this.checkInterval = null;
    this.isUpdateAvailable = false;
  }

  async checkForUpdates() {
    try {
      const response = await fetch('/version.json?' + Date.now(), {
        cache: 'no-store',
        headers: {
          'Cache-Control': 'no-cache'
        }
      });
      
      if (!response.ok) return false;
      
      const serverVersion = await response.json();

      // Check if version or build time has changed
      const localVersion = localStorage.getItem('appVersion') || CURRENT_VERSION;
      const localBuildTime = localStorage.getItem('appBuildTime');

      // First time this browser has ever checked - adopt the current server
      // build as the baseline instead of flagging it as an "update". There's
      // nothing to update from on a first visit; leaving appBuildTime unset
      // made every fresh session nag "update available" permanently (it
      // could never match a real buildTime), which is exactly the kind of
      // false alarm that trains someone to dismiss real ones too.
      if (!localBuildTime) {
        localStorage.setItem('appVersion', serverVersion.version);
        localStorage.setItem('appBuildTime', serverVersion.buildTime);
        return false;
      }

      if (serverVersion.version !== localVersion ||
          serverVersion.buildTime !== localBuildTime) {
        this.isUpdateAvailable = true;
        return true;
      }

      return false;
    } catch (error) {
      console.error('Update check failed:', error);
      return false;
    }
  }

  async applyUpdate() {
    try {
      // Fetch the new version info
      const response = await fetch('/version.json?' + Date.now(), {
        cache: 'no-store'
      });
      const serverVersion = await response.json();
      
      // Update local storage
      localStorage.setItem('appVersion', serverVersion.version);
      localStorage.setItem('appBuildTime', serverVersion.buildTime);
      
      // Clear all caches
      if ('caches' in window) {
        const cacheNames = await caches.keys();
        await Promise.all(cacheNames.map(name => caches.delete(name)));
      }
      
      // Reload the page to get new version
      window.location.reload(true);
    } catch (error) {
      console.error('Update application failed:', error);
    }
  }

  startPeriodicCheck(onUpdateAvailable) {
    // Initial check
    this.checkForUpdates().then(hasUpdate => {
      if (hasUpdate && onUpdateAvailable) {
        onUpdateAvailable();
      }
    });

    // Set up periodic checks
    this.checkInterval = setInterval(async () => {
      const hasUpdate = await this.checkForUpdates();
      if (hasUpdate && onUpdateAvailable) {
        onUpdateAvailable();
      }
    }, UPDATE_CHECK_INTERVAL);
  }

  stopPeriodicCheck() {
    if (this.checkInterval) {
      clearInterval(this.checkInterval);
      this.checkInterval = null;
    }
  }

  // Initialize version on first load
  initializeVersion() {
    if (!localStorage.getItem('appVersion')) {
      localStorage.setItem('appVersion', CURRENT_VERSION);
    }
  }
}

export default new UpdateService();
