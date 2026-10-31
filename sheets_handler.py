import logging
import time
import json
import csv
import io
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import config

# Setup logger
logger = logging.getLogger(__name__)

DEFAULT_DATANEW_APPS_SCRIPT = "https://script.google.com/macros/s/AKfycbwsleRWZLr24S6AT-cSG0CstUeSl8xIA3ssnSsZ-Hf3c50ep_ko07G-qxMrs35yqUoAvQ/exec"


class SheetsHandler:
    def __init__(self):
        self.client = None
        self.sheet = None
        self.worksheet = None
        self.apps_script_connected = False
        self.gemini_configured = False
        self._sa_cache = None
        self._sa_cache_time = 0
        
        # Inisialisasi Gemini AI jika API key tersedia
        if config.GEMINI_API_KEY:
            try:
                genai.configure(api_key=config.GEMINI_API_KEY)
                self.gemini_configured = True
                logger.info("Gemini AI berhasil dikonfigurasi.")
            except Exception as e:
                logger.error(f"Gagal mengonfigurasi Gemini AI: {e}")

    def _get_datanew_callback_url(self) -> str:
        base_url = (config.WEB_APP_URL or DEFAULT_DATANEW_APPS_SCRIPT).strip()
        if "AKfycbwsleRWZLr24S6AT" not in base_url:
            base_url = DEFAULT_DATANEW_APPS_SCRIPT
        if base_url.endswith("/exec"):
            return base_url[:-5] + "/callback?nocache_id=1"
        return base_url

    def _call_datanew_apps_script(self, func_name: str, args_list: list):
        """Memanggil fungsi Apps Script (getInitialData / getDetail) yang terhubung ke sheet DATANEW."""
        url = self._get_datanew_callback_url()
        payload = json.dumps([func_name, json.dumps(args_list), None, [0], None, None, 1, 0])
        data = urllib.parse.urlencode({"request": payload}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                "X-Same-Domain": "1",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_text = resp.read().decode("utf-8")
        lines = raw_text.split("\n", 1)
        body = lines[1] if len(lines) > 1 else lines[0]
        parsed = json.loads(body)
        return json.loads(parsed[0][1][1])

    def _find_target_worksheet(self, sh):
        """Mencari worksheet sesuai config.SHEET_NAME (default: DATANEW)."""
        target_name = (config.SHEET_NAME or "DATANEW").strip()
        try:
            return sh.worksheet(target_name)
        except Exception:
            for ws in sh.worksheets():
                if ws.title.strip().lower() == target_name.lower():
                    return ws
            return sh.get_worksheet(0)

    def connect(self):
        """Menghubungkan ke Google Sheets API (prioritas gspread, fallback ke Apps Script DATANEW)."""
        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            creds = Credentials.from_service_account_file(config.GOOGLE_CREDENTIALS_FILE, scopes=scopes)
            self.client = gspread.authorize(creds)
            
            # Buka Spreadsheet menggunakan ID
            self.sheet = self.client.open_by_key(config.GOOGLE_SHEET_ID)
            self.worksheet = self._find_target_worksheet(self.sheet)
            logger.info(f"Berhasil terhubung ke Google Sheet: {self.sheet.title} (Sheet: {self.worksheet.title})")
            return True
        except Exception as e:
            logger.warning(f"Koneksi langsung gspread ke {config.GOOGLE_SHEET_ID} belum diizinkan ({e}). Menggunakan jalur Apps Script DATANEW...")
            try:
                init_data = self._call_datanew_apps_script("getInitialData", [])
                if init_data and "dashboard" in init_data:
                    self.apps_script_connected = True
                    logger.info(f"Berhasil terhubung ke sheet {config.SHEET_NAME} via Apps Script (Tanggal: {init_data.get('selectedDate')}).")
                    return True
            except Exception as e2:
                logger.error(f"Gagal terhubung ke Google Sheets maupun Apps Script DATANEW: {e2}")
            return False

    def _ensure_connection(self):
        """Memastikan koneksi aktif sebelum melakukan operasi."""
        if not self.worksheet and not self.apps_script_connected:
            return self.connect()
        return True

    @staticmethod
    def _normalize_datanew_row(raw_dict: dict, row_num: int) -> dict:
        """Menyelaraskan kolom sheet DATANEW ke format internal WO bot."""
        upper_map = {str(k).strip().upper(): str(v).strip() for k, v in raw_dict.items() if k is not None}

        wo_id = (
            raw_dict.get("WO ID")
            or raw_dict.get("incident")
            or upper_map.get("INCIDENT")
            or upper_map.get("NO INCIDENT")
            or upper_map.get("WO ID")
            or ""
        ).strip()
        tanggal = (
            raw_dict.get("Tanggal")
            or raw_dict.get("tanggal")
            or upper_map.get("TANGGAL")
            or upper_map.get("REPORTED DATE")
            or ""
        ).strip()
        teknisi = (
            raw_dict.get("Teknisi")
            or raw_dict.get("teknisi")
            or upper_map.get("TEKNISI")
            or upper_map.get("TEAM")
            or ""
        ).strip()
        workzone = (raw_dict.get("workzone") or upper_map.get("WORKZONE") or "").strip()
        sektor = (raw_dict.get("sektor") or upper_map.get("SEKTOR") or "").strip()
        cust_type = (raw_dict.get("customerType") or upper_map.get("CUSTOMER TYPE") or upper_map.get("CUST TYPE") or "").strip()
        pelanggan = (
            raw_dict.get("Pelanggan")
            or " / ".join([p for p in [workzone, sektor, cust_type] if p and p != "-"])
            or "-"
        )
        symptom = (raw_dict.get("symptom") or upper_map.get("SYMPTOM") or "").strip()
        red_st = (raw_dict.get("red") or upper_map.get("RED") or "").strip()
        pekerjaan = (
            raw_dict.get("Pekerjaan")
            or " - ".join([p for p in [symptom, red_st] if p and p != "-"])
            or "-"
        )
        close_flag = (
            raw_dict.get("close")
            or upper_map.get("CLOSE/UPDATE")
            or upper_map.get("CLOSE")
            or upper_map.get("STATUS")
            or ""
        ).strip().upper()

        if close_flag in ["CLOSE", "CLOSED", "COMPLETE PS", "SELESAI", "BERHENTI BERLANGGANAN"]:
            status_norm = "Selesai"
            berkas_norm = "Lengkap"
        elif close_flag == "UPDATE":
            status_norm = "Dalam Proses"
            berkas_norm = "Belum Lengkap"
        elif raw_dict.get("Status") in ["Pending", "Dalam Proses", "Selesai", "Tertagih"]:
            status_norm = raw_dict["Status"]
            berkas_norm = raw_dict.get("Kelengkapan Berkas", "")
        else:
            status_norm = "Pending"
            berkas_norm = "Belum Lengkap"

        keterangan = (
            raw_dict.get("Keterangan")
            or raw_dict.get("perbaikan")
            or upper_map.get("PERBAIKAN")
            or upper_map.get("KETERANGAN")
            or ""
        ).strip()

        normalized = dict(raw_dict)
        normalized.update({
            "WO ID": wo_id,
            "Tanggal": tanggal,
            "Teknisi": teknisi,
            "Pelanggan": pelanggan,
            "Pekerjaan": pekerjaan,
            "Status": status_norm,
            "Keterangan": keterangan,
            "Kelengkapan Berkas": berkas_norm,
            "_close_raw": close_flag or "OPEN",
            "_symptom_raw": symptom or "REGULER",
            "_workzone_raw": workzone or "SNW",
            "_row_num": row_num,
        })
        return normalized

    def get_all_wos(self):
        """Mengambil seluruh data Work Order (WO) dari sheet DATANEW beserta nomor barisnya."""
        if not self._ensure_connection():
            raise ConnectionError("Gagal terhubung ke database Google Sheets (DATANEW).")
        
        # 1. Jika terhubung langsung via gspread
        if self.worksheet is not None:
            try:
                rows = self.worksheet.get_all_values()
                if not rows:
                    return []
                
                headers = [h.strip() for h in rows[0]]
                wos = []
                
                for idx, row in enumerate(rows[1:], start=2):
                    if not any(cell.strip() for cell in row):
                        continue
                    row_dict = {}
                    for col_idx, header in enumerate(headers):
                        if not header:
                            continue
                        val = row[col_idx] if col_idx < len(row) else ""
                        row_dict[header] = val.strip()
                    
                    wos.append(self._normalize_datanew_row(row_dict, idx))
                    
                return wos
            except Exception as e:
                logger.warning(f"Gagal mengambil data WO via gspread: {e}. Mencoba jalur Apps Script DATANEW...")

        # 2. Fallback via Apps Script DATANEW
        try:
            init_data = self._call_datanew_apps_script("getInitialData", [])
            selected_date = init_data.get("selectedDate", "")
            detail_data = self._call_datanew_apps_script("getDetail", [selected_date, {}])
            detail_rows = detail_data.get("rows", [])
            wos = []
            for idx, r in enumerate(detail_rows, start=2):
                r_num = int(r.get("rowNumber") or idx)
                wos.append(self._normalize_datanew_row(r, r_num))
            return wos
        except Exception as e:
            logger.error(f"Gagal mengambil data WO dari DATANEW: {e}")
            return []

    def get_wo_by_id(self, wo_id):
        """Mencari data Work Order berdasarkan WO ID."""
        wos = self.get_all_wos()
        for wo in wos:
            # Bandingkan secara case-insensitive dan bersihkan spasi
            if wo.get("WO ID", "").strip().lower() == wo_id.strip().lower():
                return wo
        return None

    def update_wo_status(self, row_num, new_status):
        """Mengupdate status pada kolom Status (Kolom F - 6) berdasarkan nomor baris."""
        if not self._ensure_connection():
            return False
        try:
            # Kolom F adalah kolom ke-6
            self.worksheet.update_cell(row_num, 6, new_status)
            logger.info(f"Berhasil mengupdate baris {row_num} ke status: {new_status}")
            return True
        except Exception as e:
            logger.error(f"Gagal mengupdate status di baris {row_num}: {e}")
            return False

    def update_wo_remarks(self, row_num, remarks):
        """Mengupdate kolom Keterangan (Kolom G - 7) berdasarkan nomor baris."""
        if not self._ensure_connection():
            return False
        try:
            # Kolom G adalah kolom ke-7
            self.worksheet.update_cell(row_num, 7, remarks)
            logger.info(f"Berhasil mengupdate keterangan di baris {row_num}")
            return True
        except Exception as e:
            logger.error(f"Gagal mengupdate keterangan di baris {row_num}: {e}")
            return False

    def update_wo_documents(self, row_num, doc_status):
        """Mengupdate kolom Kelengkapan Berkas (Kolom H - 8) berdasarkan nomor baris."""
        if not self._ensure_connection():
            return False
        try:
            # Kolom H adalah kolom ke-8
            self.worksheet.update_cell(row_num, 8, doc_status)
            logger.info(f"Berhasil mengupdate kelengkapan berkas di baris {row_num}")
            return True
        except Exception as e:
            logger.error(f"Gagal mengupdate kelengkapan berkas di baris {row_num}: {e}")
            return False

    def add_new_wo(self, wo_id, tanggal, teknisi, pelanggan, pekerjaan, status="Pending", keterangan="", berkas=""):
        """Menambahkan baris baru (Work Order baru) ke Google Sheet."""
        if not self._ensure_connection():
            return False
        try:
            # Format baris baru sesuai dengan kolom
            # A: WO ID, B: Tanggal, C: Teknisi, D: Pelanggan, E: Pekerjaan, F: Status, G: Keterangan, H: Kelengkapan Berkas
            new_row = [wo_id, tanggal, teknisi, pelanggan, pekerjaan, status, keterangan, berkas]
            self.worksheet.append_row(new_row)
            logger.info(f"Berhasil menambahkan WO baru: {wo_id}")
            return True
        except Exception as e:
            logger.error(f"Gagal menambahkan WO baru: {e}")
            return False

    def get_unique_technicians(self):
        """Mengambil daftar nama teknisi unik yang ada di kolom C (Teknisi)."""
        wos = self.get_all_wos()
        technicians = set()
        for wo in wos:
            name = wo.get("Teknisi", "").strip()
            if name:
                technicians.add(name)
        
        # Gabungkan dengan default teknisi agar lengkap
        all_techs = list(technicians.union(set(config.DEFAULT_TECHNICIANS)))
        return sorted(all_techs)

    def generate_billing_reminder(self, wo_details):
        """
        Menggunakan Gemini AI untuk membuat draf pesan penagihan/kelengkapan berkas
        berdasarkan data Work Order yang dikirim.
        """
        if not self.gemini_configured:
            return "Koneksi Gemini AI belum terkonfigurasi. Silakan periksa kunci API Anda."

        # Ekstrak detail WO
        wo_id = wo_details.get("WO ID", "-")
        teknisi = wo_details.get("Teknisi", "-")
        pelanggan = wo_details.get("Pelanggan", "-")
        pekerjaan = wo_details.get("Pekerjaan", "-")
        keterangan = wo_details.get("Keterangan", "-")
        berkas_status = wo_details.get("Kelengkapan Berkas", "-")

        prompt = f"""
        Anda adalah seorang Asisten Operasional/Koordinator Lapangan (Korlap) profesional bernama MPWOK.
        Tugas Anda adalah merumuskan pesan penagihan berkas yang ramah, sopan, namun tegas dalam bahasa Indonesia.
        Pesan ini ditujukan untuk mengingatkan Teknisi Lapangan agar segera melengkapi dokumen/berkas yang kurang untuk kebutuhan penagihan.

        Gunakan data Work Order berikut untuk merumuskan pesan:
        - Nomor WO: {wo_id}
        - Nama Teknisi: {teknisi}
        - Nama Pelanggan/Customer: {pelanggan}
        - Pekerjaan: {pekerjaan}
        - Catatan Pekerjaan: {keterangan}
        - Status Kelengkapan Berkas saat ini: {berkas_status}

        Format pesan yang dihasilkan harus siap kirim melalui WhatsApp atau Telegram, memiliki salam pembuka, detail kekurangan berkas yang jelas, dan penutup yang sopan. Sertakan emoji yang relevan agar pesan terlihat ramah dan profesional.
        """

        models_to_try = ["gemini-3.8-flash", "gemini-flash-latest"]
        for m_name in models_to_try:
            try:
                model = genai.GenerativeModel(m_name)
                response = model.generate_content(prompt, request_options={"timeout": 10})
                return response.text.strip()
            except Exception as e:
                logger.warning(f"Percobaan model {m_name} gagal: {e}")
                if "429" in str(e) or "ResourceExhausted" in str(e) or "Quota exceeded" in str(e):
                    return "⚠️ Kuota Gemini AI sedang mencapai batas sementara (Rate Limit / Quota Exceeded). Silakan coba lagi sebentar lagi."
                continue
        return "⚠️ Layanan Gemini AI sedang tidak dapat diakses saat ini."

    def get_wo_summary(self):
        """Menghitung ringkasan statistik Work Order (WO)."""
        wos = self.get_all_wos()
        total = len(wos)
        pending = 0
        in_progress = 0
        selesai = 0
        tertagih = 0
        pending_docs = 0

        for wo in wos:
            st = wo.get("Status", "").strip().lower()
            doc = wo.get("Kelengkapan Berkas", "").strip().lower()

            if st == "pending":
                pending += 1
            elif st == "dalam proses":
                in_progress += 1
            elif st == "selesai":
                selesai += 1
                if not doc or "lengkap" not in doc:
                    pending_docs += 1
            elif st == "tertagih":
                tertagih += 1

        return {
            "total": total,
            "pending": pending,
            "in_progress": in_progress,
            "selesai": selesai,
            "tertagih": tertagih,
            "pending_docs": pending_docs
        }

    def get_singkawang_report(self, jml_teknisi=None, kendala=None):
        """
        Menghitung laporan SINGKAWANG dari sheet DATANEW
        pada spreadsheet 1PrM2fyGxEWFno6WDFZVigO771yELsU3zRyrPfzO-8oQ.
        
        Format output:
        🏢 SINGKAWANG
        ___________________
        🕒 LAPORAN JAM : 12.00

        👷 Jml Teknisi : 
        🎫 Saldo tiket & unspec : 58 Order
        ✅ Close tiket & unspec : 45 Order
        📊 Produktifitas : 

        • 
        """
        current_time = time.time()
        sheet_stats = {}
        grand_total = 0
        grand_close = 0

        # Caching 15 detik untuk menghindari rate limit API
        if self._sa_cache and (current_time - self._sa_cache_time) < 15:
            sheet_stats = self._sa_cache.get("sheet_stats", {})
            grand_total = self._sa_cache.get("saldo", 0)
            grand_close = self._sa_cache.get("close", 0)
        else:
            fetched_via_gspread = False
            target_sheet_name = (config.SHEET_NAME or "DATANEW").strip()

            # 1. Prioritaskan pembacaan langsung dari worksheet DATANEW via gspread
            if self._ensure_connection() and self.worksheet is not None:
                try:
                    ws = self._find_target_worksheet(self.sheet) if self.sheet else self.worksheet
                    rows = ws.get_all_values()
                    if rows and len(rows) > 1:
                        headers = [str(h).strip().upper() for h in rows[0]]
                        close_idx = -1
                        for candidate in ["CLOSE/UPDATE", "CLOSE", "STATUS", "GROUPING", "STATE"]:
                            if candidate in headers:
                                close_idx = headers.index(candidate)
                                break
                        symptom_idx = headers.index("SYMPTOM") if "SYMPTOM" in headers else -1

                        reg_total, reg_close = 0, 0
                        unspec_total, unspec_close = 0, 0

                        for r in rows[1:]:
                            if not any(c.strip() for c in r):
                                continue
                            grand_total += 1
                            is_closed = False
                            if close_idx != -1 and len(r) > close_idx:
                                st = r[close_idx].strip().upper()
                                if st in ["CLOSE", "CLOSED", "COMPLETE PS", "BERHENTI BERLANGGANAN", "SELESAI"]:
                                    is_closed = True
                                    grand_close += 1

                            sym = r[symptom_idx].strip().upper() if (symptom_idx != -1 and len(r) > symptom_idx) else ""
                            if "UNSPEC" in sym:
                                unspec_total += 1
                                if is_closed:
                                    unspec_close += 1
                            else:
                                reg_total += 1
                                if is_closed:
                                    reg_close += 1

                        sheet_stats = {
                            f"{ws.title} (Reguler/Tiket)": {"total": reg_total, "close": reg_close},
                            f"{ws.title} (Unspec)": {"total": unspec_total, "close": unspec_close},
                        }
                        fetched_via_gspread = True
                        logger.info(f"Berhasil membaca data {ws.title} via gspread: Total={grand_total}, Close={grand_close}")
                except Exception as e:
                    logger.warning(f"Gagal membaca sheet {target_sheet_name} via gspread: {e}. Beralih ke Apps Script DATANEW.")

            # 2. Pembacaan via Apps Script yang terhubung langsung ke sheet DATANEW
            if not fetched_via_gspread:
                try:
                    init_data = self._call_datanew_apps_script("getInitialData", [])
                    dash = init_data.get("dashboard", {})
                    summary = dash.get("summary", {})
                    grand_total = int(summary.get("total") or 0)
                    grand_close = int(summary.get("close") or 0)

                    reg_total, reg_close = 0, 0
                    unspec_total, unspec_close = 0, 0
                    manual_total, manual_close = 0, 0
                    for tech in dash.get("technicians", []):
                        c_obj = tech.get("close") or {}
                        o_obj = tech.get("open") or {}
                        rc = int(c_obj.get("reguler") or 0)
                        ro = int(o_obj.get("reguler") or 0)
                        uc = int(c_obj.get("unspec") or 0)
                        uo = int(o_obj.get("unspec") or 0)
                        mc = int(c_obj.get("manual") or 0)
                        mo = int(o_obj.get("manual") or 0)
                        reg_close += rc
                        reg_total += rc + ro
                        unspec_close += uc
                        unspec_total += uc + uo
                        manual_close += mc
                        manual_total += mc + mo

                    sheet_stats = {
                        f"{target_sheet_name} (Reguler)": {"total": reg_total, "close": reg_close},
                        f"{target_sheet_name} (Unspec)": {"total": unspec_total, "close": unspec_close},
                    }
                    if manual_total > 0:
                        sheet_stats[f"{target_sheet_name} (Manual)"] = {"total": manual_total, "close": manual_close}

                    logger.info(f"Berhasil membaca data {target_sheet_name} via Apps Script: Total={grand_total}, Close={grand_close}")
                except Exception as e:
                    logger.error(f"Gagal mengambil data laporan dari {target_sheet_name}: {e}")

            # Simpan ke cache
            self._sa_cache = {
                "sheet_stats": sheet_stats,
                "saldo": grand_total,
                "close": grand_close
            }
            self._sa_cache_time = current_time

        # Format Waktu Indonesia Barat (WIB / UTC+7)
        tz_wib = timezone(timedelta(hours=7))
        now_wib = datetime.now(tz_wib)
        jam_str = now_wib.strftime("%H.%M")

        # Format Jml Teknisi (Default: Kosongkan)
        tek_str = ""
        if jml_teknisi:
            clean_tek = str(jml_teknisi).strip().lower().replace("org", "").strip()
            if clean_tek:
                tek_str = f"{clean_tek} Org"

        # Format Produktifitas (Default: Kosongkan)
        prod_str = ""
        if tek_str:
            try:
                tek_num = float(tek_str.replace("Org", "").strip())
                if tek_num > 0:
                    prod_val = grand_close / tek_num
                    prod_str = f"{prod_val:.2f}".replace(".", ",")
            except (ValueError, ZeroDivisionError):
                prod_str = ""

        # Format Kendala (Default: Kosongkan)
        if kendala and str(kendala).strip():
            kendala_str = f"• {str(kendala).strip()}"
        else:
            kendala_str = "• "

        # Format pesan teks persis sesuai gambar yang dikirim user
        message_text = (
            "🏢 SINGKAWANG\n"
            "___________________\n"
            f"🕒 LAPORAN JAM : {jam_str}\n\n"
            f"👷 Jml Teknisi : {tek_str}\n"
            f"🎫 Saldo tiket & unspec : {grand_total} Order\n"
            f"✅ Close tiket & unspec : {grand_close} Order\n"
            f"📊 Produktifitas : {prod_str}\n\n"
            f"{kendala_str}"
        )

        return {
            "saldo": grand_total,
            "close": grand_close,
            "jam": jam_str,
            "sheet_stats": sheet_stats,
            "text": message_text
        }

    # Alias untuk kompatibilitas
    get_sa_mempawah_report = get_singkawang_report

