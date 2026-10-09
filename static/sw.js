// SlideGen service worker: makes the site installable as a phone app.
// Pages always come from the server (decks change); styles and icons are cached,
// and a small offline page is shown when the phone has no internet.
const CACHE = "slidegen-v3";
const SHELL = ["/static/app.css", "/static/slides.css", "/static/offline.html",
               "/static/icons/icon-192.png", "/static/icons/icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/static/offline.html")));
    return;
  }
  if (SHELL.includes(new URL(req.url).pathname)) {
    // serve from cache, refresh in the background
    e.respondWith(caches.match(req).then(hit => {
      const net = fetch(req).then(res => {
        if (res.ok) caches.open(CACHE).then(c => c.put(req, res.clone()));
        return res;
      });
      return hit || net;
    }));
  }
});
