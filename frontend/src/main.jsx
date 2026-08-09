import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { AuthProvider } from './context/AuthContext'
import { DialogProvider } from './context/DialogContext'
import { registerServiceWorker, promptInstall } from './utils/serviceWorkerRegistration'
import './index.css'

// Register Service Worker for offline capability
registerServiceWorker()

// Enable PWA install prompt
promptInstall()

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <DialogProvider>
          <App />
        </DialogProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
