import asyncio
import base64
import http.cookiejar
import io
import json
import logging
import os
import random
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    TypeHandler,
    ApplicationHandlerStop,
    ContextTypes,
    filters,
)
from playwright.async_api import async_playwright
import config
from mybranch_capture import _solve_captcha_with_gemini, _get_playwright_cookies, USER_AGENT

logger = logging.getLogger("prov_bot")


def _generate_smart_nyeleneh_reply(user_text: str, user_name: str, bot_prev_text: str = "") -> str:
    """Menghasilkan balasan santai, nyeleneh, dan NYAMBUNG secara kontekstual dengan pesan yang di-reply."""
    t = user_text.lower().strip()
    name = user_name or "bang"

    # 1. Coba gunakan Gemini AI jika ada API Key agar jawaban dinamis & sangat nyambung
    if getattr(config, "GEMINI_API_KEY", ""):
        try:
            import google.generativeai as genai
            genai.configure(api_key=config.GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-3.8-flash")
            prompt = (
                "Kamu adalah bot Telegram (@prov_snw_bot) yang santai, kocak, lucu, nyeleneh, tapi ramah dan nyambung dengan obrolan teknisi lapangan Telkom / IndiHome di Singkawang (Kalimantan Barat).\n"
                "- Balas dengan NYAMBUNG dan kontekstual terhadap apa yang diomongin oleh user yang me-reply pesanmu.\n"
                "- Gunakan bahasa gaul / tongkrongan santai ala anak lapangan/teknisi Singkawang (boleh selipin istilah ODP, dropcore, redaman, korlap, BIMA jika pas).\n"
                "- Balasan SANGAT SINGKAT (1 kalimat saja, maksimal 2 kalimat pendek), jangan panjang-panjang.\n"
                "- Jangan gunakan tanda kutip, jangan pakai salam pembuka/penutup formal.\n\n"
                f"Pesan bot sebelumnya: '{bot_prev_text[:120]}'\n"
                f"Pesan user ({name}): '{user_text[:120]}'\n"
                "Balasan singkat bot:"
            )
            resp = model.generate_content(prompt, request_options={"timeout": 3.0})
            if resp and resp.text:
                out = resp.text.strip().replace('"', '').replace("'", "")
                if out and len(out) <= 200:
                    return out
        except Exception as ge:
            logger.debug(f"[ProvBot] Gemini reply skipped ({ge}), fallback to smart matcher.")

    # 2. Smart Contextual Rule Matcher (respons instan, tepat sasaran & nyambung)
    if re.search(r"hapus|delete|ilangin", t):
        return random.choice([
            f"Awas ya kalo gak beneran dihapus {name}, tar kena tegur korlap lu wkwk 🗿",
            f"Beneran ya janji dihapus {name}, jangan NATO (No Action Talk Only) wkwk 😂",
            f"Siap aman {name}, awas aja kalo masih nongol ntar gua laporin korlap 😜",
        ])

    if re.search(r"hmmm?|yela+|hadeh|cape|lelah|males|pusing", t):
        return random.choice([
            f"Napa lu lemes gitu {name}, cape abis narik dropcore kah? 🪜",
            f"Yelaaa mulu lu {name}, mending bantuin temen lu noh lagi nyari ODP 😌",
            f"Baru segitu aja udah hadeh {name}, inget cicilan masih melambai-lambai 🏃‍♂️💨",
        ])

    if re.search(r"\baman\b|\bsantai\b|\btenang\b|\bkalem\b", t):
        return random.choice([
            f"Beneran aman gak nih {name}? Jangan sampe ntar pas dicek BIMA zonk ya 🤣",
            f"Santai di awal, panik pas rekap absen lu ntar {name} wkwk 🗿",
            f"Aman aman bae lu {name}, kerjaan udah beres beneran belum tuh? 😜",
        ])

    if re.search(r"\bsiap\b|\boke+\b|\bok\b|\bsip\b|\byoi+\b", t):
        return random.choice([
            f"Siap siap mulu lu {name}, bukti fisik closed-nya mana coba? 📸",
            f"Okee siap {name}! Tapi jangan lupa action-nya, jangan omdo wkwk 😜",
            f"Yoi mantap {name}, gaspol terus pantang kendor sebelum target tembus! 🚀",
        ])

    if re.search(r"wkwk|haha|hehe|xixi|ngakak", t):
        return random.choice([
            f"Malah ketawa lu {name}, WO PSB udah kelar semua emang? 👀",
            f"Tertawa di atas kabel dropcore yang kusut ya {name} wkwk 😜",
            f"Seneng amat ketawanya {name}, abis dapet tip dari pelanggan ya? 🤑",
        ])

    if re.search(r"makasih|terima kasih|tengkyu|thanks", t):
        return random.choice([
            f"Sama-sama {name}, traktir kopi hitam dulu lah minimal di warkop ☕",
            f"Yoi santai aja {name}, jangan lupa bintang 5-nya ya wkwk ⭐",
        ])

    if re.search(r"makan|ngopi|kopi|lap[ae]r|sarapan", t):
        return random.choice([
            f"Titip gorengan sama kopi susu satu ya {name}, jangan lupa bungkusin ☕🤤",
            f"Ngopi mulu {name}, kerjaan udah beres belum tuh? Jangan ngilang pas jam kerja gih 🚶‍♂️",
        ])

    if re.search(r"^(p+|woi+|woy+|halo+|hai+|oy+|oi+)\b", t):
        return random.choice([
            f"Nge-P nge-P bae lu {name}, kirain mau ngasih nomor WO taunya cuma nyapa 🥱",
            f"Oyy {name}, ada apa manggil? Mau curhat apa mau disuruh manjat tiang? 🪜",
            f"Hadir bang! Jangan cuma nyapa doang, info tiket close mana? 📡",
        ])

    if re.search(r"apa|kenapa|napa|knp", t):
        return random.choice([
            f"Kepo bet lu {name}, mau bantuin ngerjain WO apa gimana nih? 🛠️",
            f"Gak kenapa-kenapa kok {name}, cuma heran aja liat kelakuan lu wkwk 🗿",
        ])

    if re.search(r"korlap|bos|mandor|atasan", t):
        return random.choice([
            f"Sssttt jangan nyebut korlap sembarangan {name}, orangnya punya telinga di mana-mana 🤫",
            f"Dicariin korlap lu {name}, buruan setor absen gih sebelum kena semprot 🏃‍♂️",
        ])

    if re.search(r"ganteng|keren|hebat|pintar|pinter", t):
        return random.choice([
            f"Makasih pujiannya {name}, tapi maaf saya udah ada yang punya (sistem server) 😎",
            f"Baru sadar ya {name}? Bot Singkawang emang gak ada lawan wkwk 🦾",
        ])

    if re.search(r"boong|hoax|dusta|ngibul", t):
        return random.choice([
            f"Mana ada bot boong {name}, lu kali tuh yang suka ngibul pas izin sakit 😜",
            f"Fakta no hoax ini {name}, cek aja langsung di sistem kalo gak percaya 📊",
        ])

    return random.choice([
        f"Iya iya paham kok maksud lu {name}, tapi kerjaan hari ini beresin dulu yaa 😌",
        f"Jangan senggol-senggol mulu {name}, lagi sibuk ngitung rekap absen nih 🗿",
        f"Mau WO apa mau kopi {name}? Kalo gaada perintah mending santai dulu gih 🚶‍♂️",
        f"Ngetik apaan sih {name}, gak kebaca di sistem BIMA saya tuh bro 📡",
        f"Bukannya kerja malah ngajak bot ngobrol... awas dicariin korlap tuh {name}! 🏃‍♂️💨",
    ])


class ProvMyBranchAuthNeeded(Exception):
    """Exception ketika sesi MyBranch bot kedua (@prov_snw_bot) belum login atau sudah kedaluwarsa."""
    pass


# ==============================================================================
# MANAJEMEN SESI & LOGIN TACTICAL MYBRANCH (KHUSUS BOT 2 - NIK 16953908)
# ==============================================================================

def _load_prov_state_from_cloud() -> dict:
    """Memulihkan sesi MyBranch Bot 2 dari Google Sheet (_bot_session sel B1)."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(config.GOOGLE_CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sheet_ids = [config.GOOGLE_SHEET_ID, getattr(config, "BACKUP_SESSION_SHEET_ID", "1GafB5UK6J5sLIIUe57qCkjJFQ9BEDNV_T7M7gxKFitw")]
        for sid in sheet_ids:
            if not sid:
                continue
            try:
                sh = client.open_by_key(sid)
                ws = sh.worksheet("_bot_session")
                raw = ws.acell("B1").value
                if raw:
                    data = json.loads(raw)
                    with open(config.PROV_MYBRANCH_STATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    logger.info("Sesi MyBranch Bot 2 (@prov_snw_bot) berhasil dipulihkan dari cloud (_bot_session!B1).")
                    return data
            except Exception:
                continue
    except Exception:
        pass
    return {}


def _save_prov_state_to_cloud(state: dict):
    """Menyimpan sesi cookies MyBranch Bot 2 ke worksheet _bot_session sel B1."""
    if not state.get("cookies"):
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(config.GOOGLE_CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sheet_ids = [config.GOOGLE_SHEET_ID, getattr(config, "BACKUP_SESSION_SHEET_ID", "1GafB5UK6J5sLIIUe57qCkjJFQ9BEDNV_T7M7gxKFitw")]
        for sid in sheet_ids:
            if not sid:
                continue
            try:
                sh = client.open_by_key(sid)
                try:
                    ws = sh.worksheet("_bot_session")
                except Exception:
                    ws = sh.add_worksheet(title="_bot_session", rows=5, cols=2)
                ws.update_acell(
                    "B1",
                    json.dumps(
                        {
                            "cookies": state["cookies"],
                            "last_keepalive": state.get("last_keepalive"),
                            "nik": config.PROV_MYBRANCH_NIK,
                        },
                        ensure_ascii=False,
                    ),
                )
                return
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Gagal mencadangkan sesi Bot 2 ke cloud: {e}")


def _load_prov_state() -> dict:
    if os.path.exists(config.PROV_MYBRANCH_STATE_FILE):
        try:
            with open(config.PROV_MYBRANCH_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return _load_prov_state_from_cloud()


def _save_prov_state(state: dict, sync_cloud: bool = False):
    try:
        with open(config.PROV_MYBRANCH_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        if sync_cloud:
            _save_prov_state_to_cloud(state)
    except Exception as e:
        logger.error(f"Gagal menyimpan state MyBranch Bot 2: {e}")


def get_prov_pending_auth_step() -> str | None:
    state = _load_prov_state()
    return state.get("pending_step")


def clear_prov_pending_auth():
    state = _load_prov_state()
    state.pop("pending_step", None)
    state.pop("tiket", None)
    state.pop("target_chat_id", None)
    _save_prov_state(state)


def set_prov_pending_assign(wo_num: str, tech_nik: str, chat_id: int | None = None):
    state = _load_prov_state()
    state["pending_wo"] = wo_num
    state["pending_nik"] = tech_nik
    if chat_id:
        state["target_chat_id"] = chat_id
    _save_prov_state(state)


def pop_prov_pending_assign() -> tuple[str | None, str | None]:
    state = _load_prov_state()
    wo_num = state.pop("pending_wo", None)
    tech_nik = state.pop("pending_nik", None)
    _save_prov_state(state)
    return wo_num, tech_nik


def set_prov_pending_action(action: str, chat_id: int | None = None):
    state = _load_prov_state()
    state["pending_action"] = action
    if chat_id:
        state["target_chat_id"] = chat_id
    _save_prov_state(state)


def pop_prov_pending_action() -> str | None:
    state = _load_prov_state()
    action = state.pop("pending_action", None)
    _save_prov_state(state)
    return action


def _build_prov_opener():
    cj = http.cookiejar.MozillaCookieJar(config.PROV_MYBRANCH_COOKIES_FILE)
    if os.path.exists(config.PROV_MYBRANCH_COOKIES_FILE):
        try:
            cj.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [
        ("User-Agent", USER_AGENT),
        ("Accept", "application/json, text/plain, */*"),
    ]
    return opener, cj


def _submit_prov_credentials(opener, cj, login_url: str, tiket: str, captcha_text: str) -> dict:
    payload = json.dumps({
        "tiket": tiket,
        "username": config.PROV_MYBRANCH_NIK,
        "password": config.PROV_MYBRANCH_PASSWORD,
        "captcha": captcha_text.strip(),
    }).encode("utf-8")
    req = urllib.request.Request(
        login_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with opener.open(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            cj.save(ignore_discard=True, ignore_expires=True)
            return {"ok": True, **data}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        try:
            err_data = json.loads(raw)
        except Exception:
            err_data = {"message": raw}
        return {"ok": False, **err_data}


def initiate_prov_tactical_login(target_chat_id: int | None = None) -> dict:
    """Memulai login Tactical MyBranch untuk akun Bot 2 (NIK 16953908)."""
    opener, cj = _build_prov_opener()
    start_url = f"{config.MYBRANCH_URL}/api/auth/tactical/start"
    login_url = f"{config.MYBRANCH_URL}/api/auth/tactical/login"

    last_captcha_bytes = None
    last_tiket = None

    for attempt in range(4):
        req = urllib.request.Request(start_url, method="GET")
        with opener.open(req, timeout=20) as resp:
            start_data = json.loads(resp.read().decode("utf-8"))

        tiket = start_data["tiket"]
        b64_part = start_data["captcha"].split(",", 1)[1]
        captcha_bytes = base64.b64decode(b64_part)
        last_captcha_bytes = captcha_bytes
        last_tiket = tiket
        cj.save(ignore_discard=True, ignore_expires=True)

        guessed = _solve_captcha_with_gemini(captcha_bytes)
        if not guessed:
            break

        res = _submit_prov_credentials(opener, cj, login_url, tiket, guessed)
        if res.get("ok") and res.get("status") == "otp":
            state = _load_prov_state()
            state["pending_step"] = "otp"
            state["tiket"] = res["tiket"]
            if target_chat_id:
                state["target_chat_id"] = target_chat_id
            _save_prov_state(state)
            return {"step": "otp", "tiket": res["tiket"]}

        logger.warning(f"[ProvBot] Percobaan auto-captcha ke-{attempt+1} ({guessed}) ditolak: {res.get('message')}")
        if res.get("beresiko"):
            break

    req = urllib.request.Request(start_url, method="GET")
    with opener.open(req, timeout=20) as resp:
        start_data = json.loads(resp.read().decode("utf-8"))
    last_tiket = start_data["tiket"]
    last_captcha_bytes = base64.b64decode(start_data["captcha"].split(",", 1)[1])
    cj.save(ignore_discard=True, ignore_expires=True)

    state = _load_prov_state()
    state["pending_step"] = "captcha"
    state["tiket"] = last_tiket
    if target_chat_id:
        state["target_chat_id"] = target_chat_id
    _save_prov_state(state)

    return {
        "step": "captcha",
        "tiket": last_tiket,
        "captcha_bytes": last_captcha_bytes,
    }


def submit_prov_manual_captcha(captcha_text: str) -> dict:
    state = _load_prov_state()
    tiket = state.get("tiket")
    if not tiket:
        return {"ok": False, "need_restart": True, "message": "Tiket login tidak ditemukan, mengulang proses login..."}

    opener, cj = _build_prov_opener()
    login_url = f"{config.MYBRANCH_URL}/api/auth/tactical/login"
    res = _submit_prov_credentials(opener, cj, login_url, tiket, captcha_text)

    if res.get("ok") and res.get("status") == "otp":
        state["pending_step"] = "otp"
        state["tiket"] = res["tiket"]
        _save_prov_state(state)
        return {"ok": True, "step": "otp", "tiket": res["tiket"]}

    return {
        "ok": False,
        "need_restart": True,
        "message": res.get("message", "Captcha salah atau kedaluwarsa."),
    }


def submit_prov_otp_code(otp_code: str) -> dict:
    state = _load_prov_state()
    tiket = state.get("tiket")
    target_chat_id = state.get("target_chat_id")
    if not tiket:
        return {"ok": False, "need_restart": True, "message": "Tiket OTP tidak ditemukan atau sudah kedaluwarsa."}

    six_digits = re.findall(r"\b(\d{6})\b", otp_code)
    clean_otp = six_digits[-1] if six_digits else otp_code.strip()

    opener, cj = _build_prov_opener()
    otp_url = f"{config.MYBRANCH_URL}/api/auth/tactical/otp"
    payload = json.dumps({
        "tiket": tiket,
        "kode": clean_otp,
    }).encode("utf-8")
    req = urllib.request.Request(
        otp_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with opener.open(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            cj.save(ignore_discard=True, ignore_expires=True)

            if data.get("status") == "daftar":
                daftar_tiket = data.get("tiket", tiket)
                areas = data.get("areas", [])
                skw_area = ""
                for a in areas:
                    val = a if isinstance(a, str) else (a.get("id") or a.get("value") or a.get("nama") or "")
                    if "singkawang" in str(val).lower() or "skw" in str(val).lower():
                        skw_area = val
                        break
                daftar_payload = json.dumps({
                    "tiket": daftar_tiket,
                    "nama": "Provisioning SNW",
                    "role": "service_area" if skw_area else "guest",
                    "service_area": skw_area,
                }).encode("utf-8")
                daftar_req = urllib.request.Request(
                    f"{config.MYBRANCH_URL}/api/auth/tactical/daftar",
                    data=daftar_payload,
                    headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                    method="POST",
                )
                with opener.open(daftar_req, timeout=20) as d_resp:
                    d_data = json.loads(d_resp.read().decode("utf-8"))
                    cj.save(ignore_discard=True, ignore_expires=True)
                    clear_prov_pending_auth()
                    return {
                        "ok": False,
                        "need_restart": False,
                        "message": f"Akun memerlukan persetujuan pendaftaran MyBranch: {d_data.get('message', 'Pendaftaran terkirim.')}",
                    }

            cookies_list = []
            for c in cj:
                cookies_list.append({
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain or "takalbagbar.id",
                    "path": c.path or "/mybranch",
                    "secure": bool(c.secure),
                })
            state["cookies"] = cookies_list
            state.pop("pending_step", None)
            state.pop("tiket", None)
            _save_prov_state(state, sync_cloud=True)

            return {"ok": True, "data": data, "target_chat_id": target_chat_id}

    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        try:
            err_data = json.loads(raw)
        except Exception:
            err_data = {"message": raw}
        mulai_ulang = bool(err_data.get("mulaiUlang"))
        if mulai_ulang:
            clear_prov_pending_auth()
        return {
            "ok": False,
            "need_restart": mulai_ulang,
            "message": err_data.get("message", "Kode OTP salah atau kedaluwarsa."),
        }


def _get_prov_playwright_cookies() -> list[dict]:
    state = _load_prov_state()
    if state.get("cookies"):
        return state["cookies"]

    cookies_list = []
    if os.path.exists(config.PROV_MYBRANCH_COOKIES_FILE):
        try:
            cj = http.cookiejar.MozillaCookieJar(config.PROV_MYBRANCH_COOKIES_FILE)
            cj.load(ignore_discard=True, ignore_expires=True)
            for c in cj:
                cookies_list.append({
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain or "takalbagbar.id",
                    "path": c.path or "/",
                    "secure": bool(c.secure),
                })
        except Exception as e:
            logger.warning(f"[ProvBot] Gagal membaca cookiejar: {e}")
    return cookies_list


def keepalive_prov_session() -> bool:
    """Keep-alive berkala untuk sesi MyBranch Bot 2 (NIK 16953908)."""
    state = _load_prov_state()
    saved_cookies = state.get("cookies", [])
    if not saved_cookies and not os.path.exists(config.PROV_MYBRANCH_COOKIES_FILE):
        return False

    opener, cj = _build_prov_opener()
    if len(cj) == 0 and saved_cookies:
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in saved_cookies if c.get("name") and c.get("value"))
        opener.addheaders.append(("Cookie", cookie_header))

    target_url = f"{config.MYBRANCH_URL}/apps/bima/workorders"
    req = urllib.request.Request(target_url, method="GET")
    try:
        with opener.open(req, timeout=20) as resp:
            final_url = resp.geturl()
            if "/auth/login" in final_url:
                logger.info("[ProvBot] Keep-Alive MyBranch: Sesi sudah kedaluwarsa di server.")
                return False

            if len(cj) > 0:
                cj.save(ignore_discard=True, ignore_expires=True)
                updated_cookies = []
                for c in cj:
                    updated_cookies.append({
                        "name": c.name,
                        "value": c.value,
                        "domain": c.domain or "takalbagbar.id",
                        "path": c.path or "/mybranch",
                        "secure": bool(c.secure),
                    })
                state["cookies"] = updated_cookies
                state["last_keepalive"] = datetime.now(timezone.utc).isoformat()
                _save_prov_state(state)

            logger.info("[ProvBot] Keep-Alive MyBranch (16953908): Sesi tetap aktif.")
            return True
    except Exception as e:
        logger.warning(f"[ProvBot] Keep-Alive MyBranch gagal: {e}")
        return False


# ==============================================================================
# OTOMASI ASSIGN WO PSB DI MYBRANCH (/apps/bima/workorders)
# ==============================================================================

async def assign_wo_psb(wo_num: str, tech_nik: str) -> tuple[bytes, str]:
    """
    Membuka https://takalbagbar.id/mybranch/apps/bima/workorders dengan sesi NIK 16953908:
    1. Centang aktif 'Tampilkan yang sudah selesai'
    2. Masukkan No WO di kolom 'Cari'
    3. Klik logo pensil (Update status / assign) pada baris WO yang muncul
    4. Pilih tab 'FLOW'
    5. Pada 'Assign Teknisi', masukkan NIK yang diberikan -> klik 'Generate' -> centang 'Assign ke LENSA' -> klik 'Simpan'
    6. Mengembalikan (screenshot_bytes, caption_laporan)
    """
    cookies = _get_prov_playwright_cookies()
    if not cookies:
        raise ProvMyBranchAuthNeeded("Belum ada sesi login MyBranch untuk bot Provisioning (NIK 16953908).")

    clean_wo = wo_num.strip().upper()
    clean_nik = re.sub(r"\s+", "", tech_nik.strip())
    target_url = f"{config.MYBRANCH_URL}/apps/bima/workorders"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=2,
            color_scheme="dark",
            user_agent=USER_AGENT,
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        try:
            await page.add_init_script("""() => {
                try {
                    localStorage.setItem('theme', 'dark');
                    document.documentElement.classList.add('dark');
                    document.documentElement.style.colorScheme = 'dark';
                } catch (e) {}
            }""")

            logger.info(f"[ProvBot] Membuka Workorder PSB: {target_url} untuk Assign {clean_wo} -> {clean_nik}")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)

            if "/auth/login" in page.url:
                raise ProvMyBranchAuthNeeded("Sesi login MyBranch (NIK 16953908) telah kedaluwarsa.")

            # Tunggu elemen filter termuat
            await page.wait_for_selector('input[placeholder*="Cari"]', timeout=25000)
            await page.wait_for_timeout(1000)

            # 1. Pastikan "Tampilkan yang sudah selesai" dicentang aktif
            selesai_cb = await page.query_selector('label:has-text("Tampilkan yang sudah selesai") input[type="checkbox"]')
            if selesai_cb:
                is_checked = await selesai_cb.is_checked()
                if not is_checked:
                    await selesai_cb.check()
                    await page.wait_for_timeout(1500)

            # 2. Masukkan nomor WO ke kolom "Cari"
            cari_input = await page.query_selector('input[placeholder*="Cari"]')
            if not cari_input:
                raise RuntimeError("Kolom input 'Cari' tidak ditemukan di halaman Workorder PSB.")

            await cari_input.click()
            await cari_input.fill("")
            await page.wait_for_timeout(200)
            await cari_input.fill(clean_wo)

            # Tunggu proses filter tabel selesai (cek hingga baris mengandung clean_wo atau pesan tidak cocok muncul)
            row_found = False
            for _ in range(16):
                await page.wait_for_timeout(500)
                check = await page.evaluate(
                    """(wo) => {
                        const trs = Array.from(document.querySelectorAll('table tbody tr'));
                        for (let i = 0; i < trs.length; i++) {
                            const txt = (trs[i].innerText || '').toUpperCase();
                            if (txt.includes(wo)) {
                                return { found: true, index: i, count: trs.length };
                            }
                        }
                        const bodyTxt = document.body.innerText || '';
                        const empty = bodyTxt.includes('Tidak ada work order yang cocok');
                        return { found: false, empty, count: trs.length };
                    }""",
                    clean_wo,
                )
                if check.get("found"):
                    row_found = True
                    break

            # Jika WO belum ada di daftar lokal, coba tarik dari BIMA lewat fitur "Tambah Order"
            if not row_found:
                logger.info(f"[ProvBot] WO {clean_wo} belum muncul di tabel, mencoba tarik otomatis lewat Tambah Order...")
                pulled = await page.evaluate(
                    """async (wo) => {
                        try {
                            const r = await fetch('/mybranch/api/bima/workorders/tambah', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ wonum: wo })
                            });
                            return { ok: r.ok, status: r.status };
                        } catch (e) {
                            return { ok: false };
                        }
                    }""",
                    clean_wo,
                )
                if pulled.get("ok"):
                    # Trigger ulang pencarian
                    await cari_input.fill("")
                    await page.wait_for_timeout(300)
                    await cari_input.fill(clean_wo)
                    for _ in range(12):
                        await page.wait_for_timeout(500)
                        check2 = await page.evaluate(
                            """(wo) => {
                                const trs = Array.from(document.querySelectorAll('table tbody tr'));
                                return trs.some(tr => (tr.innerText || '').toUpperCase().includes(wo));
                            }""",
                            clean_wo,
                        )
                        if check2:
                            row_found = True
                            break

            if not row_found:
                # Ambil screenshot tampilan pencarian yang kosong sebagai bukti
                ss_not_found = await page.screenshot(type="png", full_page=False)
                wib_now = datetime.now(timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M WIB")
                caption = (
                    f"⚠️ *WO Tidak Ditemukan di Workorder PSB*\n"
                    f"📋 *No WO:* `{clean_wo}`\n"
                    f"👷 *Target NIK:* `{clean_nik}`\n"
                    f"🕒 *Waktu:* {wib_now}\n\n"
                    f"_Pastikan Nomor WO sudah benar._"
                )
                return ss_not_found, caption

            # 3. Ambil informasi baris WO sebelum di-assign dan klik tombol logo pensil (Update status / assign)
            row_meta = await page.evaluate(
                """(wo) => {
                    const trs = Array.from(document.querySelectorAll('table tbody tr'));
                    const targetTr = trs.find(tr => (tr.innerText || '').toUpperCase().includes(wo)) || trs[0];
                    if (!targetTr) return { ok: false };
                    const tds = Array.from(targetTr.querySelectorAll('td')).map(td => (td.innerText || '').trim());
                    const pencilBtn = targetTr.querySelector('button[title="Update status / assign"]');
                    if (!pencilBtn) return { ok: false, error: 'Tombol pensil tidak ditemukan pada baris WO.' };
                    if (pencilBtn.disabled) return { ok: false, error: 'Tombol pensil (Update status / assign) berstatus nonaktif untuk akun ini.' };
                    pencilBtn.click();
                    return {
                        ok: true,
                        wonum: tds[3] || wo,
                        status: tds[4] || '-',
                        customer: tds[8] || '-',
                        sc_order: tds[11] || '-',
                        service_num: tds[12] || '-',
                        sto: tds[15] || '-',
                        sa: tds[16] || '-'
                    };
                }""",
                clean_wo,
            )

            if not row_meta.get("ok"):
                err_msg = row_meta.get("error", "Gagal menekan tombol logo pensil pada baris WO.")
                ss_err = await page.screenshot(type="png", full_page=False)
                return ss_err, f"❌ *Gagal Membuka Menu Assign WO*\n📋 *No WO:* `{clean_wo}`\n⚠️ *Keterangan:* {err_msg}"

            # Tunggu modal terbuka
            await page.wait_for_selector(".fixed.inset-0.z-50", timeout=10000)
            await page.wait_for_timeout(800)

            # 4. Pilih tab FLOW di dalam modal
            flow_tab = await page.query_selector('.fixed.inset-0.z-50 button[role="tab"]:has-text("FLOW")')
            if flow_tab:
                await flow_tab.click()
                await page.wait_for_timeout(500)

            # 5. Masukkan NIK Teknisi pada kolom Assign Teknisi
            nik_input = await page.query_selector('.fixed.inset-0.z-50 input[placeholder="NIK"]')
            if not nik_input:
                raise RuntimeError("Kolom input NIK pada tab FLOW tidak ditemukan.")

            await nik_input.click()
            await nik_input.fill("")
            await page.wait_for_timeout(150)
            await nik_input.fill(clean_nik)
            await page.wait_for_timeout(400)

            # Klik tombol Generate untuk mengambil Nama Teknisi dari NIK
            gen_btn = await page.query_selector('.fixed.inset-0.z-50 button:has-text("Generate")')
            if gen_btn and not await gen_btn.is_disabled():
                await gen_btn.click()
                # Tunggu proses "Mencari…" selesai (tombol kembali bertulisan "Generate" dan nama teknisi terisi / pesan muncul)
                for _ in range(25):
                    await page.wait_for_timeout(400)
                    gen_state = await page.evaluate(
                        """() => {
                            const modal = document.querySelector('.fixed.inset-0.z-50');
                            if (!modal) return { done: true };
                            const namaInp = modal.querySelector('input[placeholder*="Nama teknisi"]');
                            const errEl = modal.querySelector('.text-destructive');
                            const btnText = Array.from(modal.querySelectorAll('button')).map(b => b.innerText.trim());
                            const stillSearching = btnText.some(t => t.includes('Mencari'));
                            return {
                                done: !stillSearching && ((namaInp && namaInp.value.trim().length > 0) || (errEl && errEl.innerText.trim().length > 0)),
                                nama: namaInp ? namaInp.value.trim() : '',
                                err: errEl ? errEl.innerText.trim() : ''
                            };
                        }"""
                    )
                    if gen_state.get("done"):
                        break

            # Cek apakah Nama Teknisi berhasil di-generate atau ada pesan error
            after_gen = await page.evaluate(
                """() => {
                    const modal = document.querySelector('.fixed.inset-0.z-50');
                    if (!modal) return { nama: '', err: '' };
                    const namaInp = modal.querySelector('input[placeholder*="Nama teknisi"]');
                    const errEls = Array.from(modal.querySelectorAll('.text-destructive')).map(e => e.innerText.trim()).filter(Boolean);
                    return {
                        nama: namaInp ? namaInp.value.trim() : '',
                        err: errEls.join(' | ')
                    };
                }"""
            )
            tech_name = after_gen.get("nama", "")
            gen_err = after_gen.get("err", "")

            # Jika NIK tidak ditemukan di database teknisi sehingga Generate gagal mengisi nama
            if not tech_name and gen_err:
                modal_el = await page.query_selector(".fixed.inset-0.z-50 > div")
                ss_modal = await (modal_el.screenshot(type="png") if modal_el else page.screenshot(type="png"))
                wib_now = datetime.now(timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M WIB")
                caption = (
                    f"⚠️ *Gagal Generate NIK Teknisi (FLOW)*\n"
                    f"📋 *No WO:* `{row_meta.get('wonum', clean_wo)}`\n"
                    f"👤 *Customer:* {row_meta.get('customer', '-')}\n"
                    f"👷 *NIK Input:* `{clean_nik}`\n"
                    f"❌ *Pesan:* `{gen_err}`\n"
                    f"🕒 *Waktu:* {wib_now}"
                )
                return ss_modal, caption

            # Centang "Assign ke LENSA" jika ada agar diteruskan ke aplikasi teknisi (FLOW/LENSA)
            lensa_cb = await page.query_selector('.fixed.inset-0.z-50 label:has-text("Assign ke LENSA") input[type="checkbox"]')
            if lensa_cb and not await lensa_cb.is_checked():
                await lensa_cb.check()
                await page.wait_for_timeout(300)

            # Ambil screenshot modal sebelum klik Simpan sebagai cadangan jika modal langsung tertutup
            modal_box = await page.query_selector(".fixed.inset-0.z-50 > div")
            ss_before_save = await (modal_box.screenshot(type="png") if modal_box else page.screenshot(type="png"))

            # Klik tombol Simpan yang terlihat di tab FLOW
            simpan_btns = await page.query_selector_all('.fixed.inset-0.z-50 button:has-text("Simpan")')
            clicked_save = False
            for sb in simpan_btns:
                if await sb.is_visible() and not await sb.is_disabled():
                    await sb.click()
                    clicked_save = True
                    break

            if not clicked_save:
                return ss_before_save, (
                    f"⚠️ *Tombol Simpan Tidak Aktif*\n"
                    f"📋 *No WO:* `{row_meta.get('wonum', clean_wo)}`\n"
                    f"👷 *Teknisi:* `{tech_name or clean_nik}`"
                )

            # Tunggu hingga proses menyimpan selesai (modal tertutup atau muncul pesan error di dalam modal)
            save_result = {"closed": False, "err": "", "banner": ""}
            for _ in range(30):
                await page.wait_for_timeout(500)
                st = await page.evaluate(
                    """() => {
                        const modal = document.querySelector('.fixed.inset-0.z-50');
                        if (!modal) {
                            return { closed: true, err: '' };
                        }
                        const btns = Array.from(modal.querySelectorAll('button')).map(b => b.innerText.trim());
                        const saving = btns.some(t => t.includes('Menyimpan'));
                        const errs = Array.from(modal.querySelectorAll('.text-destructive')).map(e => e.innerText.trim()).filter(Boolean);
                        return { closed: false, saving, err: errs.join(' | ') };
                    }"""
                )
                if st.get("closed"):
                    save_result["closed"] = True
                    break
                if not st.get("saving") and st.get("err"):
                    save_result["err"] = st["err"]
                    break

            await page.wait_for_timeout(800)

            # Simpan pembaruan cookies setelah interaksi
            latest_cookies = await context.cookies()
            if latest_cookies:
                state = _load_prov_state()
                state["cookies"] = [
                    {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", "takalbagbar.id"),
                        "path": c.get("path", "/mybranch"),
                        "secure": bool(c.get("secure", True)),
                    }
                    for c in latest_cookies
                    if "takalbagbar.id" in c.get("domain", "")
                ]
                _save_prov_state(state)

            wib_now = datetime.now(timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M WIB")

            # Jika gagal menyimpan dan modal masih terbuka dengan pesan error
            if not save_result["closed"] and save_result["err"]:
                modal_box_after = await page.query_selector(".fixed.inset-0.z-50 > div")
                ss_err_modal = await (modal_box_after.screenshot(type="png") if modal_box_after else page.screenshot(type="png"))
                caption = (
                    f"❌ *Gagal Menyimpan Assign WO*\n"
                    f"📋 *No WO:* `{row_meta.get('wonum', clean_wo)}`\n"
                    f"👤 *Customer:* {row_meta.get('customer', '-')}\n"
                    f"👷 *Teknisi:* `{tech_name}` (`{clean_nik}`)\n"
                    f"⚠️ *Pesan Error:* `{save_result['err']}`\n"
                    f"🕒 *Waktu:* {wib_now}"
                )
                return ss_err_modal, caption

            # Jika berhasil disimpan (modal tertutup), ambil screenshot area tabel Workorder PSB yang menampilkan baris WO ter-assign
            await page.evaluate("""() => {
                const sb = document.querySelector('[data-slot="sidebar"]');
                if (sb) sb.style.display = 'none';
                document.querySelectorAll('.body-wrapper, .page-wrapper, main, .container').forEach(el => {
                    el.style.marginLeft = '0px';
                    el.style.paddingLeft = '12px';
                    el.style.maxWidth = '100%';
                });
                window.scrollTo(0, 0);
            }""")
            await page.wait_for_timeout(400)

            ss_final = await page.screenshot(type="png", full_page=False)
            caption = (
                f"✅ *Assign WO PSB Berhasil (FLOW)*\n"
                f"📋 *No WO:* `{row_meta.get('wonum', clean_wo)}`\n"
                f"🔖 *Status BIMA:* `{row_meta.get('status', '-')}`\n"
                f"👤 *Customer:* {row_meta.get('customer', '-')}\n"
                f"🌐 *Service / STO:* `{row_meta.get('service_num', '-')}` / `{row_meta.get('sto', '-')}`\n"
                f"👷 *Assign Teknisi:* *{tech_name or '-'}* (`{clean_nik}`)\n"
                f"🕒 *Waktu:* {wib_now}"
            )
            return ss_final, caption

        finally:
            await context.close()
            await browser.close()


# ==============================================================================
# OTOMASI CEK ABSEN TEKNISI PROVISIONING (/apps/absensi)
# ==============================================================================

async def _select_radix_dropdown(page, trigger_index: int, option_text: str) -> str | None:
    """Memilih opsi pada dropdown Radix UI (Branch, Service Area, Craft, Status) di halaman Absensi."""
    triggers = await page.query_selector_all('button[role="combobox"][data-slot="select-trigger"]')
    if trigger_index >= len(triggers):
        return None
    await triggers[trigger_index].click()
    await page.wait_for_timeout(400)
    selected = await page.evaluate(
        """(target) => {
            const opts = Array.from(document.querySelectorAll('[role="option"]'));
            const match = opts.find(o => (o.innerText || '').trim().toUpperCase() === target.toUpperCase())
                       || opts.find(o => (o.innerText || '').trim().toUpperCase().includes(target.toUpperCase()));
            if (match) {
                match.click();
                return match.innerText.trim();
            }
            return null;
        }""",
        option_text,
    )
    await page.wait_for_timeout(450)
    return selected


async def capture_absensi_provisioning() -> tuple[bytes, str, str]:
    """
    Membuka https://takalbagbar.id/mybranch/apps/absensi:
    - Pilih Branch: PONTIANAK
    - Pilih Service Area: SINGKAWANG
    - Pilih Craft: TEKNISI_PROVISIONING
    - Pilih Status: Semua
    Mengambil Screenshot (SS) daftar nama teknisi di bawahnya dan menyusun deskripsi
    teknisi yang Belum Absen serta Sudah Absen.
    Mengembalikan (screenshot_bytes, short_caption, detail_description).
    """
    cookies = _get_prov_playwright_cookies()
    if not cookies:
        # Gunakan cadangan cookies MyBranch Bot 1 jika sesi Bot 2 belum login
        cookies = _get_playwright_cookies()
    if not cookies:
        raise ProvMyBranchAuthNeeded("Belum ada sesi login MyBranch yang aktif.")

    target_url = f"{config.MYBRANCH_URL}/apps/absensi"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1650, "height": 1400},
            device_scale_factor=2,
            color_scheme="dark",
            user_agent=USER_AGENT,
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        try:
            await page.add_init_script("""() => {
                try {
                    localStorage.setItem('theme', 'dark');
                    document.documentElement.classList.add('dark');
                    document.documentElement.style.colorScheme = 'dark';
                } catch (e) {}
            }""")

            logger.info(f"[ProvBot] Membuka halaman Absensi: {target_url}")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3500)

            # Jika sesi Bot 2 kedaluwarsa, coba cadangan sesi Bot 1 sebelum meminta OTP baru
            if "/auth/login" in page.url:
                fallback_cookies = _get_playwright_cookies()
                if fallback_cookies and fallback_cookies != cookies:
                    await context.clear_cookies()
                    await context.add_cookies(fallback_cookies)
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(3500)

            if "/auth/login" in page.url:
                raise ProvMyBranchAuthNeeded("Sesi login MyBranch telah kedaluwarsa.")

            # Tunggu dropdown filter dan tabel termuat
            await page.wait_for_selector('button[role="combobox"][data-slot="select-trigger"]', timeout=25000)
            await page.wait_for_selector("table tbody tr", timeout=25000)
            await page.wait_for_timeout(800)

            # Pilih filter sesuai permintaan:
            # 0: Branch -> PONTIANAK
            # 1: Service Area -> SINGKAWANG
            # 2: Craft -> TEKNISI_PROVISIONING
            # 3: Status -> Semua
            await _select_radix_dropdown(page, 0, "PONTIANAK")
            await _select_radix_dropdown(page, 1, "SINGKAWANG")
            await _select_radix_dropdown(page, 2, "TEKNISI_PROVISIONING")
            await _select_radix_dropdown(page, 3, "Semua")
            await page.wait_for_timeout(1000)

            # Ekstrak data baris teknisi dan rapikan tata letak tabel agar seluruh kolom & baris terlihat utuh di SS
            extracted = await page.evaluate(
                """() => {
                    // 1. Sembunyikan sidebar & hapus margin kiri
                    const sb = document.querySelector('[data-slot="sidebar"]');
                    if (sb) sb.style.display = 'none';
                    const header = document.querySelector('header');
                    if (header) header.style.display = 'none';
                    document.querySelectorAll('.body-wrapper, .page-wrapper, main, .container').forEach(el => {
                        el.style.marginLeft = '0px';
                        el.style.maxWidth = '100%';
                        el.style.width = '100%';
                    });

                    // 2. Sembunyikan kartu admin atas & tabel rekap cabang agar SS fokus pada Filter + Daftar Nama Teknisi di bawahnya
                    const cards = Array.from(document.querySelectorAll('[data-slot="card"]'));
                    for (const card of cards) {
                        const txt = card.innerText || '';
                        if (
                            txt.includes('Kredensial Login Tactical Pro') ||
                            txt.includes('Input manual (paste)') ||
                            txt.includes('Rekap Kehadiran per Branch')
                        ) {
                            card.style.display = 'none';
                        }
                    }

                    // 3. Ambil tabel daftar teknisi (tabel terakhir di halaman)
                    const tables = Array.from(document.querySelectorAll('table'));
                    const techTable = tables[tables.length - 1];
                    const techRows = [];

                    if (techTable) {
                        techTable.style.width = '100%';
                        techTable.style.tableLayout = 'auto';
                        const allTrs = Array.from(techTable.querySelectorAll('tr'));
                        for (const r of allTrs) {
                            const cells = Array.from(r.querySelectorAll('th, td'));
                            if (cells[3]) {
                                cells[3].style.maxWidth = '240px';
                                cells[3].style.whiteSpace = 'normal';
                                cells[3].style.lineHeight = '1.25';
                            }
                            if (cells[4]) {
                                cells[4].style.maxWidth = '150px';
                                cells[4].style.whiteSpace = 'normal';
                                cells[4].style.lineHeight = '1.25';
                            }
                            if (cells[6]) {
                                cells[6].style.fontWeight = '700';
                            }
                        }

                        const bodyTrs = Array.from(techTable.querySelectorAll('tbody tr'));
                        for (const tr of bodyTrs) {
                            const tds = Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').trim());
                            if (tds.length >= 11) {
                                techRows.push({
                                    no: tds[0],
                                    branch: tds[1],
                                    sa: tds[2],
                                    sektor: tds[3],
                                    sto: tds[4],
                                    nik: tds[5],
                                    nama: tds[6],
                                    unit: tds[7],
                                    craft: tds[8],
                                    presence_at: tds[9],
                                    status: tds[10]
                                });
                            }
                        }

                        let el = techTable.parentElement;
                        while (el && el !== document.body) {
                            const st = window.getComputedStyle(el);
                            if (st.maxHeight && st.maxHeight !== 'none') el.style.maxHeight = 'none';
                            if (st.overflowY === 'auto' || st.overflowY === 'scroll') el.style.overflowY = 'visible';
                            if (st.overflowX === 'auto' || st.overflowX === 'scroll') el.style.overflowX = 'visible';
                            el = el.parentElement;
                        }
                    }

                    // 4. Hitung batas bawah kartu tabel teknisi agar screenshot terpotong rapi tanpa ruang kosong di bawah
                    window.scrollTo(0, 0);
                    let bottomY = 1200;
                    if (techTable) {
                        const r = techTable.getBoundingClientRect();
                        bottomY = Math.ceil(r.bottom + 45);
                    }
                    return {
                        bottomY,
                        techRows
                    };
                }"""
            )

            bottom_y = max(700, min(int(extracted.get("bottomY", 1400)), 3200))
            await page.set_viewport_size({"width": 1650, "height": bottom_y})
            await page.wait_for_timeout(400)

            ss_bytes = await page.screenshot(
                type="png",
                clip={"x": 0, "y": 0, "width": 1650, "height": bottom_y},
            )

            tech_rows = extracted.get("techRows", [])
            sudah_absen = []
            belum_absen = []
            libur_sakit = []

            for r in tech_rows:
                st_upper = (r.get("status") or "").upper()
                if "BELUM" in st_upper:
                    belum_absen.append(r)
                elif "MASUK" in st_upper:
                    sudah_absen.append(r)
                else:
                    libur_sakit.append(r)

            total_tech = len(tech_rows)
            cnt_masuk = len(sudah_absen)
            cnt_belum = len(belum_absen)
            cnt_other = len(libur_sakit)
            persen = round((cnt_masuk / total_tech) * 100) if total_tech > 0 else 0
            wib_now = datetime.now(timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M WIB")

            def _clean_md(val: str) -> str:
                return re.sub(r"([_*`\[\]])", "", val or "-")

            short_caption = (
                f"📋 *ABSENSI TEKNISI PROVISIONING SINGKAWANG*\n"
                f"🏢 *Branch:* `PONTIANAK` | *SA:* `SINGKAWANG`\n"
                f"🛠️ *Craft:* `TEKNISI_PROVISIONING` | *Status:* `SEMUA`\n"
                f"🕒 *Waktu:* {wib_now}\n\n"
                f"📊 *Total:* *{total_tech} Teknisi* (`{persen}% Hadir`)\n"
                f"✅ *Sudah Absen (Masuk):* *{cnt_masuk} Orang*\n"
                f"⭕ *Belum Absen:* *{cnt_belum} Orang*"
                + (f"\n🌴 *Libur/Cuti/Izin:* *{cnt_other} Orang*" if cnt_other > 0 else "")
            )

            lines = [
                "📋 *DETAIL DATA ABSENSI TEKNISI PROVISIONING*",
                "🏢 *Branch:* `PONTIANAK` | *Service Area:* `SINGKAWANG`",
                "🛠️ *Craft:* `TEKNISI_PROVISIONING` | *Status:* `SEMUA`",
                f"🕒 *Waktu Update:* {wib_now}",
                "",
                "📊 *RINGKASAN KEHADIRAN:*",
                f"• 👥 Total Teknisi: *{total_tech} Orang* (`{persen}%`)",
                f"• ✅ Sudah Absen (Masuk): *{cnt_masuk} Orang*",
                f"• ⭕ Belum Absen: *{cnt_belum} Orang*",
            ]
            if cnt_other > 0:
                lines.append(f"• 🌴 Libur / Cuti / Sakit: *{cnt_other} Orang*")

            lines.append("")
            lines.append(f"⭕ *BELUM ABSEN ({cnt_belum} Teknisi):*")
            if belum_absen:
                for i, t in enumerate(belum_absen, 1):
                    nama = _clean_md(t.get("nama", "-"))
                    nik = _clean_md(t.get("nik", "-"))
                    sto = _clean_md(t.get("sto", "-"))
                    lines.append(f"{i}. *{nama}* (`{nik}`) — STO: {sto}")
            else:
                lines.append("_Semua teknisi sudah absen!_ 🎉")

            lines.append("")
            lines.append(f"✅ *SUDAH ABSEN / MASUK ({cnt_masuk} Teknisi):*")
            if sudah_absen:
                for i, t in enumerate(sudah_absen, 1):
                    nama = _clean_md(t.get("nama", "-"))
                    nik = _clean_md(t.get("nik", "-"))
                    waktu = _clean_md(t.get("presence_at", "-"))
                    # Ambil jam saja jika format YYYY-MM-DD HH:MM:SS
                    jam = waktu.split(" ")[-1][:5] if " " in waktu else waktu
                    sto = _clean_md(t.get("sto", "-"))
                    lines.append(f"{i}. *{nama}* (`{nik}`) — `{jam}` ({sto})")
            else:
                lines.append("_- Belum ada teknisi yang absen masuk -_")

            if libur_sakit:
                lines.append("")
                lines.append(f"🌴 *LIBUR / CUTI / SAKIT ({cnt_other} Teknisi):*")
                for i, t in enumerate(libur_sakit, 1):
                    nama = _clean_md(t.get("nama", "-"))
                    nik = _clean_md(t.get("nik", "-"))
                    st = _clean_md(t.get("status", "-"))
                    lines.append(f"{i}. *{nama}* (`{nik}`) — {st}")

            detail_text = "\n".join(lines)
            return ss_bytes, short_caption, detail_text

        finally:
            await context.close()
            await browser.close()


# ==============================================================================
# HANDLERS TELEGRAM UNTUK BOT KEDUA (@prov_snw_bot)
# ==============================================================================

def _is_allowed_prov_chat(chat_id: int | None) -> bool:
    """Memeriksa apakah chat_id adalah grup resmi @prov_snw_bot (-1004346054239)."""
    if chat_id is None:
        return False
    allowed_id = getattr(config, "PROV_ALLOWED_GROUP_ID", -1004346054239)
    return chat_id in {allowed_id, -1004346054239, -4346054239}


async def restrict_prov_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Memastikan @prov_snw_bot HANYA bisa diperintah di dalam grup -1004346054239.
    Semua perintah/pesan di japri (private chat) atau grup lain akan ditolak.
    """
    chat = update.effective_chat
    if not chat:
        return

    if _is_allowed_prov_chat(chat.id):
        return

    # Jika di japri (private chat), beri tahu bahwa bot hanya aktif di grup
    if chat.type == "private":
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    "⛔ Bot ini hanya dapat digunakan di dalam grup resmi.",
                    show_alert=True,
                )
            except Exception:
                pass
        elif update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "⛔ *Akses Ditolak*\n"
                    "Bot `@prov_snw_bot` hanya dapat diperintah di dalam grup resmi yang telah ditentukan (tidak melayani japri).",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    raise ApplicationHandlerStop()


