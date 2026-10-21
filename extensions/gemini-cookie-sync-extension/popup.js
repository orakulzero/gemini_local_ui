const statusEl = document.getElementById("status");
const syncButton = document.getElementById("sync");
const exportButton = document.getElementById("export");
const inspectButton = document.getElementById("inspect");
const openButton = document.getElementById("open");
const gatewayInput = document.getElementById("gateway");
const keyInput = document.getElementById("key");
const autoInput = document.getElementById("auto");

function setStatus(message, kind = "") {
  statusEl.textContent = message;
  statusEl.className = kind;
}

function formatTime(ms) {
  return new Date(ms).toLocaleString("en-US");
}

function inspectionMessage(info) {
  const {
    cookies,
    selected,
    validation,
    legacyPresent,
    legacyMissing,
    available,
    authUser,
    pageMetadata
  } = info;

  const lines = [
    `Google cookies found: ${cookies.length}.`,
    "",
    `SAPISID: ${selected.has("SAPISID") ? "present" : "missing"}`,
    `Session cookie: ${validation.sessionCookie || "missing"}`,
    `XSRF / SNlM0e: ${pageMetadata.xsrfToken ? "present" : "missing"}`,
    `gemini_bl / cfb2h: ${pageMetadata.geminiBl ? "present" : "missing"}`,
    `auth_user: ${authUser ?? "primary account"}`,
    "",
    `Legacy cookies: ${legacyPresent.join(", ") || "none"}`,
    `Hidden from extension: ${legacyMissing.join(", ") || "none"}`,
    "",
    `Available for export: ${available.join(", ") || "none"}`
  ];

  if (pageMetadata.error) {
    lines.push("", `Page: ${pageMetadata.error}`);
  }

  if (validation.valid && pageMetadata.xsrfToken) {
    lines.push("", "Session is ready.");
  } else if (validation.valid) {
    lines.push("", "Cookies are present, page token is missing. Safe to send to gateway: the gateway will fetch the token automatically.");
  } else {
    lines.push("", "No active session: SAPISID and one of __Secure-1PSID, __Secure-3PSID, or SID are required. Please sign in to Google.");
  }

  return lines.join("\n");
}

async function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"], {
    type: "application/json;charset=utf-8"
  });
  const url = URL.createObjectURL(blob);

  await chrome.downloads.download({
    url,
    filename,
    saveAs: true,
    conflictAction: "uniquify"
  });

  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

async function saveSettings() {
  const settings = {
    gatewayUrl: gatewayInput.value.trim() || DEFAULT_SETTINGS.gatewayUrl,
    adminKey: keyInput.value.trim(),
    keepFresh: autoInput.checked
  };
  await chrome.storage.local.set(settings);
  await scheduleSync(settings.keepFresh);
  return { ...(await loadSettings()), ...settings };
}

async function showSettings() {
  const settings = await loadSettings();
  gatewayInput.value = settings.gatewayUrl;
  keyInput.value = settings.adminKey;
  autoInput.checked = settings.keepFresh;
  if (settings.lastSync) {
    const { at, ok, message } = settings.lastSync;
    setStatus(`Last sync: ${formatTime(at)} — ${ok ? "success" : "error"}\n${message}`, ok ? "ok" : "warn");
  }
}

syncButton.addEventListener("click", async () => {
  syncButton.disabled = true;
  setStatus("Sending session to gateway…");
  try {
    // Gateway access comes from host_permissions at install time: a runtime
    // permission prompt closes the popup on Linux and kills the send.
    const settings = await saveSettings();
    const reply = await syncToGateway(settings);
    const message =
      `Gateway accepted session.\n\n` +
      `Account models: ${(reply.models || []).join(", ") || "—"}\n` +
      (reply.skipped?.length ? `Unsupported yet: ${reply.skipped.join(", ")}\n` : "") +
      (settings.keepFresh ? `\nAuto-refresh: on cookie rotation and every ${SYNC_PERIOD_MINUTES} minutes.` : "");
    await recordSync(true, `Models: ${(reply.models || []).join(", ") || "—"}`);
    await chrome.storage.local.set({ configured: true });
    setStatus(message, "ok");
  } catch (error) {
    await recordSync(false, error?.message || String(error));
    setStatus(error?.message || String(error), "warn");
  } finally {
    syncButton.disabled = false;
  }
});

autoInput.addEventListener("change", async () => {
  await saveSettings();
});

openButton.addEventListener("click", async () => {
  await chrome.tabs.create({ url: "https://gemini.google.com/app" });
});

inspectButton.addEventListener("click", async () => {
  inspectButton.disabled = true;
  setStatus("Inspecting session and page…");

  try {
    const info = await buildInspection();
    const ready = info.validation.valid && Boolean(info.pageMetadata.xsrfToken);
    setStatus(inspectionMessage(info), ready ? "ok" : "warn");
  } catch (error) {
    setStatus(error?.message || String(error), "warn");
  } finally {
    inspectButton.disabled = false;
  }
});

exportButton.addEventListener("click", async () => {
  exportButton.disabled = true;
  setStatus("Reading cookies and XSRF…");

  try {
    const info = await buildInspection();

    if (!info.validation.valid || !info.pageMetadata.xsrfToken) {
      throw new Error(inspectionMessage(info));
    }

    await downloadJson("gemini-auth.json", buildSessionPayload(info));

    const exportedNames = EXPORT_ORDER.filter((name) => info.selected.has(name));
    setStatus(
      `Created gemini-auth.json: ${exportedNames.length} cookies, XSRF present.\n\n` +
      `Session cookie: ${info.validation.sessionCookie}\n` +
      `gemini_bl: ${info.pageMetadata.geminiBl ? "present" : "missing — config default will be used"}\n` +
      `auth_user: ${info.authUser ?? "null"}\n\n` +
      "This file grants access to your account: do not share it or commit it to Git.",
      "ok"
    );
  } catch (error) {
    setStatus(error?.message || String(error), "warn");
  } finally {
    exportButton.disabled = false;
  }
});

showSettings();
