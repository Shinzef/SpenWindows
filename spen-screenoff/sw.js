const CACHE = 'spen-v1';
const FILES = [
    '/wakelock.html',
    '/manifest.json',
    '/icon.png'
];

// On install: cache all files immediately
self.addEventListener('install', e => {
    e.waitUntil(
        caches.open(CACHE).then(cache => cache.addAll(FILES))
    );
    self.skipWaiting();
});

self.addEventListener('activate', e => {
    self.clients.claim();
});

// On every fetch: serve from cache, never hit network
self.addEventListener('fetch', e => {
    e.respondWith(
        caches.match(e.request).then(r => r || fetch(e.request))
    );
});