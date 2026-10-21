# Gemini Cookie Sync Setup (Gemini Local UI — Port 8000)

Short guide for zero-drop real-time session synchronization between Chrome and `gemini_local_ui` (`http://localhost:8000`).

## What this extension syncs

The extension watches `chrome.cookies.onChanged` for rotating Google tickets (`__Secure-1PSIDTS`, `__Secure-1PSIDRTS`, `SIDCC`, etc.) and reads live tokens directly from `window.WIZ_global_data` in any open Gemini tab:

- Google session cookies (`__Secure-1PSID`, `__Secure-1PSIDTS`, `SAPISID`, etc.)
- `SNlM0e` (`xsrf_token`)
- `cfb2h` (`gemini_bl`)
- `auth_user`

Whenever Chrome rotates `__Secure-1PSIDTS` or every 15 minutes via `chrome.alarms`, the extension pushes the fresh session to `POST http://localhost:8000/admin/session`.

## 1-Click Install in Chrome / Edge / Brave

1. Open `chrome://extensions`
2. Enable **Developer mode** (toggle in top-right corner)
3. Click **Load unpacked**
4. Select the folder:
   `<REPO_ROOT>/extensions/gemini-cookie-sync-extension`
5. Ensure [https://gemini.google.com/app](https://gemini.google.com/app) is open and signed in
6. Click the **Gemini Cookie Sync** extension icon in your browser toolbar:
   - **Gateway URL** is pre-configured to `http://localhost:8000`
   - **Admin key (`admin_key`)** stays empty for local use
   - **Keep session fresh in gateway** is checked by default
7. Click **Send session to gateway** once to activate automatic background sync.

## Verify Status

Check the gateway status endpoint at any time:

```bash
curl -s http://localhost:8000/admin/session
```
