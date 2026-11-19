// MOiAD Gemini Guided Learning ⇄ Antigravity ⇄ Obsidian Real-Time Harvester
// Injected on https://gemini.google.com/*

(function () {
  if (window.__MOIAD_LEARNING_HARVESTER_LOADED__) return;
  window.__MOIAD_LEARNING_HARVESTER_LOADED__ = true;

  let autoCaptureEnabled = true;
  let lastCapturedHash = "";
  let debounceTimer = null;

  function getConversationId() {
    const m = location.pathname.match(/\/(?:app|gem\/[^/]+)\/([a-zA-Z0-9_-]{8,})/);
    return m ? m[1] : "gemini_live_session";
  }

  function extractCleanMarkdownWithMath(rootEl) {
    if (!rootEl) return "";
    const clone = rootEl.cloneNode(true);

    // Replace KaTeX math annotations with $...$ or $$...$$
    const annotations = clone.querySelectorAll('annotation[encoding="application/x-tex"]');
    annotations.forEach((ann) => {
      const tex = (ann.textContent || "").trim();
      if (!tex) return;
      const katexContainer = ann.closest(".katex-display, .math-block, [ display='block' ]");
      const wrapped = katexContainer ? `\n$$\n${tex}\n$$\n` : `$${tex}$`;
      const outer = ann.closest(".katex, .math-inline, .katex-display") || ann;
      outer.replaceWith(document.createTextNode(wrapped));
    });

    // Also handle [data-math] attributes used by Gemini web components
    clone.querySelectorAll("[data-math]").forEach((el) => {
      const tex = (el.getAttribute("data-math") || "").trim();
      if (!tex) return;
      const isBlock = el.classList.contains("math-block") || el.tagName === "DIV";
      el.replaceWith(document.createTextNode(isBlock ? `\n$$\n${tex}\n$$\n` : `$${tex}$`));
    });

    return (clone.innerText || clone.textContent || "").trim();
  }

  function harvestVisualsFromTurn(responseEl) {
    const visuals = [];
    if (!responseEl) return visuals;

    // 1. Harvest inline <svg> diagrams (skip tiny 16x16 / 24x24 UI icons)
    const svgs = responseEl.querySelectorAll("svg");
    svgs.forEach((svg, idx) => {
      const rect = svg.getBoundingClientRect();
      const vb = svg.getAttribute("viewBox") || "";
      const isLargeEnough = rect.width >= 90 || rect.height >= 70 || /0\s+0\s+[1-9]\d{2,}/.test(vb);
      const insideButton = Boolean(svg.closest("button, mat-icon, [role='button']"));
      if (isLargeEnough && !insideButton) {
        visuals.push({
          type: "svg",
          content: svg.outerHTML,
          title: `SVG-визуализация Gemini #${idx + 1}`
        });
      }
    });

    // 2. Harvest <canvas> charts / interactive plots as PNG base64
    const canvases = responseEl.querySelectorAll("canvas");
    canvases.forEach((cv, idx) => {
      try {
        if (cv.width >= 80 && cv.height >= 60) {
          const dataUrl = cv.toDataURL("image/png");
          if (dataUrl && dataUrl.includes("base64,")) {
            visuals.push({
              type: "canvas",
              content: dataUrl,
              title: `Canvas-график Gemini #${idx + 1}`
            });
          }
        }
      } catch (_) {
        // Tainted canvas fallback ignored
      }
    });

    // 3. Harvest interactive HTML / SVG code blocks if present
    const codeBlocks = responseEl.querySelectorAll("pre code, code-block");
    codeBlocks.forEach((cb, idx) => {
      const txt = (cb.innerText || cb.textContent || "").trim();
      if (txt.startsWith("<svg") && txt.includes("</svg>")) {
        visuals.push({
          type: "svg",
          content: txt,
          title: `SVG-код из ответа Gemini #${idx + 1}`
        });
      } else if ((txt.includes("<!DOCTYPE html>") || txt.includes("<canvas")) && txt.length > 120) {
        visuals.push({
          type: "html",
          content: txt,
          title: `Интерактивный виджет Gemini #${idx + 1}`
        });
      }
    });

    return visuals;
  }

  function extractLatestTurnFromDOM() {
    const userNodes = document.querySelectorAll(
      "user-query, [data-test-id*='user-query'], .user-query-container, .query-text"
    );
    const modelNodes = document.querySelectorAll(
      "model-response, message-content, .model-response-text, .response-container-content"
    );

    const lastUserEl = userNodes.length ? userNodes[userNodes.length - 1] : null;
    const lastModelEl = modelNodes.length ? modelNodes[modelNodes.length - 1] : null;

    const userText = extractCleanMarkdownWithMath(lastUserEl);
    const geminiText = extractCleanMarkdownWithMath(lastModelEl);
    const visuals = harvestVisualsFromTurn(lastModelEl || document.body);
    const pageTitle = (document.title || "Сессия обучения Gemini").replace(/\s*-\s*Google Gemini$/i, "").trim();

    return {
      cid: getConversationId(),
      title: pageTitle,
      user_text: userText || "Разбор вопроса в режиме обучения",
      gemini_text: geminiText,
      visuals: visuals,
      source: "gemini_dom_live_harvester"
    };
  }


  function createFloatingHUD() {
    if (document.getElementById("moiad-learning-hud")) return;

    const hud = document.createElement("div");
    hud.id = "moiad-learning-hud";
    hud.style.cssText = [
      "position:fixed",
      "right:18px",
      "bottom:18px",
      "z-index:2147483646",
      "width:350px",
      "background:#FAF8F5",
      "color:#0F172A",
      "border:1.5px solid #0D9488",
      "border-radius:12px",
      "box-shadow:0 10px 28px rgba(15,23,42,0.18)",
      "font-family:'Inter','Segoe UI',system-ui,sans-serif",
      "font-size:13px",
      "padding:12px 14px",
      "line-height:1.4"
    ].join(";");

    hud.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <strong style="font-size:13.5px;color:#0F172A;">🎓 МОиАД Live → Obsidian</strong>
        <span id="moiad-hud-badge" style="font-size:11px;padding:2px 7px;border-radius:99px;background:#F0FDFA;color:#0D9488;border:1px solid #0D9488;">Связь...</span>
      </div>
      <div id="moiad-hud-stats" style="font-size:11.5px;color:#475569;margin-bottom:8px;">
        Вариант 8 (Иванов В. И.) · Сверка Antigravity активна
      </div>
      <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#334155;margin-bottom:8px;cursor:pointer;">
        <input type="checkbox" id="moiad-hud-auto" checked />
        Авто-сохранение ответов и SVG/Canvas в реальном времени
      </label>
      <div style="display:flex;flex-direction:column;gap:6px;">
        <button id="moiad-btn-capture" style="background:#0D9488;color:#FFFFFF;border:none;border-radius:8px;padding:7px 10px;font-weight:600;cursor:pointer;font-size:12px;">
          📥 Сохранить шаг + визуализации в Obsidian
        </button>
        <div style="display:flex;gap:6px;">
          <button id="moiad-btn-pull" style="flex:1;background:#EFF6FF;color:#2563EB;border:1px solid #93C5FD;border-radius:8px;padding:6px 8px;font-weight:600;cursor:pointer;font-size:11.5px;">
            🔄 Вытянуть весь чат
          </button>
          <button id="moiad-btn-sync" style="flex:1;background:#F5F3FF;color:#7C3AED;border:1px solid #C4B5FD;border-radius:8px;padding:6px 8px;font-weight:600;cursor:pointer;font-size:11.5px;">
            🚀 В основную базу
          </button>
        </div>
      </div>
      <div id="moiad-hud-drawer" style="display:none;margin-top:10px;padding-top:8px;border-top:1px solid #CBD5E1;max-height:220px;overflow-y:auto;font-size:11.5px;color:#1E293B;"></div>
    `;

    document.body.appendChild(hud);

    const autoBox = document.getElementById("moiad-hud-auto");
    autoBox.addEventListener("change", () => {
      autoCaptureEnabled = autoBox.checked;
    });

    document.getElementById("moiad-btn-capture").addEventListener("click", () => {
      sendCurrentTurnToBridge(true);
    });

    document.getElementById("moiad-btn-pull").addEventListener("click", () => {
      const badge = document.getElementById("moiad-hud-badge");
      badge.textContent = "RPC выгрузка...";
      chrome.runtime.sendMessage(
        { type: "LEARNING_PULL_CID", payload: { cid: getConversationId(), limit: 8 } },
        (resp) => {
          if (resp && resp.ok && resp.data?.status === "ok") {
            badge.textContent = `Выгружено: ${resp.data.turns_ingested} шаг.`;
            refreshStatus();
          } else {
            badge.textContent = "Ошибка RPC";
          }
        }
      );
    });

    document.getElementById("moiad-btn-sync").addEventListener("click", () => {
      const badge = document.getElementById("moiad-hud-badge");
      badge.textContent = "Синхронизация...";
      chrome.runtime.sendMessage({ type: "LEARNING_SYNC_MAIN" }, (resp) => {
        if (resp && resp.ok && resp.data?.status === "ok") {
          badge.textContent = `Влито в МОиАД (${resp.data.turns_synced} шаг.)`;
        } else {
          badge.textContent = "Ошибка синхр.";
        }
      });
    });

    refreshStatus();
  }

  function renderFactCheckInHUD(res) {
    const drawer = document.getElementById("moiad-hud-drawer");
    if (!drawer || !res || !res.factcheck) return;
    const fc = res.factcheck;
    const gtItems = (fc.variant8_ground_truth || []).slice(0, 3).map((x) => `<li>${x}</li>`).join("");
    const warnItems = (fc.corrections_and_warnings || []).slice(0, 2).map((x) => `<li style="color:#B45309;">⚠️ ${x}</li>`).join("");
    drawer.style.display = "block";
    drawer.innerHTML = `
      <div style="font-weight:700;color:#0D9488;margin-bottom:4px;">
        ✅ Сверка Antigravity (${fc.matched_pr} · визуализаций: ${res.saved_visuals_count || 0})
      </div>
      <ul style="margin:4px 0 6px 16px;padding:0;">${gtItems}${warnItems}</ul>
      <div style="background:#F0FDFA;border-left:3px solid #0D9488;padding:5px 7px;border-radius:4px;font-size:11px;">
        <strong>Для Ермохиной А. С.:</strong> ${fc.defense_ready_answer || ""}
      </div>
    `;
  }

  function sendCurrentTurnToBridge(forceManual) {
    const payload = extractLatestTurnFromDOM();
    if (!payload.gemini_text || payload.gemini_text.length < 25) return;

    const sig = `${payload.cid}::${payload.user_text.length}::${payload.gemini_text.length}::${payload.visuals.length}`;
    if (!forceManual && sig === lastCapturedHash) return;
    lastCapturedHash = sig;

    const badge = document.getElementById("moiad-hud-badge");
    if (badge) badge.textContent = "Сохранение...";

    chrome.runtime.sendMessage({ type: "LEARNING_INGEST", payload }, (resp) => {
      if (resp && resp.ok && resp.data?.status === "ok") {
        if (badge) badge.textContent = `Шаг #${resp.data.turn_idx} сохранён`;
        renderFactCheckInHUD(resp.data);
        refreshStatus();
      } else if (badge) {
        badge.textContent = "Мост оффлайн";
      }
    });
  }

  function refreshStatus() {
    chrome.runtime.sendMessage({ type: "LEARNING_STATUS" }, (resp) => {
      const badge = document.getElementById("moiad-hud-badge");
      const stats = document.getElementById("moiad-hud-stats");
      if (resp && resp.ok && resp.data?.status === "ok") {
        const d = resp.data;
        if (badge && badge.textContent === "Связь...") badge.textContent = "🟢 Live подключён";
        if (stats) {
          stats.textContent = `Сессий: ${d.sessions_count} · Шагов: ${d.turns_count} · Визуализаций: ${d.visuals_count}`;
        }
      } else if (badge) {
        badge.textContent = "🔴 Мост :8000 выкл.";
      }
    });
  }

  const observer = new MutationObserver(() => {
    if (!autoCaptureEnabled) return;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => sendCurrentTurnToBridge(false), 3200);
  });

  function init() {
    createFloatingHUD();
    observer.observe(document.body, { childList: true, subtree: true });
    setInterval(refreshStatus, 20000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
