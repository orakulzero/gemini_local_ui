import urllib.request
import json
import zipfile
import io
import sys

# Pack 2 files into zip
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("test.py", "print('hello from zip')\n")
zip_bytes = buf.getvalue()

# 1. upload
boundary = "----WebKitFormBoundaryDebug123"
body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="context.zip"\r\n'
    f"Content-Type: application/zip\r\n\r\n"
).encode("utf-8") + zip_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

req = urllib.request.Request(
    "http://127.0.0.1:8000/api/chat/upload",
    data=body,
    headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Gemini-Account": "acc_21aac20f"
    }
)
with urllib.request.urlopen(req, timeout=15) as resp:
    upload_res = json.loads(resp.read().decode("utf-8"))
print("[1] Uploaded:", upload_res)

# 2. call chat/generate with exactly the same payload as gemini_task.py
prompt = "[Task context: attached archive context.zip with the full project code]\n\nWrite a file hello.py with a hello() function. Format the code block with its file path."
payload = {
    "prompt": prompt,
    "model": "gemini-4-argon@thinking=2@mode=task",
    "feature_mode": "task",
    "incognito": False,
    "account_id": "acc_21aac20f",
    "attachments": [upload_res]
}

req2 = urllib.request.Request(
    "http://127.0.0.1:8000/api/chat/generate",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "X-Gemini-Account": "acc_21aac20f"
    }
)

print("[2] Streaming from /api/chat/generate...")
thought_chars = 0
text_chars = 0
with urllib.request.urlopen(req2, timeout=180) as resp:
    for line in resp:
        line_str = line.decode("utf-8", errors="replace").strip()
        if not line_str.startswith("data:"):
            continue
        try:
            ev = json.loads(line_str[5:].strip())
        except Exception:
            continue
        ev_type = ev.get("type")
        if ev_type == "thought":
            thought_chars += len(ev.get("delta", ""))
            sys.stdout.write(".")
            sys.stdout.flush()
        elif ev_type == "text":
            delta = ev.get("delta", "")
            text_chars += len(delta)
            print(f"\n[TEXT DELTA]: {delta[:100]}")
        elif ev_type == "error":
            print(f"\n[ERROR EVENT]: {ev}")
        elif ev_type == "done":
            print(f"\n[DONE EVENT]: {ev}")

print(f"\nSummary: thoughts={thought_chars} chars, text={text_chars} chars")
