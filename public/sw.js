const CACHE_NAME = "build123d-flow-static-v2";
const PYODIDE_PREFIX = "/pyodide/v314.0.7/full/";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((name) => name.startsWith("build123d-flow-static-") && name !== CACHE_NAME)
      .map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  const scope = new URL(self.registration.scope);
  const sameOriginAppAsset = url.origin === self.location.origin &&
    url.pathname.startsWith(scope.pathname) &&
    (url.pathname.includes("/assets/") || url.pathname.includes("/python/") ||
      url.pathname.endsWith("/favicon.svg"));
  const pyodideAsset = url.origin === "https://cdn.jsdelivr.net" &&
    url.pathname.startsWith(PYODIDE_PREFIX);

  if (request.mode === "navigate" && url.origin === self.location.origin) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      try {
        const response = await fetch(request);
        if (response.ok) await cache.put(request, response.clone()).catch(() => {});
        return response;
      } catch {
        return await cache.match(request) || await cache.match(new URL("index.html", scope).href);
      }
    })());
    return;
  }

  if (!sameOriginAppAsset && !pyodideAsset) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    if (cached) return cached;
    const response = await fetch(request);
    if (response.ok || response.type === "opaque") {
      await cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  })());
});
