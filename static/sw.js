/**
 * AMN Stock — Service Worker
 * Stratégie hybride :
 *   • Statiques (/static/, CDN) → Stale-While-Revalidate (cache immédiat + MAJ silencieuse)
 *   • Pages HTML (navigation) → Network First + fallback cache + fallback offline
 *   • API (/api/)              → Network Only (données temps-réel, pas de cache)
 *
 * Compatible avec les connexions 3G instables ciblées (low-end smartphones).
 */

'use strict';

// ─── Versionnage ────────────────────────────────────────────────────────────
// Incrémentez CACHE_VER à chaque déploiement pour purger les anciens caches.
const CACHE_VER     = 'amn-v1';
const STATIC_CACHE  = `${CACHE_VER}-static`;   // assets long-term (CSS/JS/images)
const DYNAMIC_CACHE = `${CACHE_VER}-pages`;    // pages HTML visitées
const OFFLINE_URL   = '/offline/';

// Taille maximale du cache dynamique (pages HTML) pour épargner la mémoire
// des téléphones bas de gamme.
const DYNAMIC_CACHE_LIMIT = 40;

// ─── Assets pré-cachés à l'installation ─────────────────────────────────────
// Seule la page offline est garantie dès l'install. Les assets CDN sont
// récupérés en no-cors (réponse opaque) : ils serviront même hors-ligne.
const PRECACHE_ASSETS = [
  OFFLINE_URL,
  // Bootstrap CSS (opaque CDN)
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  // Bootstrap JS (opaque CDN)
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js',
  // Bootstrap Icons CSS (opaque CDN)
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css',
];

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Détermine si une URL pointe vers un asset statique local ou CDN. */
function isStaticAsset(url) {
  const u = new URL(url);
  return (
    u.pathname.startsWith('/static/') ||
    u.hostname.includes('jsdelivr.net') ||
    u.hostname.includes('cdnjs.cloudflare.com') ||
    /\.(woff2?|ttf|eot|otf)$/.test(u.pathname)
  );
}

/** Détermine si la requête est une navigation HTML (page). */
function isNavigation(request) {
  return request.mode === 'navigate';
}

/** Détermine si la requête est une appel API (ne pas cacher). */
function isApiCall(url) {
  return new URL(url).pathname.startsWith('/api/');
}

/**
 * Élagage FIFO du cache dynamique pour ne pas dépasser DYNAMIC_CACHE_LIMIT.
 * Supprime les entrées les plus anciennes en premier.
 */
async function trimDynamicCache() {
  const cache = await caches.open(DYNAMIC_CACHE);
  const keys  = await cache.keys();
  if (keys.length > DYNAMIC_CACHE_LIMIT) {
    const toDelete = keys.slice(0, keys.length - DYNAMIC_CACHE_LIMIT);
    await Promise.all(toDelete.map(k => cache.delete(k)));
  }
}

// ─── INSTALL ─────────────────────────────────────────────────────────────────
// Pré-cache les assets critiques. skipWaiting() active immédiatement le SW
// sans attendre la fermeture de tous les onglets.
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(cache => {
      // Les assets CDN sont récupérés en no-cors (réponses opaques).
      const requests = PRECACHE_ASSETS.map(url => {
        const isCDN = url.startsWith('https://');
        return cache.add(new Request(url, isCDN ? { mode: 'no-cors' } : {}))
          .catch(err => console.warn('[SW] Précache échoué pour', url, err));
      });
      return Promise.allSettled(requests);
    }).then(() => self.skipWaiting())
  );
});

// ─── ACTIVATE ────────────────────────────────────────────────────────────────
// Supprime tous les caches dont le nom ne correspond plus à la version courante.
// Évite la saturation du stockage des téléphones bas de gamme.
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      const toDelete = cacheNames.filter(
        name => !name.startsWith(CACHE_VER)
      );
      return Promise.all(toDelete.map(name => {
        console.log('[SW] Suppression ancien cache :', name);
        return caches.delete(name);
      }));
    }).then(() => self.clients.claim())  // prend le contrôle sans rechargement
  );
});

// ─── FETCH ───────────────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = request.url;

  // Ignorer les requêtes non-GET (POST, PATCH…) et les extensions Chrome
  if (request.method !== 'GET' || url.startsWith('chrome-extension://')) {
    return;
  }

  // ── 1. API : Network Only ──────────────────────────────────────────────────
  // Les endpoints /api/ retournent des données temps-réel. Pas de cache.
  if (isApiCall(url)) {
    return; // laisse le navigateur gérer nativement
  }

  // ── 2. Assets statiques : Stale-While-Revalidate ──────────────────────────
  // Répond instantanément depuis le cache, puis met à jour silencieusement.
  // Idéal pour CSS/JS/images sur réseau lent (3G).
  if (isStaticAsset(url)) {
    event.respondWith(staleWhileRevalidate(request, STATIC_CACHE));
    return;
  }

  // ── 3. Navigation (pages HTML) : Network First ────────────────────────────
  // Priorité au réseau pour des données fraîches. Si le réseau échoue,
  // on sert le cache. Si rien en cache → page offline.html personnalisée.
  if (isNavigation(request)) {
    event.respondWith(networkFirstWithOfflineFallback(request));
    return;
  }

  // ── 4. Autres ressources : Stale-While-Revalidate ─────────────────────────
  event.respondWith(staleWhileRevalidate(request, STATIC_CACHE));
});

// ─── Stratégies de cache ─────────────────────────────────────────────────────

/**
 * Stale-While-Revalidate :
 * 1. Répond immédiatement depuis le cache si disponible.
 * 2. En parallèle, récupère une version fraîche du réseau et met à jour le cache.
 * 3. Si pas en cache, récupère depuis le réseau et stocke.
 */
async function staleWhileRevalidate(request, cacheName) {
  const cache    = await caches.open(cacheName);
  const cached   = await cache.match(request);

  // Lance la mise à jour réseau en arrière-plan (sans attendre)
  const networkFetch = fetch(request).then(networkResponse => {
    if (networkResponse && networkResponse.status === 200) {
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  }).catch(() => null);

  // Retourne le cache instantanément (évite latence réseau sur 3G)
  return cached || networkFetch;
}

/**
 * Network First avec fallback cache puis page offline :
 * 1. Tente le réseau.
 * 2. Si succès → stocke en cache dynamique et retourne la réponse.
 * 3. Si réseau KO → cherche dans le cache dynamique.
 * 4. Si pas en cache → retourne offline.html.
 */
async function networkFirstWithOfflineFallback(request) {
  const cache = await caches.open(DYNAMIC_CACHE);

  try {
    const networkResponse = await fetch(request);
    // Seules les réponses 200 OK sont mises en cache
    if (networkResponse.status === 200) {
      cache.put(request, networkResponse.clone());
      trimDynamicCache(); // nettoyage asynchrone, sans bloquer
    }
    return networkResponse;
  } catch {
    // Réseau indisponible — cherche dans le cache
    const cached = await cache.match(request);
    if (cached) return cached;

    // Rien en cache → page offline de substitution
    const offlineCache    = await caches.open(STATIC_CACHE);
    const offlineFallback = await offlineCache.match(OFFLINE_URL);
    return offlineFallback || new Response(
      '<h1>Hors-ligne</h1><p>Veuillez vérifier votre connexion.</p>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
  }
}
