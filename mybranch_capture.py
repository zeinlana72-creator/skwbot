import asyncio
import base64
import http.cookiejar
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
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
    _save_state(state)


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

