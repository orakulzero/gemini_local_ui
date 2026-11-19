// Background Service Worker:
// 1. Keeps Google/Gemini cookies + SNlM0e token synced to http://localhost:8000/admin/session
// 2. Relays real-time Guided Learning turns & visuals from content_script.js to
//    http://localhost:8000/api/learning/* without Content-Security-Policy (CSP) restrictions.
importScripts("common.js");

const ROTATING_COOKIES = new Set([
  "SID", "SIDCC", "SAPISID", "__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-1PSIDCC",
  "__Secure-3PSID", "__Secure-3PSIDTS", "__Secure-3PSIDCC"
]);
const CHANGE_DEBOUNCE_MS = 5000;
const MIN_INTERVAL_MS = 60000;
let changeTimer = null;
let syncing = false;

async function getGatewayBase() {
  const settings = await loadSettings();
  const raw = (settings.gatewayUrl || "http://127.0.0.1:8000").trim().replace(/\/+$/, "");
  return raw || "http://127.0.0.1:8000";
}

async function runScheduledSync(reason) {
  const settings = await loadSettings();
  if (!settings.keepFresh || syncing) return;
  const sinceLast = Date.now() - (settings.lastSync?.at || 0);
  if (sinceLast < MIN_INTERVAL_MS) {
    clearTimeout(changeTimer);
    changeTimer = setTimeout(() => runScheduledSync(reason), MIN_INTERVAL_MS - sinceLast + 1000);
    return;
  }
  syncing = true;
  try {
    const reply = await syncToGateway(settings);
    await recordSync(true, `${reason}. Models: ${(reply.models || []).join(", ") || "—"}`);
  } catch (error) {
    await recordSync(false, `${reason}: ${error?.message || String(error)}`);
  } finally {
    syncing = false;
  }
}

chrome.cookies.onChanged.addListener(({ removed, cookie }) => {
  if (removed || !ROTATING_COOKIES.has(cookie.name) || !appliesToGemini(cookie)) return;
  clearTimeout(changeTimer);
  changeTimer = setTimeout(() => runScheduledSync("Cookies updated"), CHANGE_DEBOUNCE_MS);
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === SYNC_ALARM) runScheduledSync("Scheduled sync");
});

chrome.runtime.onStartup.addListener(async () => {
  const settings = await loadSettings();
  await scheduleSync(settings.keepFresh);
  runScheduledSync("Browser startup");
});

chrome.runtime.onInstalled.addListener(async () => {
  const settings = await loadSettings();
  await scheduleSync(settings.keepFresh);
  runScheduledSync("Extension installed");
});

// Relay messages from content_script.js on gemini.google.com to the local Antigravity + Obsidian bridge
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !message.type) return false;

  (async () => {
    const base = await getGatewayBase();
    try {
      if (message.type === "LEARNING_STATUS") {
        const resp = await fetch(`${base}/api/learning/status`, { method: "GET" });
        const data = await resp.json();
        sendResponse({ ok: resp.ok, data });
        return;
      }
      if (message.type === "LEARNING_INGEST") {
        const resp = await fetch(`${base}/api/learning/ingest`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(message.payload || {})
        });
        const data = await resp.json();
        sendResponse({ ok: resp.ok, data });
        return;
      }
      if (message.type === "LEARNING_SYNC_MAIN") {
        const resp = await fetch(`${base}/api/learning/sync-to-main`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}"
        });
        const data = await resp.json();
        sendResponse({ ok: resp.ok, data });
        return;
      }
      if (message.type === "LEARNING_PULL_CID") {
        const resp = await fetch(`${base}/api/learning/pull-cid`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(message.payload || {})
        });
        const data = await resp.json();
        sendResponse({ ok: resp.ok, data });
        return;
      }
      sendResponse({ ok: false, error: `Unknown message type: ${message.type}` });
    } catch (err) {
      sendResponse({ ok: false, error: err?.message || String(err) });
    }
  })();

  return true; // keep channel open for async sendResponse
});
