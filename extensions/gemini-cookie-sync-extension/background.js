// Keeps the gateway's copy of the session in step with the browser.
//
// Google rotates __Secure-1PSIDTS and friends in the browser; the gateway's old
// copy stops working minutes later. So besides a periodic sync, every change of
// a sign-in cookie triggers a sync shortly after.
importScripts("common.js");

const ROTATING_COOKIES = new Set([
  "SID", "SIDCC", "SAPISID", "__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-1PSIDCC",
  "__Secure-3PSID", "__Secure-3PSIDTS", "__Secure-3PSIDCC"
]);
const CHANGE_DEBOUNCE_MS = 5000;
// SIDCC changes on almost every Google page load; one push a minute is plenty.
const MIN_INTERVAL_MS = 60000;
let changeTimer = null;
let syncing = false;

async function runScheduledSync(reason) {
  const settings = await loadSettings();
  if (!settings.keepFresh || !settings.configured || syncing) return;
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

// Alarms survive restarts, but re-create them in case the browser dropped them.
chrome.runtime.onStartup.addListener(async () => {
  const settings = await loadSettings();
  await scheduleSync(settings.keepFresh);
  runScheduledSync("Browser startup");
});

chrome.runtime.onInstalled.addListener(async () => {
  const settings = await loadSettings();
  await scheduleSync(settings.keepFresh);
});
