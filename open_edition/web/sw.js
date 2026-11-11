"use strict";
// Proofpane Community's service worker: it shows the ADE's notifications when no page is open, and opens the ADE
// when one is tapped. It caches nothing and fetches nothing.

function onSite(url) {
  // A page of this site, or the ADE. The browser's own URL parser decides, not a test on the text: "/\evil.example"
  // and a "/" followed by a tab and a slash both start with "/" and not "//", and both parse to another host.
  try {
    const u = new URL(String(url), self.location.origin);
    if (u.origin === self.location.origin) return u.pathname + u.search + u.hash;
  } catch (e) { /* not a URL at all */ }
  return "/#ade";
}

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data ? event.data.text() : "" }; }
  event.waitUntil(self.registration.showNotification(String(data.title || "Proofpane Community"), {
    body: String(data.body || ""), tag: String(data.tag || "ade"), icon: "/favicon.svg", data: { url: onSite(data.url) },
  }));
});

// A window that draws one panel of the control view's map (a pop-out, /app/?pop=...: ui/src/popOut.ts). A tapped
// notice opens the ADE in a window of the app's own, never in one of these, which would draw the whole app at a pop-out's
// size and leave the page the person works in as it was.
function isPopOut(url) {
  try { return new URL(String(url)).searchParams.has("pop"); } catch (e) { return false; }
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = onSite(event.notification.data && event.notification.data.url);
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const client of list) {
      if ("focus" in client && !isPopOut(client.url)) { client.navigate(url); return client.focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
