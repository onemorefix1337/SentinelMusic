// sentinel music — service worker
const CACHE = 'sm-v1';
const AUDIO_CACHE = 'sm-audio-v1';

const STATIC = [
  './',
  './index.html',
  './config.js',
  'https://telegram.org/js/telegram-web-app.js',
  'https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,300;0,400;0,500;1,300&family=Bebas+Neue&display=swap',
];

// установка — кэшируем статику
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
  );
});

// активация — чистим старые кэши
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE && k !== AUDIO_CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// fetch — стратегия зависит от типа запроса
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // аудио стрим — cache first, потом сеть
  if (url.pathname.includes('/api/stream/')) {
    e.respondWith(
      caches.open(AUDIO_CACHE).then(async cache => {
        // ключ без токена чтобы кэш работал между сессиями
        const cacheKey = url.pathname;
        const cached = await cache.match(cacheKey);
        if (cached) return cached;

        try {
          const resp = await fetch(e.request);
          if (resp.ok) {
            // клонируем и кэшируем
            cache.put(cacheKey, resp.clone());
          }
          return resp;
        } catch(err) {
          return new Response('offline', { status: 503 });
        }
      })
    );
    return;
  }

  // обложки — cache first
  if (url.pathname.includes('/api/thumb/') || url.hostname.includes('sndcdn.com') || url.hostname.includes('ytimg.com')) {
    e.respondWith(
      caches.open(CACHE).then(async cache => {
        const cached = await cache.match(e.request);
        if (cached) return cached;
        try {
          const resp = await fetch(e.request);
          if (resp.ok) cache.put(e.request, resp.clone());
          return resp;
        } catch(err) {
          return new Response('', { status: 404 });
        }
      })
    );
    return;
  }

  // апи запросы — только сеть (данные должны быть свежими)
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request).catch(() =>
      new Response(JSON.stringify({ error: 'offline' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' }
      })
    ));
    return;
  }

  // статика — cache first, потом сеть
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(resp => {
        if (resp.ok) {
          caches.open(CACHE).then(c => c.put(e.request, resp.clone()));
        }
        return resp;
      }).catch(() => caches.match('./index.html'));
    })
  );
});

// сообщение от клиента — удалить трек из кэша
self.addEventListener('message', e => {
  if (e.data?.type === 'UNCACHE_TRACK') {
    const path = e.data.path;
    caches.open(AUDIO_CACHE).then(c => c.delete(path));
  }
});
