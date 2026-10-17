import logging
import time
import json
import urllib.request
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import config

# Setup logger
logger = logging.getLogger(__name__)

class SheetsHandler:
    def __init__(self):
        self.client = None
        self.sheet = None
        self.worksheet = None
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

    def connect(self):
        """Menghubungkan ke Google Sheets API menggunakan file Service Account Credentials."""
        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            creds = Credentials.from_service_account_file(config.GOOGLE_CREDENTIALS_FILE, scopes=scopes)
            self.client = gspread.authorize(creds)
            
            # Buka Spreadsheet menggunakan ID
            self.sheet = self.client.open_by_key(config.GOOGLE_SHEET_ID)
            
            # Gunakan worksheet pertama (index 0)
            self.worksheet = self.sheet.get_worksheet(0)
            logger.info(f"Berhasil terhubung ke Google Sheet: {self.sheet.title}")
            return True
        except Exception as e:
            logger.error(f"Gagal terhubung ke Google Sheets: {e}")
            return False

    def _ensure_connection(self):
        """Memastikan koneksi aktif sebelum melakukan operasi."""
        if not self.worksheet:
            return self.connect()
        return True

    def get_all_wos(self):
        """Mengambil seluruh data Work Order (WO) beserta nomor barisnya."""
        if not self._ensure_connection():
            raise ConnectionError("Gagal terhubung ke database Google Sheets.")
        
        try:
            # Ambil semua baris data
            rows = self.worksheet.get_all_values()
            if not rows:
                return []
            
            headers = [h.strip() for h in rows[0]]
            wos = []
            
            # Mulai dari baris ke-2 (indeks 1)
            for idx, row in enumerate(rows[1:], start=2):
                # Buat dictionary data dan sesuaikan panjang kolom jika baris tidak lengkap
                row_dict = {}
                for col_idx, header in enumerate(headers):
                    val = row[col_idx] if col_idx < len(row) else ""
                    row_dict[header] = val.strip()
                
                # Tambahkan metadata nomor baris untuk mempermudah update
                row_dict["_row_num"] = idx
                wos.append(row_dict)
                
            return wos
        except Exception as e:
            logger.error(f"Gagal mengambil data WO: {e}")
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

    def get_sa_mempawah_report(self, jml_teknisi=None, kendala=None):
        """
        Menghitung laporan SA MEMPAWAH dari 4 sheet target:
        - INSERA 22 (INSERA22)
        - sta
        - UNSPEC MPW
        - UNSPEC STA
        
        Format output persis sesuai gambar referensi:
        🏢 SA MEMPAWAH
        ___________________
        🕒 LAPORAN JAM : 12.00

        👷 Jml Teknisi : 
        🎫 Saldo tiket & unspec : 173 Order
        ✅ Close tiket & unspec : 24 Order
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
            # 1. Prioritaskan pembacaan langsung dari Google Sheets via gspread
            if self._ensure_connection():
                try:
                    all_worksheets = self.sheet.worksheets()
                    target_map = {
                        "insera": ["insera 22", "insera22", "insera"],
                        "sta": ["sta"],
                        "unspec_mpw": ["unspec mpw", "undsepc mpw", "unspac mpw"],
                        "unspec_sta": ["unspec sta", "undsepc sta", "unspac sta"]
                    }

                    matched_ws = {}
                    for ws in all_worksheets:
                        title_clean = ws.title.strip().lower()
                        for group_key, aliases in target_map.items():
                            if group_key not in matched_ws:
                                for alias in aliases:
                                    if title_clean == alias or (alias in title_clean and "urgent" not in title_clean and "close" not in title_clean):
                                        matched_ws[group_key] = ws
                                        break

                    if len(matched_ws) >= 2:
                        for g_key, ws in matched_ws.items():
                            rows = ws.get_all_values()
                            total_rows = max(0, len(rows) - 1)
                            close_rows = 0
                            if rows and len(rows) > 1:
                                headers = [str(h).strip().upper() for h in rows[0]]
                                status_idx = -1
                                for candidate in ["STATUS", "GROUPING", "STATE"]:
                                    if candidate in headers:
                                        status_idx = headers.index(candidate)
                                        break
                                
                                if status_idx != -1:
                                    for r in rows[1:]:
                                        if len(r) > status_idx:
                                            st = r[status_idx].strip().upper()
                                            if st in ["CLOSE", "CLOSED", "COMPLETE PS", "BERHENTI BERLANGGANAN", "SELESAI"]:
                                                close_rows += 1
                                                
                            sheet_stats[ws.title] = {"total": total_rows, "close": close_rows}
                            grand_total += total_rows
                            grand_close += close_rows
                        
                        fetched_via_gspread = True
                        logger.info(f"Berhasil membaca data SA Mempawah via gspread: Total={grand_total}, Close={grand_close}")
                except Exception as e:
                    logger.warning(f"Gagal membaca sheet via gspread: {e}. Beralih ke fallback Web App.")

            # 2. Fallback ke Web App jika gspread terkendala
            if not fetched_via_gspread and config.WEB_APP_URL:
                try:
                    url = f"{config.WEB_APP_URL}?action=read"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        res_json = json.loads(resp.read().decode("utf-8"))
                        data_items = res_json.get("data", [])

                    sheet_groups = {
                        "INSERA 22": [],
                        "sta": [],
                        "UNSPEC MPW": [],
                        "UNSPEC STA": []
                    }

                    for it in data_items:
                        src = (it.get("SHEET_SOURCE") or "").strip()
                        src_lower = src.lower().replace(" ", "")
                        
                        if "insera22" in src_lower or src_lower == "insera22":
                            sheet_groups["INSERA 22"].append(it)
                        elif src_lower == "sta":
                            sheet_groups["sta"].append(it)
                        elif ("unspec" in src_lower or "undsepc" in src_lower or "unspac" in src_lower) and "mpw" in src_lower:
                            sheet_groups["UNSPEC MPW"].append(it)
                        elif ("unspec" in src_lower or "undsepc" in src_lower or "unspac" in src_lower) and "sta" in src_lower:
                            sheet_groups["UNSPEC STA"].append(it)

                    sheet_stats = {}
                    grand_total = 0
                    grand_close = 0
                    for s_name, items in sheet_groups.items():
                        t_count = len(items)
                        c_count = 0
                        for it in items:
                            st = (it.get("STATUS") or "").strip().upper()
                            gp = (it.get("Grouping") or "").strip().upper()
                            if st in ["CLOSE", "CLOSED", "COMPLETE PS", "BERHENTI BERLANGGANAN", "SELESAI"] or gp == "CLOSE":
                                c_count += 1
                        sheet_stats[s_name] = {"total": t_count, "close": c_count}
                        grand_total += t_count
                        grand_close += c_count

                    logger.info(f"Berhasil membaca data SA Mempawah via Web App: Total={grand_total}, Close={grand_close}")
                except Exception as e:
                    logger.error(f"Gagal mengambil data dari Web App: {e}")

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
            "🏢 SA MEMPAWAH\n"
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

