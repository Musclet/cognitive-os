// Cognitive OS Service Worker
// Network-first with cache fallback for API; cache-first for static assets

const CACHE_NAME = 'cogos-v3';
const STATIC_CACHE = 'cogos-static-v3';

// Assets to pre-cache on install
const PRECACHE_URLS = [
  '/app/',
  '/app/icon-192.svg',
  '/app/icon-512.svg',
  '/app/manifest.json',
];

// Install: pre-cache static assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      return cache.addAll(PRECACHE_URLS).catch((err) => {
        console.warn('[SW] Pre-cache partial failure:', err.message);
      });
    }).then(() => self.skipWaiting())
  );
});

// Activate: clean old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => {
          return key !== STATIC_CACHE && key !== CACHE_NAME;
        }).map((key) => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

// Helper: is this an API request?
function isApiRequest(url) {
  return url.pathname.startsWith('/api/');
}

// Helper: is this a static asset (JS, CSS, fonts, images)?
function isStaticAsset(url) {
  const ext = url.pathname.split('.').pop();
  return /^(js|css|woff2?|ttf|svg|png|jpg|ico)$/.test(ext);
}

// Helper: is this a navigation request?
function isNavigation(request) {
  return request.mode === 'navigate';
}

// Fetch: network-first for API, cache-first for static, network-only for everything else
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Skip non-GET requests and chrome-extension:// URLs
  if (event.request.method !== 'GET') return;
  if (!url.protocol.startsWith('http')) return;

  // Authenticated API data must never be persisted in Cache Storage.
  if (isApiRequest(url)) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Static assets: Cache-first (with network update)
  if (isStaticAsset(url)) {
    event.respondWith(staticCacheFirst(event.request));
    return;
  }

  // Navigation requests: Network-first with offline fallback
  if (isNavigation(event.request)) {
    event.respondWith(navigationNetworkFirst(event.request));
    return;
  }

  // Everything else: network-first
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});

// Static assets: cache-first, update cache in background
async function staticCacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) {
    // Update cache in background
    fetch(request).then((response) => {
      if (response.ok) {
        caches.open(STATIC_CACHE).then((cache) => {
          cache.put(request, response);
        });
      }
    }).catch(() => {});
    return cached;
  }
  // Not in cache, fetch from network
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    return new Response('Offline', { status: 503 });
  }
}

// Navigation: network-first, serve the app shell on failure
async function navigationNetworkFirst(request) {
  try {
    const response = await fetch(request);
    return response;
  } catch (err) {
    // Try cache first
    const cached = await caches.match(request);
    if (cached) return cached;
    // Fall back to app shell
    return caches.match('/app/');
  }
}

// Handle offline status changes
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
