/* Smart Gym service worker: Web Push display + click handling. Plain JS, no bundler. */

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let payload = { title: 'Smart Gym', body: 'You have a new notification.', url: '/dashboard', tag: undefined };
  if (event.data) {
    try {
      payload = Object.assign(payload, event.data.json());
    } catch (_err) {
      payload.body = event.data.text();
    }
  }
  const options = {
    body: payload.body,
    tag: payload.tag,
    renotify: Boolean(payload.tag),
    icon: '/icon.svg',
    badge: '/icon.svg',
    data: { url: payload.url || '/dashboard' },
  };
  event.waitUntil(self.registration.showNotification(payload.title || 'Smart Gym', options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/dashboard';
  const absolute = new URL(target, self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url === absolute && 'focus' in client) return client.focus();
      }
      for (const client of clients) {
        if ('navigate' in client && 'focus' in client) {
          return client.navigate(absolute).then((c) => (c ? c.focus() : undefined));
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(absolute);
      return undefined;
    }),
  );
});
