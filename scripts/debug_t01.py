import asyncio
import logging
import json
from pathlib import Path
from backend.app.bridge.client import GeminiWebClient
from backend.app.storage.vault import CredentialVault
from backend.app.bridge.uploader import GeminiUploader

from backend.app.bridge.session import SessionManager

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("debug_t01")

async def main():
    vault = CredentialVault()
    sm = SessionManager(vault=vault)
    await sm.initialize_all()
    ctx = sm.get_session_context("acc_21aac20f")
    print(f"Context initialized: status={ctx.status}, tier={ctx.account_tier}, build_label={ctx.build_label}")

    client = GeminiWebClient(session_context=ctx)
    uploader = GeminiUploader(session_context=ctx)

    # 1. upload context.zip
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write("backend/app/bridge/session.py", arcname="backend/app/bridge/session.py")
        zf.write("backend/app/bridge/client.py", arcname="backend/app/bridge/client.py")
    zip_bytes = buf.getvalue()

    print(f"Uploading context.zip ({len(zip_bytes)} bytes)...")
    att = await uploader.upload_file(zip_bytes, "context.zip", "application/zip")
    print(f"Uploaded attachment: {att}")

    prompt = (
        "[Task context: attached archive context.zip with the full project code and structure. "
        "Inspect and use the files from the archive to complete the task.]\n\n"
        "You are acting as a lead system architect. Write a concise overview of cookie management in session.py."
    )

    print("Starting generate_stream...")
    thought_len = 0
    text_len = 0
    chunk_count = 0
    async for chunk in client.generate_stream(
        prompt=prompt,
        model="gemini-4-argon@thinking=2@mode=task",
        attachments=[{"uri": att, "filename": "context.zip"}],
        feature_mode=40,
    ):
        chunk_count += 1
        if chunk.type == "thought":
            thought_len += len(chunk.delta)
        elif chunk.type == "text":
            text_len += len(chunk.delta)
            print(f"[TEXT CHUNK {chunk_count}] {chunk.delta[:80]}")
        elif chunk.type == "done":
            print(f"[DONE CHUNK] meta: {chunk.metadata}")
        else:
            print(f"[{chunk.type.upper()}]")

    print(f"\nFinal: chunks={chunk_count}, thoughts={thought_len} chars, text={text_len} chars")

asyncio.run(main())