def _get_prov_main_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🛠️ Assign WO PSB (/assignWO)", callback_data="prov_menu_assign")],
        [InlineKeyboardButton("📋 Cek Absen Provisioning (cek absen)", callback_data="prov_menu_absen")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def prov_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /start dan /help pada @prov_snw_bot."""
    context.user_data.clear()
    welcome_text = (
        "🤖 *Selamat Datang di Bot Provisioning SNW (@prov\\_snw\\_bot)*\n\n"
        f"🔗 *MyBranch:* `takalbagbar.id/mybranch`\n"
        f"👤 *User Tactical:* `{config.PROV_MYBRANCH_NIK}`\n\n"
        "*Daftar Perintah:*\n"
        "• `cek absen` atau `/cek_absen` — SS & Data Absensi Teknisi Provisioning Singkawang\n"
        "• `/assignWO` — Assign Workorder PSB ke NIK Teknisi (via FLOW)\n"
        "• `/assignWO <NO_WO> <NIK>` — Assign langsung 1 baris\n"
        "  _(Contoh: `/assignWO WO049029919 16840720`)_\n"
        "• `/cancel` — Batalkan proses yang sedang berjalan\n\n"
        "Silakan pilih tombol di bawah atau ketik perintah:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            welcome_text,
            parse_mode="Markdown",
            reply_markup=_get_prov_main_keyboard(),
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            welcome_text,
            parse_mode="Markdown",
            reply_markup=_get_prov_main_keyboard(),
        )


async def prov_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Membatalkan input No WO / NIK atau proses OTP yang tertunda."""
    context.user_data.clear()
    clear_prov_pending_auth()
    if update.callback_query:
        await update.callback_query.answer("Dibatalkan")
        await update.callback_query.message.reply_text(
            "❌ Proses dibatalkan.",
            reply_markup=_get_prov_main_keyboard(),
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            "❌ Proses dibatalkan.",
            reply_markup=_get_prov_main_keyboard(),
        )


async def _start_prov_login_flow(chat_id: int, context: ContextTypes.DEFAULT_TYPE, status_msg):
    """Memulai alur login Tactical untuk akun Bot 2 (16953908) dan meminta OTP di chat."""
    await status_msg.edit_text(
        f"🔐 Sesi MyBranch (`{config.PROV_MYBRANCH_NIK}`) belum aktif/kedaluwarsa.\n"
        f"⏳ _Sedang melakukan login otomatis ke Tactical MyBranch dan meminta OTP..._",
        parse_mode="Markdown",
    )
    try:
        login_res = await asyncio.to_thread(initiate_prov_tactical_login, chat_id)
    except Exception as e:
        logger.error(f"[ProvBot] Gagal memulai login MyBranch: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Gagal menghubungi server login MyBranch: `{e}`", parse_mode="Markdown")
        return

    if login_res.get("step") == "otp":
        await status_msg.edit_text(
            "📲 *Verifikasi OTP MyBranch Diperlukan*\n\n"
            f"Kode OTP 6 digit telah dikirimkan ke Telegram untuk akun NIK `{config.PROV_MYBRANCH_NIK}`.\n"
            "Silakan **ketik / kirimkan 6 digit kode OTP** tersebut di chat ini untuk melanjutkan proses Assign WO otomatis.",
            parse_mode="Markdown",
        )
    elif login_res.get("step") == "captcha":
        captcha_bytes = login_res.get("captcha_bytes")
        await status_msg.delete()
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(captcha_bytes),
            caption=(
                "🔤 *Verifikasi Captcha MyBranch*\n\n"
                "Silakan ketik **3 huruf/angka** yang terlihat pada gambar captcha di atas:"
            ),
            parse_mode="Markdown",
        )


