import os
from dotenv import load_dotenv
load_dotenv()
if os.environ.get("RENDER") != "true":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
import asyncio
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from typing import Optional
from pydantic import BaseModel
from gdrive import ensure_vault_structure, upload_to_inbox, get_oauth_flow, TOKEN_FILE
from ingest import process_inbox, rebuild_index
from chat import answer_query

app = FastAPI(title="Presale Wiki")

ROOT_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")

@app.on_event("startup")
async def startup():
    if ROOT_FOLDER_ID:
        try:
            ensure_vault_structure(ROOT_FOLDER_ID)
        except Exception as e:
            print(f"Warning: could not ensure vault structure: {e}")

# ── OAuth endpoints ─────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

@app.get("/oauth/login")
async def oauth_login():
    flow = get_oauth_flow(f"{BASE_URL}/oauth/callback")
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return RedirectResponse(auth_url)

@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    flow = get_oauth_flow(f"{BASE_URL}/oauth/callback")
    flow.fetch_token(authorization_response=str(request.url))
    creds = flow.credentials
    token_json = creds.to_json()
    with open(TOKEN_FILE, "w") as f:
        f.write(token_json)
    # auto-update Render env var
    render_api_key = os.environ.get("RENDER_API_KEY")
    render_service_id = os.environ.get("RENDER_SERVICE_ID")
    if render_api_key and render_service_id:
        import httpx as _httpx
        _httpx.put(
            f"https://api.render.com/v1/services/{render_service_id}/env-vars",
            headers={"Authorization": f"Bearer {render_api_key}", "Content-Type": "application/json"},
            json=[{"key": "GOOGLE_OAUTH_TOKEN_JSON", "value": token_json}],
        )
    return {"status": "authorized", "message": "Google Drive connected successfully!"}

# ── Upload endpoint ──────────────────────────────────────────────
@app.post("/upload")
async def upload_note(
    file: UploadFile = File(...),
    author: str = Form(...),
):
    if not ROOT_FOLDER_ID:
        raise HTTPException(500, "GDRIVE_FOLDER_ID not set")
    content = (await file.read()).decode("utf-8", errors="replace")
    # prepend author to filename so pipeline can parse it
    safe_author = author.strip().replace(" ", "-")
    filename = f"{safe_author}_{file.filename}"
    try:
        inbox_id = upload_to_inbox(ROOT_FOLDER_ID, filename, content)
        return {"status": "uploaded", "filename": filename, "inbox_folder": inbox_id}
    except Exception as e:
        raise HTTPException(500, str(e))

# ── Ingest endpoint ──────────────────────────────────────────────
@app.post("/ingest")
async def trigger_ingest():
    if not ROOT_FOLDER_ID:
        raise HTTPException(500, "GDRIVE_FOLDER_ID not set")
    try:
        results = process_inbox()
        return {"status": "done", "processed": results}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/ingest/stream")
async def ingest_stream():
    if not ROOT_FOLDER_ID:
        raise HTTPException(500, "GDRIVE_FOLDER_ID not set")

    queue = asyncio.Queue()

    def progress(msg):
        queue.put_nowait(msg)

    async def run_ingest():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: process_inbox(progress=progress))
        queue.put_nowait("__DONE__")

    async def event_generator():
        asyncio.create_task(run_ingest())
        while True:
            msg = await queue.get()
            if msg == "__DONE__":
                yield "data: __DONE__\n\n"
                break
            yield f"data: {msg}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ── Chat endpoint ────────────────────────────────────────────────
class QueryRequest(BaseModel):
    question: str
    author_filter: Optional[str] = None

@app.post("/chat")
async def chat(req: QueryRequest):
    if not ROOT_FOLDER_ID:
        raise HTTPException(500, "GDRIVE_FOLDER_ID not set")
    try:
        result = answer_query(req.question, req.author_filter)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))

# ── Rebuild index endpoint ───────────────────────────────────────
@app.post("/rebuild-index")
async def trigger_rebuild_index():
    if not ROOT_FOLDER_ID:
        raise HTTPException(500, "GDRIVE_FOLDER_ID not set")
    try:
        new_index = await asyncio.get_event_loop().run_in_executor(None, rebuild_index)
        return {"status": "done", "index": new_index}
    except Exception as e:
        raise HTTPException(500, str(e))

# ── Health check ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "folder_id": ROOT_FOLDER_ID[:8] + "..." if ROOT_FOLDER_ID else "not set"}

# ── Serve static UI ──────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")
