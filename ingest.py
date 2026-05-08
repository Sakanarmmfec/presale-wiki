import os
import re
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI
from gdrive import (
    get_service, get_or_create_folder, list_files, read_file,
    read_file_by_id, write_file, move_to_archive
)

client = OpenAI(
    base_url="https://gpt.mfec.co.th/litellm",
    api_key=os.environ.get("LITELLM_API_KEY", "sk-dowkVeNl7tpgqa-VfxCCUA"),
)
MODEL = "gpt-4.1-mini"

ROOT_FOLDER_ID = os.environ["GDRIVE_FOLDER_ID"]

def get_folders():
    service = get_service()
    folders = {}
    for name in ["inbox", "raw", "wiki", "_meta", "archive"]:
        folders[name] = get_or_create_folder(service, name, ROOT_FOLDER_ID)
    return folders

def parse_author_from_filename(filename):
    # format: YYYY-MM-DD_ชื่อ_หัวข้อ.txt or just ชื่อ_หัวข้อ.txt
    parts = filename.replace(".txt", "").replace(".md", "").split("_")
    if len(parts) >= 3 and re.match(r"\d{4}-\d{2}-\d{2}", parts[0]):
        return parts[1]
    elif len(parts) >= 2:
        return parts[0]
    return "unknown"

def build_frontmatter(author, filename, date_str):
    return f"""---
author: {author}
source_file: {filename}
date: {date_str}
tags: []
---

"""

def ingest_file(file_id, filename, author=None, progress=None):
    def emit(msg):
        if progress:
            progress(msg)

    folders = get_folders()
    service = get_service()

    emit(f"📄 อ่านไฟล์: {filename}")
    content = read_file_by_id(file_id)
    if not author:
        author = parse_author_from_filename(filename)

    date_str = datetime.now().strftime("%Y-%m-%d")
    frontmatter = build_frontmatter(author, filename, date_str)
    raw_content = frontmatter + content

    emit(f"💾 บันทึกลง raw/ ...")
    raw_filename = f"{date_str}_{author}_{filename}" if not filename.startswith(date_str) else filename
    raw_filename = re.sub(r'\.(txt|md)$', '', raw_filename) + ".md"
    write_file(service, folders["raw"], raw_filename, raw_content)

    emit(f"📚 ให้ LLM เลือก wiki pages ที่ relate ...")
    index_content = read_file(folders["_meta"], "index.md") or "# Index\n\n"
    schema_content = read_file(folders["_meta"], "SCHEMA.md") or ""
    wiki_context = _load_relevant_wiki(folders["wiki"], index_content, content)
    related_pages = [line.split("--- ")[1].split(" ---")[0] for line in wiki_context.split("\n") if line.startswith("--- ")]
    if related_pages:
        emit(f"🔗 พบ wiki pages ที่เกี่ยวข้อง: {', '.join(related_pages)}")
    else:
        emit(f"🆕 ไม่มี wiki pages เดิมที่เกี่ยวข้อง จะสร้างใหม่")

    emit(f"🤖 ส่งให้ LLM วิเคราะห์และสร้าง wiki pages ...")
    system_prompt = f"""You are a wiki maintainer for a presale team knowledge base.
{schema_content}

Current index.md:
{index_content}

Existing relevant wiki pages:
{wiki_context}
"""
    user_prompt = f"""New note to ingest:
Author: {author}
Filename: {raw_filename}
Date: {date_str}

Content:
{content}

---
Instructions:
1. Extract all entities: customers, people, topics, products, objections, pricing signals
2. For each entity/topic — create or UPDATE the relevant wiki page with SYNTHESIZED knowledge
   - Customer pages: needs, pain points, contacts, demo history, objections, budget signals
   - Topic pages: synthesized insights, patterns, recommendations
   - Cross-link pages with [[wikilinks]]
   - Each page must be useful standalone — full picture without reading the raw note
3. A single note should touch 3-8 wiki pages
4. Update index.md
5. Return structured blocks:

For each wiki page:
===WIKI:filename.md===
[full markdown content with YAML frontmatter]
===END===

===INDEX===
[full updated index.md]
===END===

===LOG_ENTRY===
## [{date_str}] ingest | {raw_filename} | {author}
===END===
"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=4000,
    )
    result = response.choices[0].message.content

    wiki_pages = re.findall(r"===WIKI:(.+?)===\n(.*?)===END===", result, re.DOTALL)
    saved_pages = []
    for i, (page_name, page_content) in enumerate(wiki_pages, 1):
        page_name = page_name.strip()
        emit(f"📝 บันทึก wiki page ({i}/{len(wiki_pages)}): {page_name}")
        write_file(service, folders["wiki"], page_name, page_content.strip())
        saved_pages.append(page_name)

    emit(f"🗂️ อัพเดต index.md ...")
    index_match = re.search(r"===INDEX===\n(.*?)===END===", result, re.DOTALL)
    if index_match:
        write_file(service, folders["_meta"], "index.md", index_match.group(1).strip())

    log_match = re.search(r"===LOG_ENTRY===\n(.*?)===END===", result, re.DOTALL)
    if log_match:
        existing_log = read_file(folders["_meta"], "log.md") or "# Log\n\n"
        new_log = existing_log + "\n" + log_match.group(1).strip() + "\n"
        write_file(service, folders["_meta"], "log.md", new_log)

    emit(f"📦 ย้ายไฟล์ไปยัง archive ...")
    move_to_archive(service, file_id, folders["archive"])

    return {
        "author": author,
        "raw_file": raw_filename,
        "wiki_pages_updated": saved_pages,
    }

def _load_relevant_wiki(wiki_folder_id, index_content, note_content):
    files = list_files(wiki_folder_id)
    if not files:
        return "(no wiki pages yet)"

    # ask LLM which pages are relevant
    selector = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": f"""Given this index of wiki pages:
{index_content}