async def _execute_assign_wo(chat_id: int, context: ContextTypes.DEFAULT_TYPE, wo_num: str, tech_nik: str, status_msg=None):
    """Menjalankan assign_wo_psb dan mengirimkan screenshot hasilnya ke chat_id."""
    clean_wo = wo_num.strip().upper()
    clean_nik = re.sub(r"\s+", "", tech_nik.strip())

    if status_msg is None:
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏳ _Sedang memproses Assign WO PSB di MyBranch..._\n"
                f"📋 *No WO:* `{clean_wo}`\n"
                f"👷 *NIK Teknisi:* `{clean_nik}`"
            ),
            parse_mode="Markdown",
        )
    else:
        await status_msg.edit_text(
            f"⏳ _Sedang memproses Assign WO PSB di MyBranch..._\n"
            f"📋 *No WO:* `{clean_wo}`\n"
            f"👷 *NIK Teknisi:* `{clean_nik}`",
            parse_mode="Markdown",
        )

    try:
        ss_bytes, caption = await assign_wo_psb(clean_wo, clean_nik)
        await status_msg.delete()
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(ss_bytes),
            caption=caption,
            parse_mode="Markdown",
        )
    except ProvMyBranchAuthNeeded:
        set_prov_pending_assign(clean_wo, clean_nik, chat_id)
        await _start_prov_login_flow(chat_id, context, status_msg)
    except Exception as e:
        logger.error(f"[ProvBot] Error saat assign_wo_psb({clean_wo}, {clean_nik}): {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ *Terjadi kesalahan saat Assign WO PSB:*\n`{e}`",
            parse_mode="Markdown",
        )


