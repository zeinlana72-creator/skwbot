import os
from pathlib import Path
from dotenv import load_dotenv

# Ambil path folder project saat ini
BASE_DIR = Path(__file__).resolve().parent

# Muat file .env dari folder project
load_dotenv(BASE_DIR / ".env")

# Konfigurasi Token dan Kunci API
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
SHEET_NAME = os.getenv("SHEET_NAME", "KAWAL MPW")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://script.google.com/macros/s/AKfycbye0gjnziy6_inPbigr-DfYKqdj0hYv_Msi2eAxoSkntHHskvp9__9IH51UZkoso0jY0w/exec")

# Path untuk file credentials Google Service Account
# Jika path bukan path absolut, jadikan relatif terhadap folder project
credentials_raw = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
if os.path.isabs(credentials_raw):
    GOOGLE_CREDENTIALS_FILE = credentials_raw
else:
    GOOGLE_CREDENTIALS_FILE = str(BASE_DIR / credentials_raw)

# Dukungan deployment cloud (Railway/Render/Heroku):
# Jika konten credentials diberikan lewat environment variable GOOGLE_CREDENTIALS_JSON,
# buatkan file credentials.json otomatis di direktori kerja jika belum ada di disk.
google_creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
if google_creds_json and not os.path.exists(GOOGLE_CREDENTIALS_FILE):
    try:
        with open(GOOGLE_CREDENTIALS_FILE, "w", encoding="utf-8") as _f:
            _f.write(google_creds_json)
    except Exception as _e:
        print(f"PERINGATAN: Gagal menulis file credentials dari GOOGLE_CREDENTIALS_JSON: {_e}")



# Daftar ID Telegram yang diizinkan untuk menggunakan bot (Korlap/Ops)
# Format di .env: ALLOWED_TELEGRAM_IDS=123456,789012
allowed_ids_raw = os.getenv("ALLOWED_TELEGRAM_IDS", "")
ALLOWED_TELEGRAM_IDS = []
if allowed_ids_raw:
    try:
        ALLOWED_TELEGRAM_IDS = [int(x.strip()) for x in allowed_ids_raw.split(",") if x.strip()]
    except ValueError:
        print("PERINGATAN: Format ALLOWED_TELEGRAM_IDS di file .env tidak valid. Harap gunakan angka dipisahkan koma.")

# Kategori Status Pekerjaan (Work Order)
STATUS_CATEGORIES = [
    "Pending",       # Baru masuk / belum dikerjakan
    "Dalam Proses",  # Sedang dikerjakan oleh teknisi
    "Selesai",       # Pekerjaan selesai fisik, berkas menyusul
    "Tertagih"       # Selesai dan berkas penagihan sudah lengkap & diajukan
]

# Daftar Teknisi Lapangan (Default)
# Catatan: Bot juga dapat membaca daftar teknisi secara dinamis dari Google Sheets jika diperlukan
DEFAULT_TECHNICIANS = [
    "Ahmad",
    "Budi",
    "Candra",
    "Dedi",
    "Eko",
    "Fahmi"
]

def validate_config():
    """Memvalidasi apakah konfigurasi penting sudah terisi."""
    errors = []
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN belum diisi di file .env")
    if not GEMINI_API_KEY:
        errors.append("GEMINI_API_KEY belum diisi di file .env")
    if not GOOGLE_SHEET_ID:
        errors.append("GOOGLE_SHEET_ID belum diisi di file .env")
    if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        errors.append(f"Berkas Google Credentials tidak ditemukan di: {GOOGLE_CREDENTIALS_FILE}")
    
    return len(errors) == 0, errors
