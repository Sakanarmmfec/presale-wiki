import os
import re
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI
from gdrive import get_service, get_or_create_folder, list_files, read_file, read_file_by_id

client = OpenAI(
    base_url="https://gpt.mfec.co.th/litellm",
    api_key=os.environ.get("LITELLM_API_KEY", "sk-dowkVeNl7tpgqa-VfxCCUA"),
)
MODEL = "gpt-4.1-mini"
ROOT_FOLDER_ID = os.environ["GDRIVE_FOLDER_ID"]

def get_folders():
    service = get_service()
    folders = {}
    for name in ["inbox", "raw", "wiki", "_meta"]:
        folders[name] = get_or_create_folder(service, name, ROOT_FOLDER_ID)
    return folders

def answer_query(question: str, author_filter: str = None) -> dict:
    folders = get_folders()

    # 1. load index.md
    index_content = read_file(folders["_meta"], "index.md") or "# Index\n\n(empty)"

    # 2. ask LLM which pages to read
    selector_prompt = f"""You are a wiki navigator. Given a user question, identify which wiki pages are most relevant.

Current index.md:
{index_content}

{"Filter: Only consider notes from author: " + author_filter if author_filter else ""}

User question: {question}

Reply with ONLY a JSON array of filenames to read, e.g.:
["crm-scb.md", "pricing-strategy.md"]

Max 5 files. If nothing relevant, return [].
"""
    selector_response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": selector_prompt}],
        max_tokens=200,
    )
    raw = selector_response.choices[0].message.content.strip()

    # parse filenames
    try:
        filenames = re.findall(r'"([^"]+\.md)"', raw)
    except Exception:
        filenames = []

    # 3. load selected wiki pages
    wiki_context = ""
    sources_used = []
    for fname in filenames:
        content = read_file(folders["wiki"], fname)
        if content:
            wiki_context += f"\n\n--- {fname} ---\n{content}"
            sources_used.append(fname)

    if not wiki_context:
        wiki_context = "(ไม่พบ wiki pages ที่เกี่ยวข้อง)"

    # 4. answer with full context
    answer_system = """You are a helpful presale knowledge assistant.
Answer questions based strictly on the wiki content provided.
Always cite which wiki page and which author the information comes from.
Answer in Thai if the question is in Thai.
"""
    answer_user = f"""Wiki content:
{wiki_context}

Question: {question}
"""
    answer_response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": answer_system},
            {"role": "user", "content": answer_user},
        ],
        max_tokens=1500,
    )
    answer = answer_response.choices[0].message.content

    return {
        "answer": answer,
        "sources": sources_used,
        "pages_considered": filenames,
    }
