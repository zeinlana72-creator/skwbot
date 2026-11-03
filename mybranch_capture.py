import asyncio
import base64
import http.cookiejar
import io
import json
import logging
import os
import re
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone, timedelta
from xml.sax.saxutils import escape as xml_escape
from playwright.async_api import async_playwright
import config

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class MyBranchAuthNeeded(Exception):
    """Exception ketika sesi belum login atau sudah kedaluwarsa."""
    pass


def _load_state_from_cloud() -> dict:
    """Memulihkan sesi MyBranch dari Google Sheet (_bot_session) jika kontainer baru saja restart/redeploy."""
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
                raw = ws.acell("A1").value
                if raw:
                    data = json.loads(raw)
                    with open(config.MYBRANCH_STATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    logger.info("Sesi MyBranch berhasil dipulihkan dari penyimpanan cloud (_bot_session).")
                    return data
            except Exception:
                continue
    except Exception:
        pass
    return {}


def _save_state_to_cloud(state: dict):
    """Menyimpan sesi cookies MyBranch ke worksheet _bot_session agar tahan terhadap restart/redeploy."""
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
                ws.update_acell("A1", json.dumps({"cookies": state["cookies"], "last_keepalive": state.get("last_keepalive")}, ensure_ascii=False))
                return
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Gagal mencadangkan sesi ke cloud: {e}")


def _load_state() -> dict:
    if os.path.exists(config.MYBRANCH_STATE_FILE):
        try:
            with open(config.MYBRANCH_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return _load_state_from_cloud()


def _save_state(state: dict, sync_cloud: bool = False):
    try:
        with open(config.MYBRANCH_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        if sync_cloud:
            _save_state_to_cloud(state)
    except Exception as e:
        logger.error(f"Gagal menyimpan state MyBranch: {e}")


def get_pending_auth_step() -> str | None:
    """Mengembalikan 'captcha' atau 'otp' jika sedang menunggu input dari user di Telegram."""
    state = _load_state()
    return state.get("pending_step")


def clear_pending_auth():
    state = _load_state()
    state.pop("pending_step", None)
    state.pop("tiket", None)
    state.pop("target_chat_id", None)
    state.pop("pending_action", None)
    state.pop("pending_nds", None)
    _save_state(state)


def set_pending_ukur_context(nd_list: list[str], chat_id: int | None = None):
    state = _load_state()
    state["pending_action"] = "ukur_masal"
    state["pending_nds"] = nd_list
    if chat_id:
        state["target_chat_id"] = chat_id
    _save_state(state)


def get_pending_ukur_context() -> tuple[str | None, list[str]]:
    state = _load_state()
    return state.get("pending_action"), state.get("pending_nds") or []


def extract_nd_numbers(raw_text: str) -> list[str]:
    """Mengekstrak daftar nomor internet (ND) dari teks input user (dipisahkan enter, spasi, koma, titik koma)."""
    if not raw_text:
        return []
    # Cari semua deretan angka minimal 6 digit (nomor internet/ND Telkom umumnya 9-13 digit)
    candidates = re.findall(r"\b\d{6,16}\b", raw_text)
    if not candidates:
        # Fallback jika ada format non-standar per baris/spasi
        for token in re.split(r"[\s,;]+", raw_text.strip()):
            clean = token.strip()
            if clean and not clean.startswith("/") and any(ch.isdigit() for ch in clean):
                candidates.append(clean)
    seen = set()
    unique_nds = []
    for nd in candidates:
        if nd not in seen:
            seen.add(nd)
            unique_nds.append(nd)
        if len(unique_nds) >= 500:
            break
    return unique_nds


def _build_opener():
    cj = http.cookiejar.MozillaCookieJar(config.MYBRANCH_COOKIES_FILE)
    if os.path.exists(config.MYBRANCH_COOKIES_FILE):
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


def _solve_captcha_with_gemini(img_bytes: bytes) -> str | None:
    """Mencoba membaca 3 karakter Captcha menggunakan Gemini REST API."""
    if not config.GEMINI_API_KEY:
        return None
    b64_img = base64.b64encode(img_bytes).decode("ascii")
    for model_name in ["gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-3.8-flash"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={config.GEMINI_API_KEY}"
        payload = json.dumps({
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}},
                    {"text": "Read the 3 uppercase letters in this captcha image (ignore thin background lines). Return ONLY the 3 letters."}
                ]
            }]
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                txt = data["candidates"][0]["content"]["parts"][0]["text"]
                cleaned = re.sub(r"[^A-Za-z0-9]", "", txt).upper()
                if 3 <= len(cleaned) <= 5:
                    logger.info(f"Gemini ({model_name}) berhasil membaca captcha: {cleaned}")
                    return cleaned
        except Exception as e:
            logger.warning(f"Gemini ({model_name}) gagal membaca captcha: {e}")
    return None


def initiate_tactical_login(target_chat_id: int | None = None) -> dict:
    """
    Memulai proses login ke https://takalbagbar.id/mybranch:
    1. Ambil captcha & tiket dari /api/auth/tactical/start
    2. Coba selesaikan captcha otomatis (maks 2x percobaan)
    3. Jika berhasil -> kirim request login -> server mengirim OTP ke Telegram user -> return {'step': 'otp'}
    4. Jika auto-captcha gagal -> return {'step': 'captcha', 'captcha_bytes': ...} agar user ketik captcha dulu di bot
    """
    opener, cj = _build_opener()
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

        # Coba submit login dengan hasil bacaan Gemini
        res = _submit_credentials(opener, cj, login_url, tiket, guessed)
        if res.get("ok") and res.get("status") == "otp":
            state = _load_state()
            state["pending_step"] = "otp"
            state["tiket"] = res["tiket"]
            if target_chat_id:
                state["target_chat_id"] = target_chat_id
            _save_state(state)
            return {"step": "otp", "tiket": res["tiket"]}

        logger.warning(f"Percobaan auto-captcha ke-{attempt+1} ({guessed}) ditolak: {res.get('message')}")
        if res.get("beresiko"):
            break

    # Jika belum berhasil otomatis, ambil 1 captcha baru untuk dikirim ke chat Telegram user
    req = urllib.request.Request(start_url, method="GET")
    with opener.open(req, timeout=20) as resp:
        start_data = json.loads(resp.read().decode("utf-8"))
    last_tiket = start_data["tiket"]
    last_captcha_bytes = base64.b64decode(start_data["captcha"].split(",", 1)[1])
    cj.save(ignore_discard=True, ignore_expires=True)

    state = _load_state()
    state["pending_step"] = "captcha"
    state["tiket"] = last_tiket
    if target_chat_id:
        state["target_chat_id"] = target_chat_id
    _save_state(state)

    return {
        "step": "captcha",
        "tiket": last_tiket,
        "captcha_bytes": last_captcha_bytes
    }


def _submit_credentials(opener, cj, login_url: str, tiket: str, captcha_text: str) -> dict:
    payload = json.dumps({
        "tiket": tiket,
        "username": config.MYBRANCH_NIK,
        "password": config.MYBRANCH_PASSWORD,
        "captcha": captcha_text.strip()
    }).encode("utf-8")
    req = urllib.request.Request(
        login_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST"
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


def submit_manual_captcha(captcha_text: str) -> dict:
    """Dipanggil ketika user mengirim teks captcha di chat bot Telegram."""
    state = _load_state()
    tiket = state.get("tiket")
    if not tiket:
        return {"ok": False, "need_restart": True, "message": "Tiket login tidak ditemukan, mengulang proses login..."}

    opener, cj = _build_opener()
    login_url = f"{config.MYBRANCH_URL}/api/auth/tactical/login"
    res = _submit_credentials(opener, cj, login_url, tiket, captcha_text)

    if res.get("ok") and res.get("status") == "otp":
        state["pending_step"] = "otp"
        state["tiket"] = res["tiket"]
        _save_state(state)
        return {"ok": True, "step": "otp", "tiket": res["tiket"]}

    return {
        "ok": False,
        "need_restart": True,
        "message": res.get("message", "Captcha salah atau kedaluwarsa.")
    }


def submit_otp_code(otp_code: str) -> dict:
    """Dipanggil ketika user mengirim kode OTP Tactical di chat bot Telegram."""
    state = _load_state()
    tiket = state.get("tiket")
    target_chat_id = state.get("target_chat_id")
    if not tiket:
        return {"ok": False, "need_restart": True, "message": "Tiket OTP tidak ditemukan atau sudah kedaluwarsa."}

    # Dukung baik input 6 digit langsung maupun hasil forward pesan dari Telkom Akses
    six_digits = re.findall(r"\b(\d{6})\b", otp_code)
    clean_otp = six_digits[-1] if six_digits else otp_code.strip()

    opener, cj = _build_opener()
    otp_url = f"{config.MYBRANCH_URL}/api/auth/tactical/otp"
    payload = json.dumps({
        "tiket": tiket,
        "kode": clean_otp
    }).encode("utf-8")
    req = urllib.request.Request(
        otp_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST"
    )
    try:
        with opener.open(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            cj.save(ignore_discard=True, ignore_expires=True)

            # Jika akun baru pertama kali masuk ke MyBranch dan diminta daftar role
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
                    "nama": "Dandi",
                    "role": "service_area" if skw_area else "guest",
                    "service_area": skw_area
                }).encode("utf-8")
                daftar_req = urllib.request.Request(
                    f"{config.MYBRANCH_URL}/api/auth/tactical/daftar",
                    data=daftar_payload,
                    headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                    method="POST"
                )
                with opener.open(daftar_req, timeout=20) as d_resp:
                    d_data = json.loads(d_resp.read().decode("utf-8"))
                    cj.save(ignore_discard=True, ignore_expires=True)
                    clear_pending_auth()
                    return {
                        "ok": False,
                        "need_restart": False,
                        "message": f"Akun memerlukan persetujuan pendaftaran MyBranch: {d_data.get('message', 'Pendaftaran terkirim.')}"
                    }

            # Simpan cookies ke state JSON juga agar persisten
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
            _save_state(state, sync_cloud=True)

            return {"ok": True, "data": data, "target_chat_id": target_chat_id}

    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        try:
            err_data = json.loads(raw)
        except Exception:
            err_data = {"message": raw}
        mulai_ulang = bool(err_data.get("mulaiUlang"))
        if mulai_ulang:
            clear_pending_auth()
        return {
            "ok": False,
            "need_restart": mulai_ulang,
            "message": err_data.get("message", "Kode OTP salah atau kedaluwarsa.")
        }


def _get_playwright_cookies() -> list[dict]:
    """Mengambil daftar cookies untuk disuntikkan ke context Playwright."""
    state = _load_state()
    if state.get("cookies"):
        return state["cookies"]

    cookies_list = []
    if os.path.exists(config.MYBRANCH_COOKIES_FILE):
        try:
            cj = http.cookiejar.MozillaCookieJar(config.MYBRANCH_COOKIES_FILE)
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
            logger.warning(f"Gagal membaca cookiejar: {e}")
    return cookies_list


def keepalive_mybranch_session() -> bool:
    """
    Melakukan request berkala (keep-alive) ke https://takalbagbar.id/mybranch/apps/absensi-assurance
    menggunakan cookies yang tersimpan agar sesi login tidak terkena idle timeout.
    Sekaligus memperbarui cookies jika server mengirimkan Set-Cookie baru (rolling session).
    """
    state = _load_state()
    saved_cookies = state.get("cookies", [])
    if not saved_cookies and not os.path.exists(config.MYBRANCH_COOKIES_FILE):
        return False

    opener, cj = _build_opener()
    # Pastikan cookies dari state JSON juga termuat di CookieJar jika file cookiejar belum sinkron
    if len(cj) == 0 and saved_cookies:
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in saved_cookies if c.get("name") and c.get("value"))
        opener.addheaders.append(("Cookie", cookie_header))

    target_url = f"{config.MYBRANCH_URL}/apps/absensi-assurance"
    req = urllib.request.Request(target_url, method="GET")
    try:
        with opener.open(req, timeout=20) as resp:
            final_url = resp.geturl()
            if "/auth/login" in final_url:
                logger.info("Keep-Alive MyBranch: Sesi sudah kedaluwarsa di server.")
                return False

            # Simpan kembali cookies terbaru dari CookieJar jika ada pembaruan masa aktif dari server
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
                _save_state(state)

            logger.info("Keep-Alive MyBranch: Sesi berhasil dipertahankan tetap aktif.")
            return True
    except Exception as e:
        logger.warning(f"Keep-Alive MyBranch gagal: {e}")
        return False


