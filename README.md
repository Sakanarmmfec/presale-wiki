# Presale Wiki

ระบบ wiki อัตโนมัติสำหรับทีม presale — อัพโหลด note → LLM สร้าง knowledge graph → ถามตอบได้เลย

## Stack
- **Backend**: FastAPI (Python)
- **Storage**: Google Drive
- **LLM**: MFEC LiteLLM (gpt-4.1-mini)
- **Deploy**: Render

## Setup

### 1. Google Service Account
1. ไปที่ https://console.cloud.google.com → สร้าง project `presale-wiki`
2. เปิด Google Drive API (APIs & Services → Enable APIs)
3. สร้าง Service Account (IAM & Admin → Service Accounts → Create)
4. สร้าง JSON key → download ได้ไฟล์ `sa.json`
5. สร้าง folder ใน Google Drive ชื่อ `presale-wiki-vault`
6. copy Folder ID จาก URL: `https://drive.google.com/drive/folders/<<FOLDER_ID>>`
7. Share folder ให้ `client_email` ใน `sa.json` เป็น Editor

### 2. Deploy บน Render
1. push code ขึ้น GitHub
2. ไปที่ https://render.com → New Web Service → เลือก repo
3. ตั้ง Environment Variables:
   - `GDRIVE_FOLDER_ID` = Folder ID จากขั้นตอนที่ 6
   - `GOOGLE_SERVICE_ACCOUNT_JSON` = เนื้อหาทั้งหมดของไฟล์ `sa.json` (วางเป็น string)
   - `LITELLM_API_KEY` = `sk-dowkVeNl7tpgqa-VfxCCUA`
4. Deploy

### 3. ใช้งาน
- `/` — หน้า UI: อัพโหลด note, trigger ingest, ถามตอบ
- `POST /upload` — อัพโหลดไฟล์ + author
- `POST /ingest` — ประมวลผลไฟล์ใน inbox
- `POST /chat` — ถามคำถาม
- `GET /health` — ตรวจสอบ connection

## ชื่อไฟล์แนะนำ
```
YYYY-MM-DD_ชื่อ_หัวข้อ.txt
เช่น: 2025-05-06_สมชาย_demo-scb-crm.txt
```
ระบบจะ parse author จากชื่อไฟล์อัตโนมัติ

## Vault structure (สร้างอัตโนมัติ)
```
presale-wiki-vault/
├── inbox/      ← drop ไฟล์ที่นี่ หรือ upload ผ่าน UI
├── raw/        ← note ต้นฉบับ + frontmatter
├── wiki/       ← LLM-generated knowledge pages
└── _meta/
    ├── index.md    ← catalog ทุก wiki page
    ├── log.md      ← history การ ingest
    └── SCHEMA.md   ← instruction ให้ LLM
```
