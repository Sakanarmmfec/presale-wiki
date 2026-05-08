import os
import json
import io
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]
TOKEN_FILE = "token.json"
CLIENT_SECRET_FILE = "client_secret.json"

def get_oauth_flow(redirect_uri):
    secret_json = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
    if secret_json:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(secret_json)
            tmp_path = f.name
        flow = Flow.from_client_secrets_file(tmp_path, scopes=SCOPES, redirect_uri=redirect_uri)
        os.unlink(tmp_path)
        return flow
    return Flow.from_client_secrets_file(CLIENT_SECRET_FILE, scopes=SCOPES, redirect_uri=redirect_uri)

def get_service():
    # try env var first (production)
    token_json = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON")
    if token_json:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        return build("drive", "v3", credentials=creds)
    # fallback to file (local)
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError("Not authorized. Please visit /oauth/login first.")
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("drive", "v3", credentials=creds)

def get_or_create_folder(service, name, parent_id):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
    results = service.files().list(q=q, fields="files(id)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]

def ensure_vault_structure(root_folder_id):
    service = get_service()
    folders = {}
    for name in ["inbox", "raw", "wiki", "_meta", "archive"]:
        folders[name] = get_or_create_folder(service, name, root_folder_id)
    # ensure index.md and log.md exist
    for fname, content in [("index.md", "# Index\n\n"), ("log.md", "# Log\n\n"), ("SCHEMA.md", SCHEMA_CONTENT)]:
        if not read_file(folders["_meta"], fname):
            write_file(service, folders["_meta"], fname, content)
    return folders

def list_files(folder_id, mime_filter=None):
    service = get_service()
    q = f"'{folder_id}' in parents and trashed=false"
    if mime_filter:
        q += f" and mimeType='{mime_filter}'"
    results = service.files().list(q=q, fields="files(id,name,createdTime)").execute()
    return results.get("files", [])

def read_file_by_id(file_id):
    service = get_service()
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue().decode("utf-8", errors="replace")

def read_file(folder_id, filename):
    service = get_service()
    q = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    results = service.files().list(q=q, fields="files(id)").execute()
    files = results.get("files", [])
    if not files:
        return None
    return read_file_by_id(files[0]["id"])

def write_file(service_or_none, folder_id, filename, content):
    service = service_or_none or get_service()
    q = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    results = service.files().list(q=q, fields="files(id)").execute()
    files = results.get("files", [])
    buf = io.BytesIO(content.encode("utf-8"))
    media = MediaIoBaseUpload(buf, mimetype="text/plain")
    if files:
        service.files().update(fileId=files[0]["id"], media_body=media).execute()
    else:
        meta = {"name": filename, "parents": [folder_id]}
        service.files().create(body=meta, media_body=media, fields="id").execute()

def move_to_archive(service, file_id, archive_folder_id):
    file = service.files().get(fileId=file_id, fields="parents").execute()
    prev_parents = ",".join(file.get("parents", []))
    service.files().update(
        fileId=file_id,
        addParents=archive_folder_id,
        removeParents=prev_parents,
        fields="id, parents",
    ).execute()

def upload_to_inbox(folder_id, filename, content):
    service = get_service()
    inbox_id = get_or_create_folder(service, "inbox", folder_id)
    write_file(service, inbox_id, filename, content)
    return inbox_id

SCHEMA_CONTENT = """# Presale Wiki Schema

## Purpose
This wiki is a **persistent, compounding knowledge base** for a presale team.
Each wiki page is a synthesized, LLM-maintained artifact — not a raw note dump.
The goal is that when someone asks a question, the LLM can read wiki pages and answer immediately without going back to raw sources.

## Folder structure
- raw/       — original notes converted to markdown with YAML frontmatter (immutable)
- wiki/      — LLM-compiled knowledge pages (you own and maintain these)
- archive/   — notes already ingested
- _meta/     — index.md, log.md, SCHEMA.md

## Wiki page types
Create pages in these categories as needed:
- **Customer pages** (`customer-<name>.md`) — what we know about this customer: needs, pain points, key contacts, demo history, objections, budget signals
- **Topic/Concept pages** (`topic-<name>.md`) — synthesized knowledge on a subject (e.g. pricing strategy, integration patterns)
- **Comparison pages** (`compare-<a>-vs-<b>.md`) — side-by-side analysis
- **Person pages** (`person-<name>.md`) — key stakeholders, their role, preferences, history

## Wiki page format
Every page must have YAML frontmatter:
```
---
title: <title>
tags: [<tag1>, <tag2>]
authors: [<who contributed notes>]
updated: <YYYY-MM-DD>
sources: [<raw filenames>]
---
```
Then structured markdown with:
- Clear `##` sections
- `[[wikilinks]]` to related pages
- A `## Sources` section at the bottom listing raw note filenames

## index.md format
Organized by category: Customers, Topics, People, Comparisons
Each entry: `- [[page-name]] — one-line description (authors: X, Y | updated: YYYY-MM-DD)`

## log.md format
`## [YYYY-MM-DD] ingest | filename | author`

## Ingest instructions
When ingesting a new note:
1. Extract: customers mentioned, topics discussed, insights, objections, pricing signals, action items
2. For each entity/topic found — create or UPDATE the relevant wiki page with synthesized knowledge
3. A single note may touch 3-8 wiki pages
4. Cross-link pages with [[wikilinks]]
5. Update index.md
6. The wiki page must be useful standalone — someone reading it should understand the full picture without reading the raw note
"""