async def _execute_cek_absen(chat_id: int, context: ContextTypes.DEFAULT_TYPE, status_msg=None):
    """Menjalankan capture_absensi_provisioning dan mengirim SS + deskripsi lengkap Belum Absen & Sudah Absen."""
    if status_msg is None:
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⏳ _Sedang mengambil data & Screenshot Absensi Teknisi Provisioning Singkawang di MyBranch..._\n"
                "🏢 *Branch:* `PONTIANAK` | *SA:* `SINGKAWANG` | *Craft:* `TEKNISI_PROVISIONING`"
            ),
            parse_mode="Markdown",
        )
    else:
        await status_msg.edit_text(
            "⏳ _Sedang mengambil data & Screenshot Absensi Teknisi Provisioning Singkawang di MyBranch..._\n"
            "🏢 *Branch:* `PONTIANAK` | *SA:* `SINGKAWANG` | *Craft:* `TEKNISI_PROVISIONING`",
            parse_mode="Markdown",
        )

    try:
        ss_bytes, short_caption, detail_text = await capture_absensi_provisioning()
        await status_msg.delete()

        # Kirim Screenshot tabel nama-nama teknisi
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(ss_bytes),
            caption=short_caption,
            parse_mode="Markdown",
        )

        # Kirim deskripsi lengkap daftar teknisi yang Belum Absen dan Sudah Absen
        if len(detail_text) <= 4000:
            await context.bot.send_message(
                chat_id=chat_id,
                text=detail_text,
                parse_mode="Markdown",
            )
        else:
            # Jika sangat panjang, bagi per baris dengan aman
            chunks = []
            cur = []
            cur_len = 0
            for line in detail_text.splitlines():
                if cur_len + len(line) + 1 > 3900:
                    chunks.append("\n".join(cur))
                    cur = [line]
                    cur_len = len(line)
                else:
                    cur.append(line)
                    cur_len += len(line) + 1
            if cur:
                chunks.append("\n".join(cur))
            for ch in chunks:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=ch,
                    parse_mode="Markdown",
                )

    except ProvMyBranchAuthNeeded:
        set_prov_pending_action("cek_absen", chat_id)
        await _start_prov_login_flow(chat_id, context, status_msg)
    except Exception as e:
        logger.error(f"[ProvBot] Error saat capture_absensi_provisioning: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ *Terjadi kesalahan saat mengambil data Absensi Provisioning:*\n`{e}`",
            parse_mode="Markdown",
        )


