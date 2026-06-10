import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './App.css'

// Register service worker for PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' }).then(
      (registration) => {
        console.log('[PWA] Service Worker registered:', registration.scope)
        // Auto-update on new version
        registration.addEventListener('updatefound', () => {
          const newWorker = registration.installing
          if (newWorker) {
            newWorker.addEventListener('statechange', () => {
              if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                console.log('[PWA] New version available — refresh to update')
                // Could show a "New version available" toast here
              }
            })
          }
        })
      },
      (err) => {
        console.warn('[PWA] Service Worker registration failed:', err.message)
      }
    )
  })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
