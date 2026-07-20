import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Home, BookOpen, FolderOpen, Settings, Save, Download, FileText,
  Sparkles, RefreshCw, User, LogOut, Menu, X, Smartphone, Check
} from 'lucide-react'
import './NavigationMenu.css'

function NavigationMenu({ user, isAdmin, onHome, onNewStory, onLoadStories, onOfflineManager, onAdminClick, onProfile, onLogout, onSaveStory, onDownloadStory, isPlayingStory, currentStory, onShowFileViewer, onCheckUpdate }) {
  const [isMobileOpen, setIsMobileOpen] = useState(false)
  const [isCheckingUpdate, setIsCheckingUpdate] = useState(false)
  const [showInstallPrompt, setShowInstallPrompt] = useState(false)
  const [isPWA, setIsPWA] = useState(false)
  const deferredPromptRef = useRef(null)

  useEffect(() => {
    const handleBeforeInstallPrompt = (e) => {
      e.preventDefault()
      deferredPromptRef.current = e
      setShowInstallPrompt(true)
    }
    const handleAppInstalled = () => {
      setShowInstallPrompt(false)
      setIsPWA(true)
    }
    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt)
    window.addEventListener('appinstalled', handleAppInstalled)
    if (window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true) {
      setIsPWA(true)
    }
    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt)
      window.removeEventListener('appinstalled', handleAppInstalled)
    }
  }, [])

  const handleInstallPWA = async () => {
    if (deferredPromptRef.current) {
      deferredPromptRef.current.prompt()
      const { outcome } = await deferredPromptRef.current.userChoice
      if (outcome === 'accepted') { setIsPWA(true); setShowInstallPrompt(false) }
      deferredPromptRef.current = null
    } else {
      alert('Install prompt is not available yet. Please use the browser menu to install or revisit after a bit of usage.')
    }
  }

  const handleCheckUpdate = async () => {
    setIsCheckingUpdate(true)
    try {
      await onCheckUpdate?.()
    } finally {
      setIsCheckingUpdate(false)
    }
  }

  const handleAction = (action) => {
    if (action) action()
    setIsMobileOpen(false)
  }

  return (
    <>
      {/* Mobile Hamburger Button */}
      <button
        className="mobile-menu-btn"
        onClick={() => setIsMobileOpen(!isMobileOpen)}
        aria-label="Menu"
      >
        <motion.div
          animate={{ rotate: isMobileOpen ? 90 : 0 }}
          transition={{ duration: 0.3 }}
          style={{ display: 'flex' }}
        >
          {isMobileOpen ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
        </motion.div>
      </button>

      {/* Desktop Menu (Always visible - RELIABLE text buttons) */}
      <nav className="desktop-menu">
        {onHome && (
          <button onClick={() => handleAction(onHome)} className="menu-btn">
            <Home size={16} aria-hidden="true" /> Home
          </button>
        )}
        {onLoadStories && (
          <button onClick={() => handleAction(onLoadStories)} className="menu-btn">
            <BookOpen size={16} aria-hidden="true" /> Load Story
          </button>
        )}
        {onOfflineManager && (
          <button onClick={() => handleAction(onOfflineManager)} className="menu-btn">
            <FolderOpen size={16} aria-hidden="true" /> Offline
          </button>
        )}
        {isAdmin && onAdminClick && (
          <button onClick={() => handleAction(onAdminClick)} className="menu-btn admin">
            <Settings size={16} aria-hidden="true" /> Admin
          </button>
        )}
        {isPlayingStory && onSaveStory && (
          <button onClick={() => handleAction(onSaveStory)} className="menu-btn primary">
            <Save size={16} aria-hidden="true" /> Save
          </button>
        )}
        {isPlayingStory && onDownloadStory && (
          <button onClick={() => handleAction(onDownloadStory)} className="menu-btn primary">
            <Download size={16} aria-hidden="true" /> Download
          </button>
        )}
        {currentStory?.persistent_path && (
          <button onClick={() => handleAction(onShowFileViewer)} className="menu-btn">
            <FileText size={16} aria-hidden="true" /> View File
          </button>
        )}
        {onNewStory && (
          <button onClick={() => handleAction(onNewStory)} className="menu-btn primary">
            <Sparkles size={16} aria-hidden="true" /> New Story
          </button>
        )}
        <button
          onClick={handleCheckUpdate}
          className="menu-btn update"
          disabled={isCheckingUpdate}
          title="Check for updates"
        >
          <RefreshCw size={16} aria-hidden="true" className={isCheckingUpdate ? 'spin-icon' : ''} /> Update
        </button>
        {onProfile && (
          <button onClick={() => handleAction(onProfile)} className="menu-btn profile" title="Account">
            <User size={16} aria-hidden="true" /> {user?.email?.split('@')[0] || 'Account'}
          </button>
        )}
        {onLogout && (
          <button onClick={() => handleAction(onLogout)} className="menu-btn logout">
            <LogOut size={16} aria-hidden="true" /> Logout
          </button>
        )}
      </nav>

      {/* Mobile Drawer - via Portal */}
      {createPortal(
        <AnimatePresence>
          {isMobileOpen && (
            <>
              <motion.div
                className="mobile-overlay"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setIsMobileOpen(false)}
              />
              <motion.div
                className="mobile-drawer"
                initial={{ x: '100%' }}
                animate={{ x: 0 }}
                exit={{ x: '100%' }}
                transition={{ type: 'spring', damping: 25, stiffness: 200 }}
              >
                <div className="drawer-header">
                  <h3>Menu</h3>
                  <button onClick={() => setIsMobileOpen(false)} className="drawer-close-btn" aria-label="Close menu">
                    <X size={22} aria-hidden="true" />
                  </button>
                </div>
                <div className="drawer-content">
                  {user && (
                    <div className="user-profile-card" onClick={() => handleAction(onProfile)}>
                      <div className="user-avatar">
                        {user.email?.charAt(0).toUpperCase()}
                      </div>
                      <div className="user-info">
                        <p className="user-email">{user.email}</p>
                        {isAdmin && <span className="user-badge">Admin</span>}
                      </div>
                    </div>
                  )}
                  <div className="section-container">
                    <div>
                      <h4 className="section-header">Navigation</h4>
                      {onHome && <button onClick={() => handleAction(onHome)} className="drawer-btn"><span className="icon"><Home size={18} aria-hidden="true" /></span><span>Home</span></button>}
                      {onLoadStories && <button onClick={() => handleAction(onLoadStories)} className="drawer-btn"><span className="icon"><BookOpen size={18} aria-hidden="true" /></span><span>Load Saved Story</span></button>}
                      {onOfflineManager && <button onClick={() => handleAction(onOfflineManager)} className="drawer-btn"><span className="icon"><FolderOpen size={18} aria-hidden="true" /></span><span>Offline Manager</span></button>}
                      {onNewStory && <button onClick={() => handleAction(onNewStory)} className="drawer-btn"><span className="icon"><Sparkles size={18} aria-hidden="true" /></span><span>New Story</span></button>}
                      {isPlayingStory && onSaveStory && <button onClick={() => handleAction(onSaveStory)} className="drawer-btn primary"><span className="icon"><Save size={18} aria-hidden="true" /></span><span>Save Story</span></button>}
                      {isPlayingStory && onDownloadStory && <button onClick={() => handleAction(onDownloadStory)} className="drawer-btn primary"><span className="icon"><Download size={18} aria-hidden="true" /></span><span>Download Story</span></button>}
                      {currentStory?.persistent_path && <button onClick={() => handleAction(onShowFileViewer)} className="drawer-btn"><span className="icon"><FileText size={18} aria-hidden="true" /></span><span>View Current File</span></button>}
                    </div>
                    {isAdmin && onAdminClick && (
                      <>
                        <div className="section-divider"></div>
                        <div>
                          <h4 className="section-header">Admin</h4>
                          <button onClick={() => handleAction(onAdminClick)} className="drawer-btn"><span className="icon"><Settings size={18} aria-hidden="true" /></span><span>Admin Panel</span></button>
                        </div>
                      </>
                    )}
                    <div className="section-divider"></div>
                    <div>
                      <h4 className="section-header">Account</h4>
                      <button onClick={() => { handleCheckUpdate(); setIsMobileOpen(false) }} className="drawer-btn" disabled={isCheckingUpdate}>
                        <span className="icon"><RefreshCw size={18} aria-hidden="true" className={isCheckingUpdate ? 'spin-icon' : ''} /></span><span>Check for Updates</span>
                      </button>
                      {!isPWA ? (
                        <button onClick={() => { handleInstallPWA(); setIsMobileOpen(false) }} className="drawer-btn primary">
                          <span className="icon"><Smartphone size={18} aria-hidden="true" /></span><span>Install App</span>
                        </button>
                      ) : (
                        <div className="status-indicator"><span className="icon"><Check size={18} aria-hidden="true" /></span><span>App Installed</span></div>
                      )}
                      {onLogout && <button onClick={() => handleAction(onLogout)} className="drawer-btn"><span className="icon"><LogOut size={18} aria-hidden="true" /></span><span>Logout</span></button>}
                    </div>
                  </div>
                </div>
              </motion.div>
            </>
          )}
        </AnimatePresence>,
        document.body
      )}
    </>
  )
}

export default NavigationMenu
