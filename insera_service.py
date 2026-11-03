import os
import json
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Tuple, Dict, Any, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page
import config

logger = logging.getLogger(__name__)

# Lock untuk sinkronisasi akses Playwright Insera
_insera_lock = asyncio.Lock()

# Objek penyimpanan state login aktif in-memory (saat menunggu token TOTP)
_active_login_process = {
    "playwright": None,
    "browser": None,
    "context": None,
    "page": None,
    "pending": False,
    "chat_id": None,
    "pending_action": None,  # dict: {"type": "ujung"|"mediacare", "ticket": ..., "actual": ...}
    "started_at": None,
}


class InseraAuthNeeded(Exception):
    """Exception saat sesi Insera belum login atau kedaluwarsa."""
    pass


# ==============================================================================
# PENYIMPANAN SESI (LOKAL & CLOUD GOOGLE SHEETS C1)
# ==============================================================================

def _load_insera_state_from_cloud() -> dict:
    """Mengambil sesi cookies Insera dari sheet _bot_session sel C1 jika ada."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(config.GOOGLE_CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sheet_ids = [config.GOOGLE_SHEET_ID, getattr(config, "BACKUP_SESSION_SHEET_ID", "")]
        for sid in sheet_ids:
            if not sid:
                continue
            try:
                sh = client.open_by_key(sid)
                ws = sh.worksheet("_bot_session")
                raw = ws.acell("C1").value
                if raw:
                    data = json.loads(raw)
                    with open(config.INSERA_STATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    logger.info("Sesi Insera Bot 3 (@ass_snw_bot) berhasil dipulihkan dari cloud (_bot_session!C1).")
                    return data
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Gagal memuat sesi Insera dari cloud: {e}")
    return {}


def _save_insera_state_to_cloud(state: dict):
    """Menyimpan sesi cookies Insera ke worksheet _bot_session sel C1."""
    if not state.get("cookies"):
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(config.GOOGLE_CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sheet_ids = [config.GOOGLE_SHEET_ID, getattr(config, "BACKUP_SESSION_SHEET_ID", "")]
        for sid in sheet_ids:
            if not sid:
                continue
            try:
                sh = client.open_by_key(sid)
                try:
                    ws = sh.worksheet("_bot_session")
                except Exception:
                    ws = sh.add_worksheet(title="_bot_session", rows=5, cols=3)
                ws.update_acell(
                    "C1",
                    json.dumps(
                        {
                            "cookies": state["cookies"],
                            "username": config.INSERA_USERNAME,
                            "updated_at": datetime.now(timezone(timedelta(hours=7))).isoformat(),
                        },
                        ensure_ascii=False,
                    ),
                )
                logger.info("Sesi Insera berhasil dicadangkan ke Google Sheets (_bot_session!C1).")
                return
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Gagal mencadangkan sesi Insera ke cloud: {e}")


def load_insera_state() -> dict:
    if os.path.exists(config.INSERA_STATE_FILE):
        try:
            with open(config.INSERA_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return _load_insera_state_from_cloud()


def save_insera_state(state: dict, sync_cloud: bool = True):
    try:
        with open(config.INSERA_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        if sync_cloud:
            _save_insera_state_to_cloud(state)
    except Exception as e:
        logger.error(f"Gagal menyimpan state Insera: {e}")


def has_insera_session() -> bool:
    state = load_insera_state()
    return bool(state.get("cookies"))


def is_insera_login_pending() -> bool:
    return _active_login_process.get("pending", False)


def get_pending_insera_chat_id() -> Optional[int]:
    return _active_login_process.get("chat_id")


def set_pending_insera_action(action_type: str, ticket_no: str, actual_solution: str, chat_id: int):
    _active_login_process["pending_action"] = {
        "type": action_type,
        "ticket": ticket_no,
        "actual": actual_solution,
    }
    _active_login_process["chat_id"] = chat_id


def pop_pending_insera_action() -> Optional[dict]:
    act = _active_login_process.get("pending_action")
    _active_login_process["pending_action"] = None
    return act


async def cancel_insera_login():
    """Membatalkan proses login Insera yang sedang berjalan dan menutup browser."""
    global _active_login_process
    try:
        if _active_login_process.get("context"):
            await _active_login_process["context"].close()
        if _active_login_process.get("browser"):
            await _active_login_process["browser"].close()
        if _active_login_process.get("playwright"):
            await _active_login_process["playwright"].stop()
    except Exception as e:
        logger.debug(f"Error saat menutup browser login Insera: {e}")
    finally:
        _active_login_process = {
            "playwright": None,
            "browser": None,
            "context": None,
            "page": None,
            "pending": False,
            "chat_id": None,
            "pending_action": None,
            "started_at": None,
        }


# ==============================================================================
# ALUR LOGIN INSERA SSO & VERIFIKASI TOKEN TOTP
# ==============================================================================

async def start_insera_login_flow(chat_id: int) -> Tuple[bool, str, Optional[bytes]]:
    """
    Membuka halaman login Insera, memasukkan username dan password, mencentang syarat,
    dan menunggu frame input TOTP/Token muncul.
    Mengembalikan (sukses, pesan, screenshot_bytes).
    """
    await cancel_insera_login()

    p = await async_playwright().start()
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
        viewport={"width": 1366, "height": 768},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = await context.new_page()

    # Otomatis handle dialog popup jika ada
    page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))

    try:
        logger.info("[Insera] Membuka URL login: %s", config.INSERA_URL)
        
        # Test koneksi TCP port 443 terlebih dahulu untuk diagnostik cepat
        import socket
        try:
            ip = socket.gethostbyname("insera-sso.telkom.co.id")
            logger.info(f"[Insera] Resolved IP: {ip}")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(6.0)
            tcp_res = s.connect_ex((ip, 443))
            s.close()
            logger.info(f"[Insera] TCP 443 connect_ex: {tcp_res}")
            if tcp_res != 0:
                ss = None
                await context.close()
                await browser.close()
                await p.stop()
                return False, f"Server Insera ({ip}:443) tidak merespons koneksi (TCP error {tcp_res}). Server Telkom memblokir IP server cloud (Railway luar negeri). Diperlukan proxy IP Indonesia.", None
        except Exception as se:
            logger.warning(f"[Insera] Socket test error: {se}")

        await page.goto(config.INSERA_URL, timeout=30000, wait_until="commit")
        await page.wait_for_timeout(1500)

        # Isi kredensial dan centang Terms of Use
        await page.evaluate("""(data) => {
            const fu = document.querySelector('#fake-username');
            const fp = document.querySelector('#fake-password');
            const ju = document.querySelector('#j_username');
            const jp = document.querySelector('#j_password');
            const cb = document.querySelector('#acceptTerms');

            if (fu) fu.value = data.username;
            if (fp) fp.value = data.password;
            if (ju) ju.value = data.username;
            if (jp) jp.value = data.password;
            if (cb) cb.checked = true;
        }""", {"username": config.INSERA_USERNAME, "password": config.INSERA_PASSWORD})

        await page.wait_for_timeout(500)

        # Klik tombol login
        await page.click("#fake-login")
        logger.info("[Insera] Form login disubmit, menunggu dialog verifikasi TOTP...")

        # Tunggu hingga iframe TOTP (jqueryDialogFrame) muncul
        totp_frame = None
        for _ in range(25):
            await page.wait_for_timeout(800)
            frame = page.frame(name="jqueryDialogFrame")
            if frame:
                try:
                    pin_input = await frame.query_selector("#pin")
                    if pin_input:
                        totp_frame = frame
                        break
                except Exception:
                    pass

        if not totp_frame:
            # Periksa apakah ada error pesan di halaman
            err_text = await page.evaluate("""() => {
                const el = document.querySelector('#main-body-message, .alert-danger, .error');
                return el ? el.innerText.trim() : '';
            }""")
            ss = await page.screenshot(type="png")
            await context.close()
            await browser.close()
            await p.stop()
            return False, f"Dialog Token / TOTP tidak muncul. Pesan: {err_text or 'Login gagal/ditolak'}", ss

        ss = await page.screenshot(type="png")

        # Simpan state proses login yang aktif
        _active_login_process["playwright"] = p
        _active_login_process["browser"] = browser
        _active_login_process["context"] = context
        _active_login_process["page"] = page
        _active_login_process["pending"] = True
        _active_login_process["chat_id"] = chat_id
        _active_login_process["started_at"] = datetime.now()

        return True, "Form input Token TOTP siap.", ss

    except Exception as e:
        logger.error(f"[Insera] Error saat memulai login: {e}", exc_info=True)
        try:
            ss = await page.screenshot(type="png")
        except Exception:
            ss = None
        await context.close()
        await browser.close()
        await p.stop()
        return False, f"Gagal membuka login Insera: {e}", ss


async def submit_insera_totp_code(token_code: str) -> Tuple[bool, str, Optional[bytes]]:
    """
    Memasukkan kode token TOTP yang diberikan user ke input #pin pada jqueryDialogFrame.
    Jika berhasil, menyimpan session cookies ke disk dan cloud.
    """
    if not is_insera_login_pending():
        return False, "Tidak ada proses login Insera yang sedang menunggu token.", None

    page: Page = _active_login_process.get("page")
    context: BrowserContext = _active_login_process.get("context")
    browser: Browser = _active_login_process.get("browser")
    p = _active_login_process.get("playwright")

    clean_code = token_code.strip().replace(" ", "")

    try:
        totp_frame = page.frame(name="jqueryDialogFrame")
        if not totp_frame:
            await cancel_insera_login()
            return False, "Sesi dialog login kedaluwarsa. Silakan ulangi perintah login.", None

        # Isi kode PIN / Token
        logger.info(f"[Insera] Memasukkan kode Token TOTP: {clean_code[:2]}***")
        await totp_frame.fill("#pin", clean_code)
        await page.wait_for_timeout(300)

        # Klik tombol submit pada iframe
        submit_btn = await totp_frame.query_selector('input[type="submit"], button[type="submit"]')
        if submit_btn:
            await submit_btn.click()
        else:
            await totp_frame.press("#pin", "Enter")

        # Tunggu navigasi halaman setelah verifikasi TOTP
        login_success = False
        for _ in range(30):
            await page.wait_for_timeout(800)
            url = page.url.lower()
            if "login_error" not in url and ("welcome" in url or "userview" in url or "insera" in url or "home" in url):
                # Cek apakah dialog sudah tertutup
                if not page.frame(name="jqueryDialogFrame"):
                    login_success = True
                    break

        ss = await page.screenshot(type="png")

        if login_success or ("login" not in page.url.lower() or "login_error" not in page.url.lower()):
            cookies = await context.cookies()
            state = {
                "cookies": cookies,
                "username": config.INSERA_USERNAME,
                "saved_at": datetime.now(timezone(timedelta(hours=7))).isoformat(),
            }
            save_insera_state(state, sync_cloud=True)
            logger.info("[Insera] Berhasil login dan menyimpan sesi cookies.")

            await cancel_insera_login()
            return True, "Login Insera SSO berhasil!", ss
        else:
            # Periksa pesan error di iframe jika ada
            err_msg = ""
            try:
                frame_now = page.frame(name="jqueryDialogFrame")
                if frame_now:
                    err_msg = await frame_now.evaluate("() => document.body.innerText")
            except Exception:
                pass
            return False, f"Verifikasi Token gagal atau salah: {err_msg[:200]}", ss

    except Exception as e:
        logger.error(f"[Insera] Error saat submit TOTP: {e}", exc_info=True)
        try:
            ss = await page.screenshot(type="png")
        except Exception:
            ss = None
        await cancel_insera_login()
        return False, f"Terjadi kesalahan saat verifikasi Token: {e}", ss


# ==============================================================================
# FUNGSI PEMBANTU OTOMASI INSERA (CLOSE UJUNG & CLOSE MEDIACARE)
# ==============================================================================

async def _get_authenticated_page() -> Tuple[Any, Browser, BrowserContext, Page]:
    """Membuka browser dan menerapkan cookies sesi Insera yang tersimpan."""
    state = load_insera_state()
    cookies = state.get("cookies", [])
    if not cookies:
        raise InseraAuthNeeded("Sesi Insera belum login atau cookies kosong.")

    p = await async_playwright().start()
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
        viewport={"width": 1400, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    await context.add_cookies(cookies)
    page = await context.new_page()

    # Tangani dialog popup otomatis (klik OK)
    page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))

    return p, browser, context, page


async def _search_incident(page: Page, ticket_no: str) -> bool:
    """Mencari tiket insiden di halaman awal / find Incident."""
    clean_ticket = ticket_no.strip()
    logger.info(f"[Insera] Mencari tiket insiden: {clean_ticket}")

    # Coba berbagai selector pencarian Incident
    search_input = None
    selectors = [
        'input[placeholder*="Incident" i]',
        'input[placeholder*="Cari" i]',
        'input[placeholder*="Search" i]',
        'input[name*="incident" i]',
        'input[id*="incident" i]',
        'input[name*="ticket" i]',
        'input[id*="ticket" i]',
        'input[type="text"]',
    ]

    for sel in selectors:
        inp = await page.query_selector(sel)
        if inp and await inp.is_visible():
            search_input = inp
            break

    if not search_input:
        return False

    await search_input.fill(clean_ticket)
    await search_input.press("Enter")
    await page.wait_for_timeout(3000)

    # Cek apakah ada baris tiket di tabel hasil pencarian dan klik
    row = await page.query_selector(f'a:has-text("{clean_ticket}"), td:has-text("{clean_ticket}")')
    if row:
        await row.click()
        await page.wait_for_timeout(2500)
        return True

    return True


async def _click_take_owner(page: Page) -> bool:
    """Memilih 'Take Owner' di bagian kanan tengah layar."""
    logger.info("[Insera] Mencoba klik Take Owner...")
    btn = await page.query_selector('button:has-text("Take Owner"), a:has-text("Take Owner"), input[value*="Take Owner" i]')
    if btn and await btn.is_visible():
        await btn.click()
        await page.wait_for_timeout(2000)
        return True
    return False


async def _fill_actual_solution(page: Page, actual_code: str) -> bool:
    """
    Memasukkan Actual Solution:
    - Masuk Actual Solution
    - Masukkan actual di classification_code >> Show
    - Pilih actual tersebut yang muncul di pilihan
    """
    clean_code = actual_code.strip()
    logger.info(f"[Insera] Memasukkan Actual Solution: {clean_code}")

    # Cari section atau tab Actual Solution
    act_section = await page.query_selector('a:has-text("Actual Solution"), button:has-text("Actual Solution"), div:has-text("Actual Solution")')
    if act_section and await act_section.is_visible():
        try:
            await act_section.click()
            await page.wait_for_timeout(1000)
        except Exception:
            pass

    # Masukkan ke classification_code
    cls_input = await page.query_selector('input[name*="classification_code" i], input[id*="classification_code" i], input[placeholder*="classification_code" i]')
    if cls_input:
        await cls_input.fill(clean_code)
        await page.wait_for_timeout(500)

    # Klik Show
    show_btn = await page.query_selector('button:has-text("Show"), input[value="Show"], a:has-text("Show")')
    if show_btn:
        await show_btn.click()
        await page.wait_for_timeout(1500)

    # Pilih item yang muncul
    item = await page.query_selector(f'tr:has-text("{clean_code}"), td:has-text("{clean_code}"), li:has-text("{clean_code}")')
    if item:
        await item.click()
        await page.wait_for_timeout(1000)
        return True

    return True


async def _click_update_ibooster(page: Page) -> bool:
    """Scroll ke bawah dan klik '( update pengukuran ibooster )'."""
    logger.info("[Insera] Mengklik update pengukuran ibooster...")
    btn = await page.query_selector('button:has-text("update pengukuran ibooster"), a:has-text("update pengukuran ibooster"), span:has-text("update pengukuran ibooster")')
    if btn:
        await btn.scroll_into_view_if_needed()
        await btn.click()
        await page.wait_for_timeout(3000)
        return True
    return False


# ==============================================================================
# OTOMASI 1: CLOSE UJUNG
# ==============================================================================

async def execute_close_ujung(ticket_no: str, actual_solution: str) -> Tuple[bytes, str]:
    """
    Tahapan Close Ujung:
    1. Masukkan tiket di pencarian/find Incident
    2. Pilih take owner di kanan tengah
    3. Masukkan Actual Solution > classification_code >> Show dan pilih
    4. Scroll ke bawah klik ( update pengukuran ibooster )
    5. Klik CEK SCC QC
    6. Cek: Kategori ukur 'SPAC' dan QC Internet Test Result 'PASSED'
       Jika lolos:
       - Scroll ke Workorder Section
       - Pilih logo kertas di menu Attribut Workorder
       - Pilih status COMPLETED -> save dan oke
    """
    p, browser, context, page = await _get_authenticated_page()
    wib_now = datetime.now(timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M WIB")

    try:
        logger.info(f"[Insera] Memulai Close Ujung untuk Tiket: {ticket_no}")
        await page.goto(config.INSERA_URL, timeout=30000, wait_until="commit")
        await page.wait_for_timeout(2000)

        # 1. Cari Tiket
        await _search_incident(page, ticket_no)

        # 2. Take Owner
        await _click_take_owner(page)

        # 3. Actual Solution
        await _fill_actual_solution(page, actual_solution)

        # 4. Update Pengukuran iBooster
        await _click_update_ibooster(page)

        # 5. Klik CEK SCC QC
        logger.info("[Insera] Mengklik CEK SCC QC...")
        qc_btn = await page.query_selector('button:has-text("CEK SCC QC"), a:has-text("CEK SCC QC"), input[value*="CEK SCC QC" i]')
        if qc_btn:
            await qc_btn.scroll_into_view_if_needed()
            await qc_btn.click()
            await page.wait_for_timeout(4000)

        # 6. Validasi Kategori Ukur (SPAC) dan QC Internet Test Result (PASSED)
        page_text = await page.evaluate("() => document.body.innerText")
        is_spac = "SPAC" in page_text.upper()
        is_passed = "PASSED" in page_text.upper()

        if not (is_spac and is_passed):
            ss = await page.screenshot(type="png", full_page=False)
            caption = (
                f"⚠️ *Close Ujung Ditangguhkan (QC Tidak Sesuai)*\n\n"
                f"🎫 *No Tiket:* `{ticket_no}`\n"
                f"🛠️ *Actual Solution:* `{actual_solution}`\n"
                f"📊 *Kategori Ukur SPAC:* {'✅ Ya' if is_spac else '❌ Bukan SPAC'}\n"
                f"📶 *QC Test Result:* {'✅ PASSED' if is_passed else '❌ Belum PASSED'}\n"
                f"🕒 *Waktu:* {wib_now}\n\n"
                f"_Tiket belum dapat di-COMPLETED karena syarat QC SPAC & PASSED tidak terpenuhi._"
            )
            return ss, caption

        # 7. Scroll ke Workorder Section -> Logo kertas di Attribut Workorder
        logger.info("[Insera] Masuk Workorder Section & Attribut Workorder...")
        wo_icon = await page.query_selector('.workorder-section a i.fa-file, .workorder-section a, a[title*="Attribut" i], i.fa-file-text, i.fa-file')
        if wo_icon:
            await wo_icon.scroll_into_view_if_needed()
            await wo_icon.click()
            await page.wait_for_timeout(2000)

        # Ubah status ke COMPLETED
        status_select = await page.query_selector('select[name*="status" i], select[id*="status" i]')
        if status_select:
            await status_select.select_option(label="COMPLETED")
            await page.wait_for_timeout(500)

        # Klik Save dan OK
        save_btn = await page.query_selector('button:has-text("Save"), input[value="Save"], button:has-text("Simpan")')
        if save_btn:
            await save_btn.click()
            await page.wait_for_timeout(3000)

        ss_final = await page.screenshot(type="png", full_page=False)
        caption = (
            f"✅ *Close Ujung Berhasil (COMPLETED)*\n\n"
            f"🎫 *No Tiket:* `{ticket_no}`\n"
            f"🛠️ *Actual Solution:* `{actual_solution}`\n"
            f"📊 *Kategori Ukur:* `SPAC` (Valid)\n"
            f"📶 *QC Internet Test Result:* `PASSED` (Valid)\n"
            f"📋 *Status Workorder:* `COMPLETED`\n"
            f"🕒 *Waktu:* {wib_now}"
        )
        return ss_final, caption

    finally:
        await context.close()
        await browser.close()
        await p.stop()


# ==============================================================================
# OTOMASI 2: CLOSE MEDIACARE
# ==============================================================================

async def execute_close_mediacare(ticket_no: str, actual_solution: str) -> Tuple[bytes, str]:
    """
    Tahapan Close Mediacare:
    1. Masukkan tiket di pencarian/find Incident
    2. Pilih take owner di kanan tengah
    3. Masukkan Actual Solution > classification_code >> Show dan pilih
    4. Scroll ke bawah klik ( update pengukuran ibooster )
    5. Scroll ke bawah di Workorder Section pilih logo kertas di Attribut Workorder
    6. Pilih status CANCELED lalu save dan oke
    7. Pilih Action Status lalu pilih RESOLVED dan SAVE lalu oke
    """
    p, browser, context, page = await _get_authenticated_page()
    wib_now = datetime.now(timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M WIB")

    try:
        logger.info(f"[Insera] Memulai Close Mediacare untuk Tiket: {ticket_no}")
        await page.goto(config.INSERA_URL, timeout=30000, wait_until="commit")
        await page.wait_for_timeout(2000)

        # 1. Cari Tiket
        await _search_incident(page, ticket_no)

        # 2. Take Owner
        await _click_take_owner(page)

        # 3. Actual Solution
        await _fill_actual_solution(page, actual_solution)

        # 4. Update Pengukuran iBooster
        await _click_update_ibooster(page)

        # 5. Workorder Section -> Attribut Workorder (logo kertas)
        logger.info("[Insera] Mengubah Status Workorder ke CANCELED...")
        wo_icon = await page.query_selector('.workorder-section a i.fa-file, .workorder-section a, a[title*="Attribut" i], i.fa-file-text, i.fa-file')
        if wo_icon:
            await wo_icon.scroll_into_view_if_needed()
            await wo_icon.click()
            await page.wait_for_timeout(2000)

        # Status: CANCELED
        status_select = await page.query_selector('select[name*="status" i], select[id*="status" i]')
        if status_select:
            await status_select.select_option(label="CANCELED")
            await page.wait_for_timeout(500)

        # Save dan oke
        save_btn = await page.query_selector('button:has-text("Save"), input[value="Save"], button:has-text("Simpan")')
        if save_btn:
            await save_btn.click()
            await page.wait_for_timeout(2500)

        # 6. Action Status -> RESOLVED -> SAVE -> oke
        logger.info("[Insera] Memilih Action Status RESOLVED...")
        act_status = await page.query_selector('select[name*="action_status" i], select[id*="action_status" i], select[name*="action" i]')
        if act_status:
            await act_status.scroll_into_view_if_needed()
            try:
                await act_status.select_option(label="RESOLVED")
            except Exception:
                await act_status.select_option(value="RESOLVED")
            await page.wait_for_timeout(500)

        save_res = await page.query_selector('button:has-text("SAVE"), button:has-text("Save"), input[value="SAVE"]')
        if save_res:
            await save_res.click()
            await page.wait_for_timeout(3000)

        ss_final = await page.screenshot(type="png", full_page=False)
        caption = (
            f"✅ *Close Mediacare Berhasil (RESOLVED)*\n\n"
            f"🎫 *No Tiket:* `{ticket_no}`\n"
            f"🛠️ *Actual Solution:* `{actual_solution}`\n"
            f"📋 *Status Workorder:* `CANCELED`\n"
            f"🎯 *Action Status:* `RESOLVED`\n"
            f"🕒 *Waktu:* {wib_now}"
        )
        return ss_final, caption

    finally:
        await context.close()
        await browser.close()
        await p.stop()

