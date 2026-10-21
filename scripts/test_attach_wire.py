import urllib.request
import json

# 1. upload small test file
boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
body = (
    f'--{boundary}\r\n'
    f'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
    f'Content-Type: text/plain\r\n\r\n'
    f'Hello attachment test content line 1\r\n'
    f'--{boundary}--\r\n'
).encode('utf-8')

req = urllib.request.Request(
    'http://127.0.0.1:8000/api/chat/upload',
    data=body,
    headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'X-Gemini-Account': 'acc_21aac20f'
    }
)
with urllib.request.urlopen(req, timeout=15) as resp:
    upload_res = json.loads(resp.read().decode('utf-8'))
print('Upload result:', upload_res)

# 2. call chat/generate with attachment
chat_payload = {
    'prompt': 'Read the attached test.txt file and output its contents inside a code block.',
    'attachments': [upload_res],
    'model': 'gemini-4-argon'
}
req2 = urllib.request.Request(
    'http://127.0.0.1:8000/api/chat/generate',
    data=json.dumps(chat_payload).encode('utf-8'),
    headers={
        'Content-Type': 'application/json',
        'X-Gemini-Account': 'acc_21aac20f'
    }
)
with urllib.request.urlopen(req2, timeout=90) as resp:
    for line in resp:
        line_str = line.decode('utf-8').strip()
        if line_str.startswith('data:'):
            ev = json.loads(line_str[5:].strip())
            ev_type = ev.get('type')
            delta = ev.get('delta', '')
            if ev_type == 'thought':
                print(f"[THOUGHT] {delta[:60]}...")
            elif ev_type == 'text':
                print(f"[TEXT] {delta}")
            elif ev_type == 'done':
                print(f"[DONE] metadata: {ev.get('metadata')}")
            else:
                print(f"[{ev_type.upper()}] {ev}")
