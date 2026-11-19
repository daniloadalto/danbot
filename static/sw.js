/* DanBot Pro - service worker (19/11): app instalavel + notificacoes de WIN / LOSS / EMPATE.
   Nao guarda nada em cache: o painel e sempre carregado do servidor (dados ao vivo). */
const ICON = '/static/icon-192.png';
const BADGE = '/static/badge-96.png';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

// sem cache: as paginas vem sempre do servidor; sem internet mostra um aviso em vez da tela de erro do navegador
const OFFLINE = '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<body style="background:#04080a;color:#e8f5ee;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center">' +
  '<div><h2 style="color:#39FF14">DANBOT</h2><h3>Sem conexao</h3>' +
  '<p style="color:#7f9b8c">O bot continua rodando no servidor.<br>Abra de novo quando a internet voltar.</p></div></body>';
self.addEventListener('fetch', (e) => {
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
  }
});

self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { title: 'DanBot', body: e.data ? e.data.text() : '' }; }
  const title = d.title || 'DanBot';
  const opts = {
    body: d.body || '',
    icon: ICON,
    badge: BADGE,
    tag: (d.tag || 'danbot') + '-' + Date.now(),   // cada resultado e uma notificacao separada
    renotify: true,
    vibrate: d.kind === 'win' ? [80, 40, 80] : (d.kind === 'loss' ? [250] : [60]),
    data: { url: d.url || '/' }
  };
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ('focus' in c) return c.focus(); }
    return self.clients.openWindow(url);
  }));
});