async def handle_cek_absen_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /cek_absen, /cekabsen, /absen, atau tombol Cek Absen Provisioning."""
    chat_id = update.effective_chat.id
    if update.callback_query:
        await update.callback_query.answer()
    await _execute_cek_absen(chat_id, context)


async def handle_assign_wo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler untuk perintah /assignWO, /assignwo, /assign_wo, /assign, atau tombol menu Assign WO.
    Mendukung:
    1. Langsung: /assignWO <NO_WO> <NIK>
    2. Hanya No WO: /assignWO <NO_WO> -> lanjut minta NIK
    3. Tanpa argumen: /assignWO -> minta No WO -> minta NIK
    """
    chat_id = update.effective_chat.id
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batalkan", callback_data="prov_cancel")]])

    if update.callback_query:
        await update.callback_query.answer()
        context.user_data["prov_step"] = "awaiting_wo"
        context.user_data.pop("prov_wo_num", None)
        await update.callback_query.message.reply_text(
            "🛠️ *Assign WO PSB (MyBranch — FLOW)*\n\n"
            "Silakan masukkan **Nomor WO** yang akan di-assign:\n"
            "_(Contoh: `WO049029919`)_",
            parse_mode="Markdown",
            reply_markup=cancel_kb,
        )
        return

    args = list(context.args) if context.args else []
    if len(args) >= 2:
        wo_num = args[0].strip().upper()
        tech_nik = args[1].strip()
        context.user_data.clear()
        await _execute_assign_wo(chat_id, context, wo_num, tech_nik)
        return

    if len(args) == 1:
        wo_num = args[0].strip().upper()
        context.user_data["prov_wo_num"] = wo_num
        context.user_data["prov_step"] = "awaiting_nik"
        await update.effective_message.reply_text(
            f"📋 *No WO:* `{wo_num}`\n\n"
            "Sekarang silakan masukkan **NIK Teknisi** yang akan di-assign pada bagian *FLOW*:\n"
            "_(Contoh: `16840720`)_",
            parse_mode="Markdown",
            reply_markup=cancel_kb,
        )
        return

    context.user_data["prov_step"] = "awaiting_wo"
    context.user_data.pop("prov_wo_num", None)
    await update.effective_message.reply_text(
        "🛠️ *Assign WO PSB (MyBranch — FLOW)*\n\n"
        "Silakan masukkan **Nomor WO** yang akan di-assign:\n"
        "_(Contoh: `WO049029919`)_",
        parse_mode="Markdown",
        reply_markup=cancel_kb,
    )


