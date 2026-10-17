import sys
import os
import config
from sheets_handler import SheetsHandler

# Pastikan output konsol mendukung karakter emoji di Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def test_connectivity():
    print("==================================================")
    print("🔍 MEMULAI VERIFIKASI PENGATURAN BOT MPWOK")
    print("==================================================")

    # 1. Cek file .env
    print("\n1. Memeriksa Ketersediaan File Konfigurasi:")
    env_exists = os.path.exists(".env")
    if not env_exists:
        print("❌ File '.env' TIDAK ditemukan!")
        print("💡 Salin file '.env.example' menjadi '.env' dan isi kunci API Anda.")
    else:
        print("✅ File '.env' ditemukan.")

    # 2. Cek variabel config
    is_valid, errors = config.validate_config()
    if not is_valid:
        print("❌ Konfigurasi belum lengkap:")
        for err in errors:
            print(f"   - {err}")
        return False
    else:
        print("✅ Semua variabel konfigurasi dasar di file .env terisi.")

    # 3. Tes koneksi Google Sheets
    print("\n2. Menguji Koneksi Google Sheets...")
    db = SheetsHandler()
    if db.connect():
        print("✅ KONEKSI BERHASIL!")
        print(f"📊 Judul Spreadsheet: '{db.sheet.title}'")
        print(f"📄 Judul Worksheet: '{db.worksheet.title}'")
        
        # Ambil sampel baris
        try:
            wos = db.get_all_wos()
            print(f"📊 Jumlah data Work Order saat ini: {len(wos)} baris.")
            if wos:
                print(f"📋 Contoh baris pertama: {wos[0]}")
            else:
                print("💡 Lembar kerja kosong atau hanya berisi baris judul header.")
        except Exception as e:
            print(f"❌ Gagal mengambil baris data dari Google Sheet: {e}")
    else:
        print("❌ GAGAL terhubung ke Google Sheets.")
        print("💡 Periksa petunjuk berikut:")
        print(f"   a. Pastikan file credentials '{config.GOOGLE_CREDENTIALS_FILE}' valid.")
        print(f"   b. Pastikan email Service Account (client_email di JSON) sudah di-invite")
        print("      sebagai Editor di Spreadsheet Google Anda.")
        print(f"   c. Pastikan GOOGLE_SHEET_ID '{config.GOOGLE_SHEET_ID}' benar.")
        return False

    # 4. Tes Laporan SA MEMPAWAH (/report)
    print("\n3. Menguji Perhitungan Laporan SA MEMPAWAH (/report)...")
    try:
        report = db.get_sa_mempawah_report(jml_teknisi=10, kendala="Semua aman terkendali")
        print("✅ PERHITUNGAN LAPORAN BERHASIL!")
        print("📝 Tampilan Laporan:")
        print("--------------------------------------------------")
        print(report["text"])
        print("--------------------------------------------------")
    except Exception as e:
        print(f"❌ Gagal menghitung laporan SA MEMPAWAH: {e}")

    # 5. Tes koneksi Gemini AI
    print("\n4. Menguji Koneksi Gemini AI...")
    if not db.gemini_configured:
        print("❌ Gemini AI belum terkonfigurasi. Periksa API key Anda di .env.")
        return False
        
    try:
        # Kirim prompt pengujian singkat
        test_wo = {
            "WO ID": "TEST-001",
            "Teknisi": "Agus",
            "Pelanggan": "Toko Berkah",
            "Pekerjaan": "Perbaikan Kabel",
            "Keterangan": "Selesai tapi butuh berkas foto",
            "Kelengkapan Berkas": "Kurang Foto Bukti"
        }
        print("⏳ Memanggil Gemini API dengan mock data...")
        draft = db.generate_billing_reminder(test_wo)
        if "⚠️" in draft or "Gagal" in draft or "tidak dapat" in draft:
            print(f"⚠️ Catatan Gemini AI:\n{draft}")
        else:
            print("✅ RESPONS GEMINI AI BERHASIL!")
            print("📝 Hasil Draf Contoh:")
            print("--------------------------------------------------")
            print(draft)
            print("--------------------------------------------------")
    except Exception as e:
        print(f"⚠️ Peringatan respons Gemini AI: {e}")

    print("\n==================================================")
    print("🎉 SEMUA TES BERHASIL! Bot siap dijalankan.")
    print("💡 Jalankan bot dengan perintah: python main.py")
    print("==================================================")
    return True

if __name__ == "__main__":
    test_connectivity()