And this new note:
{note_content[:2000]}

Which wiki pages are relevant and should be updated? Reply with ONLY a JSON array of filenames, max 8.
Example: ["customer-scb.md", "topic-crm.md"]
If none, return []."""}],
        max_tokens=200,
    )
    raw = selector.choices[0].message.content.strip()
    filenames = re.findall(r'"([^"]+\.md)"', raw)

    if not filenames:
        return "(no related wiki pages)"

    file_map = {f["name"]: f["id"] for f in files}
    result = ""
    for fname in filenames:
        if fname in file_map:
            content = read_file_by_id(file_map[fname])
            result += f"\n--- {fname} ---\n{content}\n"
    return result or "(no related wiki pages)"

def rebuild_index():
    """Rebuild index.md from actual wiki files on Drive"""
    folders = get_folders()
    service = get_service()
    files = list_files(folders["wiki"])
    if not files:
        return "(no wiki pages)"

    pages_content = ""
    for f in files:
        content = read_file_by_id(f["id"])
        pages_content += f"\n--- {f['name']} ---\n{content[:500]}\n"

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": f"""Given these wiki pages, generate a complete index.md.

{pages_content}

Format:
# Index

## Customers
- [[page-name]] — one-line description (authors: X)

## People
- ...

## Topics
- ...

## Comparisons
- ...

Return ONLY the index.md content."""}],
        max_tokens=1000,
    )
    new_index = response.choices[0].message.content.strip()
    write_file(service, folders["_meta"], "index.md", new_index)
    return new_index

def process_inbox(progress=None):
    """Process all files in inbox — call this from scheduler or manually"""
    def emit(msg):
        if progress:
            progress(msg)

    folders = get_folders()
    files = list_files(folders["inbox"])
    total = len(files)
    results = []

    if total == 0:
        emit("📭 ไม่มีไฟล์ใน inbox")
        return results

    emit(f"📬 พบ {total} ไฟล์ใน inbox")
    for i, f in enumerate(files, 1):
        emit(f"\n⏳ กำลังประมวลผลไฟล์ {i}/{total}: {f['name']}")
        try:
            result = ingest_file(f["id"], f["name"], progress=progress)
            results.append({"file": f["name"], "status": "ok", **result})
            emit(f"✅ เสร็จแล้ว ({i}/{total}) — wiki pages: {', '.join(result['wiki_pages_updated'])}")
        except Exception as e:
            results.append({"file": f["name"], "status": "error", "error": str(e)})
            emit(f"❌ เกิดข้อผิดพลาด ({f['name']}): {str(e)}")

    emit(f"\n🔄 Rebuild index.md จาก wiki files ทั้งหมด ...")
    rebuild_index()
    emit(f"\n🎉 ประมวลผลครบทั้งหมด {total} ไฟล์")
    return results