async def handle_prov_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Menangani pesan teks di @prov_snw_bot:
    1. Jika sedang menunggu Captcha / OTP MyBranch -> proses login & lanjutkan Assign WO yang tertunda
    2. Jika user mengetik 'assignWO' tanpa garis miring -> mulai alur Assign WO
    3. Jika sedang dalam tahapan input No WO ('awaiting_wo') -> simpan No WO lalu minta NIK Teknisi
    4. Jika sedang dalam tahapan input NIK ('awaiting_nik') -> jalankan Assign WO ke MyBranch
    """
    if not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text.strip()
    chat_id = update.effective_chat.id

    # 1. Cek apakah sedang menunggu input Captcha atau OTP MyBranch
    pending_auth = get_prov_pending_auth_step()
    if pending_auth == "captcha":
        status_msg = await update.effective_message.reply_text("⏳ _Memverifikasi Captcha ke MyBranch..._", parse_mode="Markdown")
        res = await asyncio.to_thread(submit_prov_manual_captcha, text)
        if res.get("ok") and res.get("step") == "otp":
            await status_msg.edit_text(
                "✅ *Captcha Diterima!*\n\n"
                f"📲 Kode OTP 6 digit telah dikirimkan ke Telegram untuk akun NIK `{config.PROV_MYBRANCH_NIK}`.\n"
                "Silakan **ketik / kirimkan 6 digit kode OTP** tersebut di chat ini:",
                parse_mode="Markdown",
            )
            return
        await _start_prov_login_flow(chat_id, context, status_msg)
        return

    if pending_auth == "otp":
        status_msg = await update.effective_message.reply_text("⏳ _Memverifikasi kode OTP ke MyBranch..._", parse_mode="Markdown")
        res = await asyncio.to_thread(submit_prov_otp_code, text)
        if res.get("ok"):
            pending_act = pop_prov_pending_action()
            wo_num, tech_nik = pop_prov_pending_assign()
            if pending_act == "cek_absen":
                await status_msg.edit_text(
                    f"✅ *Login MyBranch (`{config.PROV_MYBRANCH_NIK}`) Berhasil!*\n"
                    f"⏳ _Melanjutkan pengambilan data Absensi Teknisi Provisioning Singkawang..._",
                    parse_mode="Markdown",
                )
                await _execute_cek_absen(chat_id, context, status_msg=status_msg)
            elif wo_num and tech_nik:
                await status_msg.edit_text(
                    f"✅ *Login MyBranch (`{config.PROV_MYBRANCH_NIK}`) Berhasil!*\n"
                    f"⏳ _Melanjutkan proses Assign WO `{wo_num}` ke NIK `{tech_nik}`..._",
                    parse_mode="Markdown",
                )
                await _execute_assign_wo(chat_id, context, wo_num, tech_nik, status_msg=status_msg)
            else:
                await status_msg.edit_text(
                    f"✅ *Login MyBranch (`{config.PROV_MYBRANCH_NIK}`) Berhasil!*\n"
                    "Sesi telah disimpan secara permanen. Silakan gunakan `/assignWO` atau ketik `cek absen`.",
                    parse_mode="Markdown",
                )
            return

        if res.get("need_restart"):
            await status_msg.edit_text(
                f"⚠️ {res.get('message', 'OTP kedaluwarsa.')}\n⏳ _Mengulang permintaan login..._",
                parse_mode="Markdown",
            )
            await _start_prov_login_flow(chat_id, context, status_msg)
        else:
            await status_msg.edit_text(
                f"❌ {res.get('message', 'Kode OTP salah.')}\nSilakan masukkan kembali 6 digit kode OTP yang benar:",
                parse_mode="Markdown",
            )
        return

    # 2. Dukung pesan teks "cek absen" dan "assignWO" / "assign wo" tanpa slash (/)
    lower_text = re.sub(r"\s+", " ", text.lower()).strip()
    if lower_text in {"cek absen", "cekabsen", "cek absensi", "absen", "absen teknisi", "absen provisioning"} or lower_text.startswith("cek absen"):
        context.user_data.clear()
        await _execute_cek_absen(chat_id, context)
        return

    if lower_text.startswith("assignwo") or lower_text.startswith("assign_wo") or lower_text.startswith("assign wo"):
        parts = text.split()
        if lower_text.startswith("assign wo"):
            args = parts[2:]
        else:
            args = parts[1:]
        context.args = args
        await handle_assign_wo_command(update, context)
        return

    # 3. Tahapan interaktif: menunggu Nomor WO ('awaiting_wo')
    prov_step = context.user_data.get("prov_step")
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batalkan", callback_data="prov_cancel")]])

    if prov_step == "awaiting_wo":
        tokens = text.split()
        # Jika user langsung mengirim "WO049029919 16840720" sekaligus
        if len(tokens) >= 2 and re.match(r"^(WO)?\d+", tokens[0], re.IGNORECASE) and re.match(r"^\d{5,12}$", tokens[1]):
            wo_num = tokens[0].upper()
            if not wo_num.startswith("WO") and wo_num.isdigit():
                wo_num = f"WO{wo_num}"
            tech_nik = tokens[1]
            context.user_data.clear()
            await _execute_assign_wo(chat_id, context, wo_num, tech_nik)
            return

        wo_candidate = tokens[0].upper() if tokens else ""
        if not wo_candidate:
            await update.effective_message.reply_text(
                "⚠️ Nomor WO tidak valid. Silakan masukkan Nomor WO (contoh: `WO049029919`):",
                parse_mode="Markdown",
                reply_markup=cancel_kb,
            )
            return

        if not wo_candidate.startswith("WO") and wo_candidate.isdigit():
            wo_candidate = f"WO{wo_candidate}"

        context.user_data["prov_wo_num"] = wo_candidate
        context.user_data["prov_step"] = "awaiting_nik"
        await update.effective_message.reply_text(
            f"📋 *No WO:* `{wo_candidate}`\n\n"
            "Sekarang silakan masukkan **NIK Teknisi** yang akan di-assign pada bagian *FLOW*:\n"
            "_(Contoh: `16840720`)_",
            parse_mode="Markdown",
            reply_markup=cancel_kb,
        )
        return

    # 4. Tahapan interaktif: menunggu NIK Teknisi ('awaiting_nik')
    if prov_step == "awaiting_nik":
        wo_num = context.user_data.get("prov_wo_num", "")
        nik_match = re.search(r"\b(\d{5,15})\b", text)
        tech_nik = nik_match.group(1) if nik_match else re.sub(r"\s+", "", text)
        if not tech_nik or not any(ch.isdigit() for ch in tech_nik):
            await update.effective_message.reply_text(
                f"⚠️ Format NIK tidak valid. Silakan masukkan angka **NIK Teknisi** untuk WO `{wo_num}`:",
                parse_mode="Markdown",
                reply_markup=cancel_kb,
            )
            return

        context.user_data.clear()
        await _execute_assign_wo(chat_id, context, wo_num, tech_nik)
        return

    # 5. Balas balasan (reply) atau mention di grup dengan santai/nyeleneh jika bukan perintah
    reply_msg = update.effective_message.reply_to_message
    is_reply_to_bot = bool(reply_msg and reply_msg.from_user and reply_msg.from_user.id == context.bot.id)
    bot_username = getattr(context.bot, "username", "prov_snw_bot") or "prov_snw_bot"
    is_mention = f"@{bot_username.lower()}" in lower_text or "@prov_snw_bot" in lower_text

    if is_reply_to_bot or is_mention:
        user_name = update.effective_user.first_name if update.effective_user else "bang"
        bot_prev = (reply_msg.text or reply_msg.caption or "") if reply_msg else ""
        reply_txt = await asyncio.to_thread(_generate_smart_nyeleneh_reply, text, user_name, bot_prev)
        await update.effective_message.reply_text(reply_txt)
        return


async def prov_post_init(application):
    """Mendaftarkan menu perintah (/) untuk @prov_snw_bot khusus di grup -1004346054239."""
    asyncio.create_task(asyncio.to_thread(_load_prov_state))
    commands = [
        BotCommand("cek_absen", "📋 Cek Absensi Teknisi Provisioning Singkawang"),
        BotCommand("assignwo", "🛠️ Assign WO PSB ke NIK Teknisi (FLOW)"),
        BotCommand("start", "🏠 Menu Utama Bot Provisioning"),
        BotCommand("cancel", "❌ Batalkan Proses"),
    ]
    try:
        # Hapus menu (/) di chat pribadi (japri) karena bot hanya untuk grup -1004346054239
        await application.bot.delete_my_commands(scope=BotCommandScopeDefault())
        await application.bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
        await application.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
        try:
            await application.bot.set_my_commands(
                commands,
                scope=BotCommandScopeChat(getattr(config, "PROV_ALLOWED_GROUP_ID", -1004346054239)),
            )
        except Exception as ge:
            logger.debug(f"[ProvBot] set_my_commands khusus grup {config.PROV_ALLOWED_GROUP_ID}: {ge}")
        logger.info(f"[ProvBot] Menu command (/) @prov_snw_bot berhasil didaftarkan khusus grup {config.PROV_ALLOWED_GROUP_ID}.")
    except Exception as e:
        logger.warning(f"[ProvBot] Gagal mengatur command menu: {e}")


def build_prov_application():
    """Membangun instance Application untuk Bot Kedua (@prov_snw_bot)."""
    if not getattr(config, "PROV_BOT_TOKEN", ""):
        return None

    app = (
        ApplicationBuilder()
        .token(config.PROV_BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .post_init(prov_post_init)
        .build()
    )

    # Jadwalkan Keep-Alive sesi MyBranch Bot 2 setiap 10 menit
    if app.job_queue:
        async def _run_prov_keepalive(context: ContextTypes.DEFAULT_TYPE):
            await asyncio.to_thread(keepalive_prov_session)

        app.job_queue.run_repeating(_run_prov_keepalive, interval=600, first=150, name="prov_mybranch_keepalive")

    # Batasi agar bot HANYA bisa diperintah di grup -1004346054239 (tolak japri & grup lain)
    app.add_handler(TypeHandler(Update, restrict_prov_to_group), group=-1)

    app.add_handler(CommandHandler(["start", "help"], prov_start))
    app.add_handler(CommandHandler(["cancel", "batal"], prov_cancel))
    app.add_handler(
        CommandHandler(
            ["assignWO", "assignwo", "assign_wo", "assign"],
            handle_assign_wo_command,
        )
    )
    app.add_handler(
        CommandHandler(
            ["cek_absen", "cekabsen", "absen", "absensi"],
            handle_cek_absen_command,
        )
    )
    app.add_handler(CallbackQueryHandler(handle_assign_wo_command, pattern="^prov_menu_assign$"))
    app.add_handler(CallbackQueryHandler(handle_cek_absen_command, pattern="^prov_menu_absen$"))
    app.add_handler(CallbackQueryHandler(prov_cancel, pattern="^prov_cancel$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_prov_text_input))

    return app
