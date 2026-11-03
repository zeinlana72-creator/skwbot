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
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1PrM2fyGxEWFno6WDFZVigO771yELsU3zRyrPfzO-8oQ")
SHEET_NAME = os.getenv("SHEET_NAME", "DATANEW")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://script.google.com/macros/s/AKfycbwsleRWZLr24S6AT-cSG0CstUeSl8xIA3ssnSsZ-Hf3c50ep_ko07G-qxMrs35yqUoAvQ/exec")
BACKUP_SESSION_SHEET_ID = os.getenv("BACKUP_SESSION_SHEET_ID", "1GafB5UK6J5sLIIUe57qCkjJFQ9BEDNV_T7M7gxKFitw")

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

# ID Grup Telegram untuk pengiriman laporan otomatis
target_group_raw = os.getenv("TARGET_GROUP_ID", "-1001342185601").strip()
try:
    TARGET_GROUP_ID = int(target_group_raw)
except ValueError:
    TARGET_GROUP_ID = -1001342185601

# ID Grup Telegram tujuan pengiriman hasil perintah laporan (/absen_assurance, /report, /dashboard)
report_group_raw = os.getenv("REPORT_GROUP_ID", "-1003737866244").strip()
try:
    REPORT_GROUP_ID = int(report_group_raw)
except ValueError:
    REPORT_GROUP_ID = -1003737866244

# Konfigurasi MyBranch (takalbagbar.id/mybranch) untuk Absen Assurance
MYBRANCH_URL = os.getenv("MYBRANCH_URL", "https://takalbagbar.id/mybranch").rstrip("/")
MYBRANCH_NIK = os.getenv("MYBRANCH_NIK", "20260004")
MYBRANCH_PASSWORD = os.getenv("MYBRANCH_PASSWORD", "Dandi998")
MYBRANCH_OTP_CHAT_ID = int(os.getenv("MYBRANCH_OTP_CHAT_ID", "794046951"))
MYBRANCH_COOKIES_FILE = str(BASE_DIR / "mybranch_cookies.txt")
MYBRANCH_STATE_FILE = str(BASE_DIR / "mybranch_state.json")

# Konfigurasi Bot Kedua (Provisioning / Assign WO PSB - @prov_snw_bot)
PROV_BOT_TOKEN = os.getenv("PROV_BOT_TOKEN", "9132996761:AAF8nf4qaADaqY4Qo9s-0SHzxVCy3JcEnWE")
PROV_MYBRANCH_NIK = os.getenv("PROV_MYBRANCH_NIK", "16953908")
PROV_MYBRANCH_PASSWORD = os.getenv("PROV_MYBRANCH_PASSWORD", "@Condong2026")
PROV_MYBRANCH_COOKIES_FILE = str(BASE_DIR / "mybranch_prov_cookies.txt")
PROV_MYBRANCH_STATE_FILE = str(BASE_DIR / "mybranch_prov_state.json")
prov_group_raw = os.getenv("PROV_ALLOWED_GROUP_ID", "-1004346054239").strip()
try:
    PROV_ALLOWED_GROUP_ID = int(prov_group_raw)
except ValueError:
    PROV_ALLOWED_GROUP_ID = -1004346054239

# Konfigurasi Bot Ketiga (Assurance - @ass_snw_bot)
ASS_BOT_TOKEN = os.getenv("ASS_BOT_TOKEN", "9058640514:AAFvsIYC2byOlT-HwH0AS1H4pPtJWLGEsxE")
ASS_MYBRANCH_COOKIES_FILE = str(BASE_DIR / "mybranch_ass_cookies.txt")
ASS_MYBRANCH_STATE_FILE = str(BASE_DIR / "mybranch_ass_state.json")

# Konfigurasi Insera SSO (Close Tiket - @ass_snw_bot)
INSERA_URL = os.getenv("INSERA_URL", "https://insera-sso.telkom.co.id/jw/web/login")
INSERA_USERNAME = os.getenv("INSERA_USERNAME", "16023172")
INSERA_PASSWORD = os.getenv("INSERA_PASSWORD", "Rahasia11#")
INSERA_STATE_FILE = str(BASE_DIR / "insera_state.json")

# Jika MYBRANCH_STATE_JSON diset di environment variable (Railway), tulis ke mybranch_state.json saat startup
_mybranch_state_env = os.getenv("MYBRANCH_STATE_JSON", "").strip()
if _mybranch_state_env and not os.path.exists(MYBRANCH_STATE_FILE):
    try:
        with open(MYBRANCH_STATE_FILE, "w", encoding="utf-8") as _mf:
            _mf.write(_mybranch_state_env)
    except Exception as _me:
        print(f"PERINGATAN: Gagal menulis mybranch_state.json dari MYBRANCH_STATE_JSON: {_me}")


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