async def capture_absen_assurance() -> tuple[bytes, str]:
    """
    Membuka https://takalbagbar.id/mybranch/apps/absensi-assurance menggunakan sesi login yang tersimpan,
    memfilter/memfokuskan tabel hanya pada bagian SINGKAWANG (BKY, PMK, SMB, SNW, TBA),
    dan mengembalikan (screenshot_bytes, caption_text).
    Jika belum login / sesi habis, melempar MyBranchAuthNeeded.
    """
    cookies = _get_playwright_cookies()
    if not cookies:
        raise MyBranchAuthNeeded("Belum ada sesi login MyBranch yang tersimpan.")

    target_url = f"{config.MYBRANCH_URL}/apps/absensi-assurance"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1400},
            device_scale_factor=2,
            color_scheme="dark",
            user_agent=USER_AGENT
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        try:
            logger.info(f"Membuka halaman Absen Assurance: {target_url}")
            await page.goto(target_url, wait_until="networkidle", timeout=45000)
            await page.wait_for_timeout(2000)

            if "/auth/login" in page.url:
                logger.warning("Sesi MyBranch kedaluwarsa (redirect ke /auth/login).")
                raise MyBranchAuthNeeded("Sesi login MyBranch telah habis.")

            # Pastikan mode gelap aktif sesuai tampilan referensi user
            await page.evaluate("""() => {
                document.documentElement.classList.remove('light');
                document.documentElement.classList.add('dark');
                document.documentElement.style.colorScheme = 'dark';
            }""")

            # Tunggu hingga tabel dan baris SINGKAWANG termuat
            await page.wait_for_selector("table tbody tr", timeout=25000)
            await page.wait_for_function(
                """() => document.body.innerText.toUpperCase().includes('SINGKAWANG')""",
                timeout=20000
            )
            await page.wait_for_timeout(800)

            # Filter baris tabel agar HANYA menampilkan bagian SINGKAWANG beserta STO-nya (BKY, PMK, SMB, SNW, TBA)
            # dan ekstrak data statistik teknisi Singkawang untuk caption laporan
            skw_data = await page.evaluate("""() => {
                const table = document.querySelector('table');
                if (!table) return { found: false };

                const rows = Array.from(table.querySelectorAll('tbody tr'));
                let inSingkawang = false;
                let skwHeaderSummary = '';
                const techList = [];

                for (const row of rows) {
                    const cells = Array.from(row.querySelectorAll('td, th'));
                    const rowText = (row.innerText || '').trim();
                    const upperText = rowText.toUpperCase();
                    const firstCellText = cells.length > 0 ? (cells[0].innerText || '').trim().toUpperCase() : '';

                    // Deteksi baris header Service Area (misal: "MEMPAWAH   24 teknisi...", "SINGKAWANG   28 teknisi...", "SINTANG...")
                    const isServiceAreaHeader = (
                        upperText.includes('TEKNISI') &&
                        (cells.length <= 3 || row.querySelector('[colspan]'))
                    );

                    if (isServiceAreaHeader) {
                        if (upperText.startsWith('SINGKAWANG') || firstCellText === 'SINGKAWANG') {
                            inSingkawang = true;
                            skwHeaderSummary = rowText.replace(/\\s+/g, ' ');
                            row.style.display = '';
                        } else {
                            if (inSingkawang) {
                                // Sudah melewati blok SINGKAWANG (masuk ke SINTANG, dll)
                                inSingkawang = false;
                            }
                            row.style.display = 'none';
                        }
                        continue;
                    }

                    // Jika bukan baris header Service Area:
                    // Cek apakah baris teknisi memiliki kolom pertama == SINGKAWANG
                    if (cells.length >= 6 && firstCellText === 'SINGKAWANG') {
                        inSingkawang = true;
                        row.style.display = '';
                        const sto = (cells[1]?.innerText || '').trim();
                        const nik = (cells[2]?.innerText || '').trim();
                        const nama = (cells[3]?.innerText || '').trim();
                        const absen = (cells[4]?.innerText || '').trim().toUpperCase();
                        const waktu = (cells[5]?.innerText || '').trim();
                        const status = (cells[6]?.innerText || cells[4]?.innerText || '').trim();
                        techList.push({ sto, nik, nama, absen, waktu, status });
                    } else if (inSingkawang) {
                        // Baris sub-header STO di dalam blok SINGKAWANG (BKY, PMK, SMB, SNW, TBA)
                        // Pastikan bukan baris teknisi dari SA lain
                        if (cells.length >= 6 && firstCellText !== 'SINGKAWANG' && firstCellText !== '') {
                            inSingkawang = false;
                            row.style.display = 'none';
                        } else {
                            row.style.display = '';
                        }
                    } else {
                        row.style.display = 'none';
                    }
                }

                // Sembunyikan sidebar kiri dan elemen di atas tabel agar screenshot fokus langsung ke tabel SINGKAWANG
                for (const sel of ['aside', 'header', 'nav']) {
                    document.querySelectorAll(sel).forEach(el => { el.style.display = 'none'; });
                }

                // Pastikan pembungkus tabel tidak memotong scroll (overflow)
                let parent = table.parentElement;
                while (parent && parent !== document.body) {
                    const style = window.getComputedStyle(parent);
                    if (style.overflowY === 'auto' || style.overflowY === 'scroll' || style.maxHeight !== 'none') {
                        parent.style.overflow = 'visible';
                        parent.style.maxHeight = 'none';
                        parent.style.height = 'auto';
                    }
                    parent = parent.parentElement;
                }

                table.scrollIntoView({ block: 'start' });
                return {
                    found: techList.length > 0,
                    headerSummary: skwHeaderSummary,
                    techList
                };
            }""")

            logger.info(f"Ekstraksi Singkawang selesai: found={skw_data.get('found')}, count={len(skw_data.get('techList', []))}")
            await page.wait_for_timeout(400)

            # Perbarui cookies dari browser context agar masa aktif sesi terus diperpanjang
            try:
                fresh_cookies = await context.cookies()
                if fresh_cookies:
                    state = _load_state()
                    state["cookies"] = [
                        {
                            "name": c["name"],
                            "value": c["value"],
                            "domain": c.get("domain", "takalbagbar.id"),
                            "path": c.get("path", "/mybranch"),
                            "secure": bool(c.get("secure", True)),
                        }
                        for c in fresh_cookies
                    ]
                    state["last_keepalive"] = datetime.now(timezone.utc).isoformat()
                    _save_state(state, sync_cloud=True)
            except Exception as ce:
                logger.warning(f"Gagal memperbarui cookies dari Playwright: {ce}")

            # Screenshot elemen tabel (atau container tabel) yang kini hanya berisi bagian SINGKAWANG
            table_elem = await page.query_selector("table") or await page.query_selector("main") or await page.query_selector("body")
            screenshot_bytes = await table_elem.screenshot()

            # Hitung statistik Singkawang untuk caption Telegram
            tech_list = skw_data.get("techList", [])
            total_tech = len(tech_list)
            masuk_count = sum(1 for t in tech_list if "MASUK" in t["absen"])
            belum_list = [t for t in tech_list if "BELUM" in t["absen"]]
            libur_count = sum(1 for t in tech_list if any(k in t["absen"] for k in ["LIBUR", "CUTI"]))
            sakit_count = sum(1 for t in tech_list if any(k in t["absen"] for k in ["SAKIT", "IZIN"]))
            tidak_tercatat_list = [
                t for t in tech_list
                if "MASUK" not in t["absen"]
                and "BELUM" not in t["absen"]
                and "LIBUR" not in t["absen"]
                and "CUTI" not in t["absen"]
                and "SAKIT" not in t["absen"]
                and "IZIN" not in t["absen"]
            ]

            pct_masuk = (masuk_count / total_tech * 100) if total_tech > 0 else 0.0
            tz_wib = timezone(timedelta(hours=7))
            now_wib = datetime.now(tz_wib)

            desc_lines = [
                "📋 *LAPORAN ABSEN ASSURANCE - SINGKAWANG*",
                f"📅 *Tanggal:* `{now_wib.strftime('%d-%m-%Y')}` | 🕒 *Pukul:* `{now_wib.strftime('%H:%M WIB')}`",
                "━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"👷 *Total Teknisi:* {total_tech} Orang",
                f"✅ *Masuk:* {masuk_count} ({pct_masuk:.1f}%)",
                f"🟠 *Belum Absen:* {len(belum_list)}",
                f"🌴 *Libur/Cuti:* {libur_count} | 🤒 *Sakit/Izin:* {sakit_count}",
                f"❌ *Tidak Tercatat:* {len(tidak_tercatat_list)}",
                "━━━━━━━━━━━━━━━━━━━━━━━━━",
            ]

            if belum_list:
                desc_lines.append("⚠️ *Belum Absen:*")
                for t in belum_list[:15]:
                    nama_label = t["nama"] or t["nik"]
                    desc_lines.append(f"• `[{t['sto']}]` {nama_label}")

            if tidak_tercatat_list:
                desc_lines.append("\n❌ *Tidak Tercatat:*")
                for t in tidak_tercatat_list[:5]:
                    nama_label = t["nama"] or t["nik"]
                    desc_lines.append(f"• `[{t['sto']}]` {nama_label}")

            desc_lines.append("\n_Tangkapan layar Absen Assurance (Singkawang) terlampir di atas._")
            caption = "\n".join(desc_lines)

            return screenshot_bytes, caption

        finally:
            await browser.close()


