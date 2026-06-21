import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './App.css'

// Keep Vite development free from stale PWA assets; register only production builds.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    if (import.meta.env.DEV) {
      Promise.all([
        navigator.serviceWorker.getRegistrations(),
        'caches' in window ? caches.keys() : Promise.resolve([]),
      ]).then(async ([registrations, cacheKeys]) => {
        const appRegistrations = registrations.filter(registration =>
          registration.scope.startsWith(`${window.location.origin}/app/`)
        )
        const appCaches = cacheKeys.filter(key => key.startsWith('cogos'))
        await Promise.all([
          ...appRegistrations.map(registration => registration.unregister()),
          ...appCaches.map(key => caches.delete(key)),
        ])

        const reloadKey = 'cogos-dev-cache-cleaned'
        if ((appRegistrations.length || appCaches.length) && !sessionStorage.getItem(reloadKey)) {
          sessionStorage.setItem(reloadKey, '1')
          window.location.reload()
          return
        }
        sessionStorage.removeItem(reloadKey)
      })
      return
    }

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
