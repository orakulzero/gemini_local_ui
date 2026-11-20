// Background Service Worker:
// Keeps Google/Gemini cookies synced to http://localhost:8000/admin/session or via Native Messaging.
importScripts("common.js");

const ROTATING_COOKIES = new Set([
  "SID", "SIDCC", "SAPISID", "__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-1PSIDCC",
  "__Secure-3PSID", "__Secure-3PSIDTS", "__Secure-3PSIDCC"
]);
const CHANGE_DEBOUNCE_MS = 5000;
const MIN_INTERVAL_MS = 60000;
let changeTimer = null;
let syncing = false;

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