def _build_ukur_masal_xlsx(results: list[dict], summary: dict, now_wib: datetime) -> bytes:
    """
    Membangun file Excel (.xlsx) asli dengan kolom-kolom terpisah yang rapi,
    header berwarna biru tua, border tabel, lebar kolom otomatis, dan warna status ONU.
    """
    headers = [
        "NO",
        "ND",
        "STATUS ONU",
        "RX ONU (dBm)",
        "RX OLT (dBm)",
        "PORT (CALLING STATION ID)",
        "OLT",
        "SN ONT",
        "TIPE ONT",
        "JARAK (M)",
        "STATUS KONEKSI",
        "DURASI SESI",
        "IP ADDRESS",
        "LAST SEEN",
    ]
    col_widths = [6, 16, 14, 14, 14, 38, 12, 20, 20, 12, 16, 15, 16, 22]

    def _col_letter(col_idx_1based: int) -> str:
        res = ""
        n = col_idx_1based
        while n > 0:
            n, rem = divmod(n - 1, 26)
            res = chr(65 + rem) + res
        return res

    def _cell_xml(row_num: int, col_num: int, val: str, style_id: int = 1) -> str:
        ref = f"{_col_letter(col_num)}{row_num}"
        safe_val = xml_escape(str(val if val is not None else ""))
        return f'<c r="{ref}" t="inlineStr" s="{style_id}"><is><t>{safe_val}</t></is></c>'

    rows_xml = []

    # Baris 1: Header Tabel
    header_cells = [_cell_xml(1, c_idx, h_text, style_id=2) for c_idx, h_text in enumerate(headers, start=1)]
    rows_xml.append(f'<row r="1" ht="24" customHeight="1">{"".join(header_cells)}</row>')

    # Baris 2 dst: Data Hasil Ukur Masal
    for idx, item in enumerate(results, start=1):
        row_num = idx + 1
        onu_status = str(item.get("onu") or "-").strip()
        onu_upper = onu_status.upper()

        # Pisahkan SN ONT dan Tipe ONT
        sn_raw = str(item.get("sn_ont") or "-")
        sn_parts = [p.strip() for p in sn_raw.split("/") if p.strip()]
        sn_ont = sn_parts[0] if len(sn_parts) >= 1 else "-"
        tipe_ont = sn_parts[1] if len(sn_parts) >= 2 else "-"

        # Pisahkan Status Koneksi dan Durasi Sesi
        kon_raw = str(item.get("koneksi") or "-")
        kon_parts = [p.strip() for p in kon_raw.split("/") if p.strip()]
        status_koneksi = kon_parts[0] if len(kon_parts) >= 1 else "-"
        durasi_sesi = kon_parts[1] if len(kon_parts) >= 2 else "-"

        # Pilih warna sel untuk kolom STATUS ONU (3 = hijau ONLINE, 4 = merah LOS/OFFLINE, 1 = normal)
        if "ONLINE" in onu_upper:
            onu_style = 3
        elif any(k in onu_upper for k in ["LOS", "OFF", "DYING", "TIDAK"]):
            onu_style = 4
        else:
            onu_style = 1

        row_values = [
            (str(idx), 5),
            (item.get("nd") or "-", 1),
            (onu_status, onu_style),
            (item.get("rx_onu") or "-", 5),
            (item.get("rx_olt") or "-", 5),
            (item.get("port") or "-", 1),
            (item.get("olt") or "-", 5),
            (sn_ont, 1),
            (tipe_ont, 1),
            (item.get("jarak") or "-", 5),
            (status_koneksi, 5),
            (durasi_sesi, 5),
            (item.get("ip") or "-", 1),
            (item.get("last_seen") or "-", 5),
        ]

        cell_tags = [
            _cell_xml(row_num, c_idx, val, style_id=st_id)
            for c_idx, (val, st_id) in enumerate(row_values, start=1)
        ]
        rows_xml.append(f'<row r="{row_num}" ht="20" customHeight="1">{"".join(cell_tags)}</row>')

    cols_xml = "".join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(col_widths, start=1)
    )
    last_col = _col_letter(len(headers))
    last_row = max(1, len(results) + 1)

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_col}{last_row}"/>'
        '<sheetViews><sheetView tabSelected="1" workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>'
        f'<cols>{cols_xml}</cols>'
        f'<sheetData>{"".join(rows_xml)}</sheetData>'
        f'<autoFilter ref="A1:{last_col}{last_row}"/>'
        '</worksheet>'
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="4">'
        '<font><sz val="10"/><name val="Calibri"/><color rgb="FF1F2937"/></font>'
        '<font><b/><sz val="10"/><name val="Calibri"/><color rgb="FFFFFFFF"/></font>'
        '<font><b/><sz val="10"/><name val="Calibri"/><color rgb="FF107C41"/></font>'
        '<font><b/><sz val="10"/><name val="Calibri"/><color rgb="FFC00000"/></font>'
        '</fonts>'
        '<fills count="5">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE2EFDA"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFFCE4D6"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="2">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border>'
        '<left style="thin"><color rgb="FFD0D7DE"/></left>'
        '<right style="thin"><color rgb="FFD0D7DE"/></right>'
        '<top style="thin"><color rgb="FFD0D7DE"/></top>'
        '<bottom style="thin"><color rgb="FFD0D7DE"/></bottom>'
        '<diagonal/>'
        '</border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="6">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '</cellXfs>'
        '</styleSheet>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Hasil Ukur Masal" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )

    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )

    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", root_rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/styles.xml", styles_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


