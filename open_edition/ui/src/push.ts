// Push notifications on this device (the server's ade/push.py): what needs the owner reaches it when no page is open,
// through this browser's own push service and the site's service worker (/sw.js), which shows them and opens the app
// when one is tapped. The subscription's keys go to the server; nothing else of this browser does.
import { api } from "./api";

export type PushState = "unsupported" | "home-screen" | "off" | "on";

function keyBytes(b64: string): Uint8Array {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64.length + 3) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

const supported = () => typeof navigator !== "undefined" && "serviceWorker" in navigator && typeof window !== "undefined" && "PushManager" in window;

async function current(): Promise<PushSubscription | null> {
  const reg = await navigator.serviceWorker.getRegistration("/");
  return reg ? reg.pushManager.getSubscription() : null;
}

export async function pushState(): Promise<PushState> {
  if (!supported()) {
    const phone = /iPhone|iPad/.test(navigator.userAgent) && !(navigator as Navigator & { standalone?: boolean }).standalone;
    return phone ? "home-screen" : "unsupported";
  }
  return (await current()) ? "on" : "off";
}

export async function pushOn(): Promise<void> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Notifications are not allowed for this site in this browser.");
  const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  const info = await api<{ key: string }>("/api/ade/push");
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(info.key) as BufferSource });
  const json = sub.toJSON();
  await api("/api/ade/push/subscribe", { method: "POST", body: { endpoint: json.endpoint, keys: json.keys } });
}

export async function pushTest(): Promise<{ sent: number; devices: number }> {
  return api("/api/ade/push/test", { method: "POST" });
}

export async function pushOff(): Promise<void> {
  const sub = await current();
  if (sub) {
    await api("/api/ade/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } });
    await sub.unsubscribe();
  }
}
