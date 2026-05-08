import os
from dotenv import load_dotenv
load_dotenv()

from gdrive import get_service, list_files, ensure_vault_structure

ROOT_FOLDER_ID = os.environ["GDRIVE_FOLDER_ID"]

print("Testing Google Drive connection...")
try:
    service = get_service()
    print("✓ Authenticated successfully")

    result = service.files().get(fileId=ROOT_FOLDER_ID, fields="id,name").execute()
    print(f"✓ Found root folder: {result['name']} ({result['id']})")

    print("\nEnsuring vault structure...")
    folders = ensure_vault_structure(ROOT_FOLDER_ID)
    for name, fid in folders.items():
        print(f"  ✓ {name}/ → {fid}")

    print("\nAll good! Google Drive is connected.")
except Exception as e:
    print(f"✗ Error: {e}")