async def capture_ukur_masal(nd_list: list[str]) -> tuple[bytes, bytes, str]:
    """
    Membuka halaman https://takalbagbar.id/mybranch/apps/tickets/ukur-masal,
    memasukkan daftar ND ke kolom Daftar ND, menekan tombol Ukur ND,
    menunggu hasil pengukuran selesai, lalu mengambil screenshot bagian hasil di bawahnya
    beserta file Excel (.xlsx) dengan kolom-kolom terpisah yang rapi.
    Jika sesi belum login / kadaluarsa, melempar MyBranchAuthNeeded.
    """
    clean_nds = []
    seen = set()
    for item in nd_list:
        s = str(item).strip()
        if s and s not in seen:
            seen.add(s)
            clean_nds.append(s)
    if not clean_nds:
        raise ValueError("Daftar Nomor Internet (ND) kosong.")

    state = _load_state()
    cookies = state.get("cookies", [])
    if not cookies:
        raise MyBranchAuthNeeded("Sesi login belum tersedia.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1650, "height": 950},
            device_scale_factor=2,
            user_agent=USER_AGENT,
            color_scheme="dark",
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        await page.add_init_script("""() => {
            try {
                localStorage.setItem('theme', 'dark');
                document.documentElement.classList.remove('light');
                document.documentElement.classList.add('dark');
                document.documentElement.style.colorScheme = 'dark';
            } catch (e) {}
        }""")

        try:
            target_url = f"{config.MYBRANCH_URL}/apps/tickets/ukur-masal"
            logger.info(f"Membuka halaman Ukur Masal MyBranch ({len(clean_nds)} ND): {target_url}")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2500)

            current_url = page.url
            body_text = await page.inner_text("body")
            if "/login" in current_url or ("Sign In" in body_text and "Tactical" in body_text):
                logger.info("Sesi MyBranch belum login atau sudah kedaluwarsa saat mengakses Ukur Masal.")
                raise MyBranchAuthNeeded("Sesi login kedaluwarsa.")

            # Pastikan mode dark aktif
            await page.evaluate("""() => {
                document.documentElement.classList.remove('light');
                document.documentElement.classList.add('dark');
                document.documentElement.style.colorScheme = 'dark';
            }""")

            # Temukan textarea Daftar ND dan masukkan nomor internet
            textarea = await page.wait_for_selector("textarea", timeout=15000)
            await textarea.fill("\n".join(clean_nds))
            await page.wait_for_timeout(500)

            # Klik tombol "Ukur ... ND" di dalam card input pertama
            ukur_btn = await page.query_selector("div[data-slot='card'] button")
            if not ukur_btn:
                raise RuntimeError("Tombol 'Ukur ND' tidak ditemukan di halaman Ukur Masal.")

            await ukur_btn.click()

            # Tunggu hingga proses pengukuran seluruh ND selesai (maksimal 120 detik)
            expected_count = len(clean_nds)
            max_wait_sec = min(120, max(25, expected_count * 6))
            for step in range(max_wait_sec):
                await page.wait_for_timeout(1000)
                status_info = await page.evaluate("""() => {
                    const cards = document.querySelectorAll("div[data-slot='card']");
                    const firstBtn = cards.length > 0 ? cards[0].querySelector('button') : null;
                    const btnDisabled = firstBtn ? firstBtn.disabled : false;
                    const btnText = firstBtn ? (firstBtn.innerText || '').trim() : '';
                    const rows = document.querySelectorAll('table tbody tr');
                    return {
                        btnDisabled,
                        btnText,
                        rowCount: rows.length
                    };
                }""")
                if (
                    not status_info["btnDisabled"]
                    and status_info["rowCount"] >= expected_count
                    and step >= 1
                ):
                    break
                # Jika tombol sudah kembali aktif (tidak disabled) setelah beberapa detik meski sebagian ND tidak valid
                if not status_info["btnDisabled"] and status_info["rowCount"] > 0 and step >= 4:
                    break

            await page.wait_for_timeout(800)

            # Perbarui cookies sesi agar tetap awet
            try:
                fresh_cookies = await context.cookies()
                if fresh_cookies:
                    state = _load_state()
                    state["cookies"] = [
                        {
                            "name": c["name"],
                            "value": c["value"],
                            "domain": c.get("domain", "takalbagbar.id"),
                            "path": c.get("path", "/mybranch"),
                            "secure": bool(c.get("secure", True)),
                        }
                        for c in fresh_cookies
                    ]
                    state["last_keepalive"] = datetime.now(timezone.utc).isoformat()
                    _save_state(state, sync_cloud=True)
            except Exception as ce:
                logger.warning(f"Gagal memperbarui cookies seusai Ukur Masal: {ce}")

            # Ekstrak hasil dari tabel dan kartu ringkasan, sekaligus rapikan tampilan untuk screenshot hasil di bawahnya
            ukur_data = await page.evaluate("""() => {
                // Sembunyikan sidebar dan header atas agar screenshot fokus pada hasil
                const sidebar = document.querySelector('[data-slot="sidebar"]');
                if (sidebar) sidebar.style.display = 'none';
                const sidebarWrapper = document.querySelector('.xl\\\\:block.hidden');
                if (sidebarWrapper) sidebarWrapper.style.display = 'none';
                const topHeader = document.querySelector('header');
                if (topHeader) topHeader.style.display = 'none';
                const bodyWrapper = document.querySelector('.body-wrapper');
                if (bodyWrapper) {
                    bodyWrapper.style.marginLeft = '0';
                    bodyWrapper.style.width = '100%';
                }

                const container = document.querySelector('.container') || document.querySelector('main') || document.body;
                container.style.maxWidth = '100%';
                container.style.padding = '16px 24px';

                // Sembunyikan kartu input Daftar ND di atas agar fokus pada bagian hasil di bawahnya
                const cards = container.querySelectorAll("div[data-slot='card']");
                if (cards.length >= 2) {
                    cards[0].style.display = 'none';
                }

                // Buka overflow tabel agar semua kolom terlihat utuh tanpa terpotong scrollbar
                document.querySelectorAll('.overflow-x-auto').forEach(el => {
                    el.style.overflow = 'visible';
                });

                // Ambil ringkasan dari grid kartu
                const summary = {};
                const gridBoxes = container.querySelectorAll('.grid > div');
                gridBoxes.forEach(box => {
                    const lines = (box.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                    if (lines.length >= 2) {
                        summary[lines[0]] = lines.slice(1).join(' ');
                    }
                });

                // Ambil data baris tabel hasil ukur
                const rows = Array.from(container.querySelectorAll('table tbody tr'));
                const results = rows.map(tr => {
                    const cells = Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').trim().replace(/\\n+/g, ' / '));
                    return {
                        nd: cells[0] || '-',
                        onu: cells[1] || '-',
                        rx_onu: cells[2] || '-',
                        rx_olt: cells[3] || '-',
                        port: cells[4] || '-',
                        olt: cells[5] || '-',
                        sn_ont: cells[6] || '-',
                        jarak: cells[7] || '-',
                        koneksi: cells[8] || '-',
                        ip: cells[9] || '-',
                        last_seen: cells[10] || '-'
                    };
                });

                // Buat pembungkus khusus hasil ukur (kartu ringkasan + tabel hasil) untuk di-screenshot
                let resultTarget = container;
                if (cards.length >= 2) {
                    resultTarget = container;
                }
                return { summary, results };
            }""")

            await page.wait_for_timeout(300)

            target_elem = await page.query_selector(".container") or await page.query_selector("main") or await page.query_selector("body")
            screenshot_bytes = await target_elem.screenshot()

            tz_wib = timezone(timedelta(hours=7))
            now_wib = datetime.now(tz_wib)
            results = ukur_data.get("results", [])
            summary = ukur_data.get("summary", {})

            excel_bytes = _build_ukur_masal_xlsx(results, summary, now_wib)
            excel_filename = f"Hasil_Ukur_Masal_{now_wib.strftime('%d-%m-%Y_%H%M')}.xlsx"
            return screenshot_bytes, excel_bytes, excel_filename

        finally:
            await browser.close()

