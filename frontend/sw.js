/* Service Worker do Sistema de Orçamentos Públicos (PWA).
   Estratégia:
   - APP SHELL (html/css/js/ícones): cache-first com atualização em segundo plano,
     para abrir rápido e funcionar mesmo com conexão ruim.
   - API (/api/...): SEMPRE rede (nunca cacheia) — os dados precisam ser atuais.
*/
const CACHE = 'orcamentos-shell-v2';
const SHELL = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  '/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((chaves) =>
      Promise.all(chaves.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Só tratamos GET do mesmo domínio.
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;

  // API nunca é cacheada — sempre busca na rede (dados sempre atuais).
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(req));
    return;
  }

  // App shell: responde do cache e atualiza em segundo plano (stale-while-revalidate).
  event.respondWith(
    caches.match(req).then((cacheado) => {
      const naRede = fetch(req)
        .then((resp) => {
          if (resp && resp.status === 200 && resp.type === 'basic') {
            const clone = resp.clone();
            caches.open(CACHE).then((c) => c.put(req, clone)).catch(() => {});
          }
          return resp;
        })
        .catch(() => cacheado);
      return cacheado || naRede;
    })
  );
});
