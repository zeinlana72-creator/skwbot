import logging
import asyncio
import io
import sys
from datetime import datetime, time, timezone, timedelta

# Pastikan output konsol mendukung karakter emoji (khususnya di Windows)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    TypeHandler,
    ApplicationHandlerStop,
    filters
)
import config
from sheets_handler import SheetsHandler
from dashboard_capture import capture_productivity_dashboard

# Konfigurasi Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Inisialisasi Sheets Handler
db = SheetsHandler()

# Definisi State untuk Percakapan (ConversationHandler)
(
    STATE_UPDATE_SELECT_WO,
    STATE_UPDATE_SELECT_STATUS,
    STATE_ADD_ID,
    STATE_ADD_TECH,
    STATE_ADD_CLIENT,
    STATE_ADD_JOB,
    STATE_GEMINI_SELECT_WO,
    STATE_MANUAL_INPUT_WO
) = range(8)

# Helper untuk memeriksa izin akses user
def is_authorized(user_id: int) -> bool:
    if not config.ALLOWED_TELEGRAM_IDS:
        # Jika tidak dikonfigurasi, semua orang diizinkan (untuk testing/kemudahan)
        return True
    return user_id in config.ALLOWED_TELEGRAM_IDS

async def block_group_interactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Memastikan anggota grup TARGET_GROUP_ID (-1001342185601)
    tidak dapat melakukan perintah apapun di dalam grup.
    """
    chat = update.effective_chat
    if chat and chat.id == config.TARGET_GROUP_ID:
        # Jika ada perintah teks (seperti /start, /report, /dashboard, dll) dari anggota grup
        if update.message and update.message.text and update.message.text.startswith("/"):
            raise ApplicationHandlerStop
        # Jika ada interaksi tombol inline di grup
        if update.callback_query:
            await update.callback_query.answer("Perintah dinonaktifkan di grup ini.", show_alert=False)
            raise ApplicationHandlerStop

# --- FUNGSI UTAMA / COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan Menu Utama."""
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("Maaf, Anda tidak memiliki izin untuk menggunakan bot ini. Hubungi Administrator.")
        return

    welcome_text = (
        f"👋 Halo *{user.first_name}*!\n\n"
        f"Saya adalah *SINGKAWANG BOT*, Asisten Operasional & Korlap Anda.\n"
        f"Saya terhubung ke Google Sheets untuk memantau data pekerjaan teknisi lapangan.\n\n"
        f"Pilih menu di bawah ini atau ketik `/report` untuk memulai:"
    )

    keyboard = [
        [
            InlineKeyboardButton("📊 Lihat WO Aktif", callback_data="menu_list_wo"),
            InlineKeyboardButton("📝 Tambah WO Baru", callback_data="menu_add_wo")
        ],
        [
            InlineKeyboardButton("✏️ Update Status WO", callback_data="menu_update_wo"),
            InlineKeyboardButton("🤖 Tagihan Gemini AI", callback_data="menu_gemini_wo")
        ],
        [
            InlineKeyboardButton("🏢 Laporan SINGKAWANG (/report)", callback_data="menu_singkawang")
        ],
        [
            InlineKeyboardButton("📸 Dashboard Produktivitas (/dashboard)", callback_data="menu_dashboard")
        ],
        [
            InlineKeyboardButton("⏰ Cek Berkas Pending", callback_data="menu_check_docs")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Membatalkan alur percakapan aktif."""
    await update.message.reply_text("❌ Aksi dibatalkan. Kembali ke menu utama.")
    # Reset user_data
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END

# --- ALUR: LIHAT WO AKTIF ---

async def handle_list_wo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("⏳ Mengambil data dari Google Sheets...")
    
    # Ambil data WO secara asinkron agar tidak memblokir event loop
    wos = await asyncio.to_thread(db.get_all_wos)
    
    if not wos:
        keyboard = [[InlineKeyboardButton("⬅️ Kembali", callback_data="menu_back")]]
        await query.edit_message_text("⚠️ Data Work Order kosong atau gagal mengambil data.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Filter WO yang statusnya bukan "Tertagih" (WO Aktif)
    active_wos = [wo for wo in wos if wo.get("Status", "").lower() != "tertagih"]
    
    if not active_wos:
        keyboard = [[InlineKeyboardButton("⬅️ Kembali", callback_data="menu_back")]]
        await query.edit_message_text("✅ Semua Work Order telah diselesaikan dan tertagih! Tidak ada WO aktif.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    message_text = "📋 *Daftar Work Order (WO) Aktif:*\n\n"
    for wo in active_wos[:15]: # Batasi 15 item agar tidak kepanjangan di chat
        wo_id = wo.get("WO ID", "-")
        teknisi = wo.get("Teknisi", "-")
        pelanggan = wo.get("Pelanggan", "-")
        pekerjaan = wo.get("Pekerjaan", "-")
        status = wo.get("Status", "Pending")
        
        emoji = "⏳"
        if status == "Dalam Proses":
            emoji = "⚙️"
        elif status == "Selesai":
            emoji = "✅"
            
        message_text += (
            f"{emoji} *{wo_id}* - {pelanggan}\n"
            f"👤 Teknisi: {teknisi}\n"
            f"🛠️ Kerja: {pekerjaan}\n"
            f"📌 Status: {status}\n\n"
        )
        
    if len(active_wos) > 15:
        message_text += f"_...dan {len(active_wos) - 15} WO aktif lainnya di Google Sheet._"

    keyboard = [[InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu_back")]]
    await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# --- ALUR: CEK BERKAS PENDING ---

async def handle_check_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("⏳ Memeriksa kelengkapan berkas...")
    wos = await asyncio.to_thread(db.get_all_wos)
    
    # Cari WO yang statusnya "Selesai" tapi dokumen belum lengkap (tidak sama dengan "lengkap")
    pending_docs = []
    for wo in wos:
        status = wo.get("Status", "").strip().lower()
        berkas = wo.get("Kelengkapan Berkas", "").strip().lower()
        
        if status == "selesai" and (not berkas or "lengkap" not in berkas):
            pending_docs.append(wo)
            
    if not pending_docs:
        keyboard = [[InlineKeyboardButton("⬅️ Kembali", callback_data="menu_back")]]
        await query.edit_message_text("🎉 Hebat! Tidak ada berkas tertunda untuk pekerjaan yang selesai.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    message_text = "⏰ *Daftar Pekerjaan Selesai dengan Berkas Tertunda:*\n\n"
    for wo in pending_docs:
        message_text += (
            f"📄 *{wo.get('WO ID', '-')}* - {wo.get('Pelanggan', '-')}\n"
            f"👤 Teknisi: {wo.get('Teknisi', '-')}\n"
            f"⚠️ Berkas: {wo.get('Kelengkapan Berkas', 'Belum lengkap')}\n\n"
        )
        
    keyboard = [
        [InlineKeyboardButton("🤖 Buat Tagihan (Gemini)", callback_data="menu_gemini_wo")],
        [InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu_back")]
    ]
    await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# --- ALUR: LAPORAN & REKAP OPERASIONAL (/report, /rekap, /singkawang, dll) ---

async def show_report_help(update: Update):
    """Menampilkan panduan format penggunaan perintah /report."""
    help_text = (
        "💡 *Panduan Perintah Laporan:* `/report`\n\n"
        "Perintah ini dapat digunakan secara langsung maupun dengan argumen opsional:\n\n"
        "1. `/report`\n"
        "   Mengambil dan menampilkan laporan ringkas SINGKAWANG saat ini.\n\n"
        "2. `/report [jml_teknisi]`\n"
        "   Contoh: `/report 10`\n"
        "   Otomatis menghitung produktifitas (Close Order ÷ Jml Teknisi).\n\n"
        "3. `/report [jml_teknisi] [kendala...]`\n"
        "   Contoh: `/report 12 Hujan deras & ODP padam`\n"
        "   Menyertakan jumlah teknisi serta catatan kendala operasional.\n\n"
        "📌 *Alias Perintah:* Anda juga bisa mengetik `/rekap`, `/laporan`, `/reaport`, atau `/singkawang`."
    )
    keyboard = [
        [InlineKeyboardButton("🏢 Buat Laporan Sekarang", callback_data="menu_singkawang")],
        [InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu_back")]
    ]
    if update.message:
        await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani perintah /report, /reaport, /laporan, /rekap, /singkawang."""
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("Maaf, Anda tidak memiliki izin untuk menggunakan bot ini.")
        return

    # Ambil argumen opsional jika ada: /report [jml_teknisi] [kendala...]
    jml_teknisi = None
    kendala = None
    if context.args:
        first_arg = context.args[0].lower()
        if first_arg in ["help", "bantuan", "?"]:
            await show_report_help(update)
            return

        jml_teknisi = context.args[0]
        if len(context.args) > 1:
            kendala = " ".join(context.args[1:])

    # Kirim status memproses data
    loading_msg = await update.message.reply_text("⏳ Sedang menghitung laporan SINGKAWANG dari sheet target...")

    try:
        report = await asyncio.to_thread(db.get_singkawang_report, jml_teknisi, kendala)
        keyboard = [
            [
                InlineKeyboardButton("📊 Rincian Per Sheet", callback_data="singkawang_detail"),
                InlineKeyboardButton("🔄 Refresh", callback_data="singkawang_refresh")
            ],
            [
                InlineKeyboardButton("📋 Ringkasan WO", callback_data="report_wo_summary"),
                InlineKeyboardButton("ℹ️ Format /report", callback_data="report_help_info")
            ],
            [
                InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu_back")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await loading_msg.edit_text(report["text"], reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Gagal memproses laporan: {e}")
        await loading_msg.edit_text(f"❌ Terjadi kesalahan saat membaca data laporan:\n{e}")

# Alias untuk kompatibilitas
handle_singkawang = handle_report
handle_sa_mempawah = handle_report

async def handle_menu_singkawang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani klik tombol menu Laporan SINGKAWANG."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text("⏳ Sedang menghitung laporan SINGKAWANG...")
    try:
        report = await asyncio.to_thread(db.get_singkawang_report)
        keyboard = [
            [
                InlineKeyboardButton("📊 Rincian Per Sheet", callback_data="singkawang_detail"),
                InlineKeyboardButton("🔄 Refresh", callback_data="singkawang_refresh")
            ],
            [
                InlineKeyboardButton("📋 Ringkasan WO", callback_data="report_wo_summary"),
                InlineKeyboardButton("ℹ️ Format /report", callback_data="report_help_info")
            ],
            [
                InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu_back")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(report["text"], reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Gagal memproses laporan: {e}")
        keyboard = [[InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu_back")]]
        await query.edit_message_text(f"❌ Terjadi kesalahan saat membaca data laporan:\n{e}", reply_markup=InlineKeyboardMarkup(keyboard))

# Alias untuk callback lama
handle_menu_sa_mempawah = handle_menu_singkawang

async def handle_singkawang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani tombol refresh, rincian per sheet, dan ringkasan pada laporan SINGKAWANG."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data in ["singkawang_refresh", "sa_mempawah_refresh"]:
        # Paksa ambil data baru
        db._sa_cache = None
        db._sa_cache_time = 0
        report = await asyncio.to_thread(db.get_singkawang_report)
        keyboard = [
            [
                InlineKeyboardButton("📊 Rincian Per Sheet", callback_data="singkawang_detail"),
                InlineKeyboardButton("🔄 Refresh", callback_data="singkawang_refresh")
            ],
            [
                InlineKeyboardButton("📋 Ringkasan WO", callback_data="report_wo_summary"),
                InlineKeyboardButton("ℹ️ Format /report", callback_data="report_help_info")
            ],
            [
                InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu_back")
            ]
        ]
        await query.edit_message_text(report["text"], reply_markup=InlineKeyboardMarkup(keyboard))

    elif data in ["singkawang_detail", "sa_mempawah_detail"]:
        report = await asyncio.to_thread(db.get_singkawang_report)
        sheet_stats = report.get("sheet_stats", {})
        
        detail_text = "📋 *Rincian Sheet SINGKAWANG:*\n\n"
        for s_name, stats in sheet_stats.items():
            tot = stats.get("total", 0)
            cls = stats.get("close", 0)
            opn = tot - cls
            detail_text += f"• *{s_name}*\n  Saldo: {tot} | Close: {cls} | Open: {opn}\n\n"
            
        detail_text += f"📈 *Grand Total:* {report['saldo']} Order (Close: {report['close']} Order)"
        
        keyboard = [
            [
                InlineKeyboardButton("⬅️ Kembali ke Laporan", callback_data="singkawang_refresh")
            ]
        ]
        await query.edit_message_text(detail_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "report_wo_summary":
        summary = await asyncio.to_thread(db.get_wo_summary)
        summary_text = (
            "📊 *Ringkasan Status Work Order (WO):*\n\n"
            f"📁 *Total WO Terdata:* {summary['total']} Order\n"
            f"⏳ *Pending:* {summary['pending']} Order\n"
            f"⚙️ *Dalam Proses:* {summary['in_progress']} Order\n"
            f"✅ *Selesai:* {summary['selesai']} Order\n"
            f"💰 *Tertagih:* {summary['tertagih']} Order\n\n"
            f"⚠️ *Berkas Pending (Belum Lengkap):* {summary['pending_docs']} Order"
        )
        keyboard = [
            [
                InlineKeyboardButton("⬅️ Kembali ke Laporan", callback_data="singkawang_refresh")
            ]
        ]
        await query.edit_message_text(summary_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "report_help_info":
        await show_report_help(update)

# Alias callback lama
handle_sa_mempawah_callback = handle_singkawang_callback

# --- ALUR: DASHBOARD PRODUKTIVITAS TEKNISI (/dashboard) ---

async def handle_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mengambil screenshot dashboard Assurance dan merangkum analisis produktivitas teknisi."""
    user = update.effective_user
    if not is_authorized(user.id):
        if update.message:
            await update.message.reply_text("Maaf, Anda tidak memiliki izin untuk menggunakan bot ini.")
        elif update.callback_query:
            await update.callback_query.answer("Akses ditolak.", show_alert=True)
        return

    chat_id = update.effective_chat.id
    if update.callback_query:
        await update.callback_query.answer()
        status_msg = await update.callback_query.message.reply_text(
            "⏳ Sedang mengambil tangkapan layar dashboard Assurance dan menganalisis rekap teknisi...\n_(Membutuhkan beberapa detik)_"
        )
    else:
        status_msg = await update.message.reply_text(
            "⏳ Sedang mengambil tangkapan layar dashboard Assurance dan menganalisis rekap teknisi...\n_(Membutuhkan beberapa detik)_"
        )

    try:
        screenshot_bytes, desc = await capture_productivity_dashboard()

        photo_file = io.BytesIO(screenshot_bytes)
        photo_file.name = "dashboard_produktivitas.png"

        keyboard = [
            [
                InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="menu_dashboard"),
                InlineKeyboardButton("🏢 Laporan SINGKAWANG", callback_data="menu_singkawang")
            ],
            [
                InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu_back")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Caption batas 1024 karakter di Telegram
        if len(desc) <= 1024:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo_file,
                caption=desc,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo_file
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=desc,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )

        if status_msg:
            await status_msg.delete()

    except Exception as e:
        logger.error(f"Gagal memproses dashboard capture: {e}", exc_info=True)
        err_msg = f"❌ Terjadi kesalahan saat mengambil dashboard:\n`{str(e)}`"
        if status_msg:
            await status_msg.edit_text(err_msg, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=chat_id, text=err_msg, parse_mode="Markdown")

# --- ALUR PERCAKAPAN: UPDATE STATUS WO ---

async def start_update_wo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Memulai proses update status WO."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("⏳ Membaca daftar WO dari Google Sheets...")
    wos = await asyncio.to_thread(db.get_all_wos)
    
    active_wos = [wo for wo in wos if wo.get("Status", "").lower() != "tertagih"]
    
    if not active_wos:
        keyboard = [[InlineKeyboardButton("⬅️ Kembali", callback_data="menu_back")]]
        await query.edit_message_text("⚠️ Tidak ada WO aktif untuk diupdate statusnya.", reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    # Buat inline keyboard berisi list WO ID
    keyboard = []
    # Batasi maksimal 8 WO di tombol, sisanya bisa diketik manual
    for wo in active_wos[:8]:
        wo_id = wo.get("WO ID", "-")
        client = wo.get("Pelanggan", "-")
        keyboard.append([InlineKeyboardButton(f"{wo_id} ({client})", callback_data=f"upwo_{wo_id}")])
        
    keyboard.append([InlineKeyboardButton("✍️ Ketik WO ID Manual", callback_data="upwo_manual")])
    keyboard.append([InlineKeyboardButton("❌ Batalkan", callback_data="menu_back")])
    
    await query.edit_message_text("Pilih Work Order yang ingin di-update statusnya:", reply_markup=InlineKeyboardMarkup(keyboard))
    return STATE_UPDATE_SELECT_WO

async def update_wo_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima pilihan WO ID dari tombol."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data == "upwo_manual":
        await query.edit_message_text("Silakan ketik nomor WO ID yang ingin Anda update (contoh: WO-001):")
        return STATE_MANUAL_INPUT_WO
        
    wo_id = data.split("_")[1]
    context.user_data["update_wo_id"] = wo_id
    
    # Tampilkan pilihan status
    keyboard = []
    for cat in config.STATUS_CATEGORIES:
        keyboard.append([InlineKeyboardButton(f"📌 {cat}", callback_data=f"upstat_{cat}")])
    keyboard.append([InlineKeyboardButton("❌ Batalkan", callback_data="menu_back")])
    
    await query.edit_message_text(f"Pilih status baru untuk *{wo_id}*:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return STATE_UPDATE_SELECT_STATUS

async def update_wo_manual_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima input manual WO ID lewat pesan chat."""
    wo_id = update.message.text.strip()
    
    # Cari di database
    wo = await asyncio.to_thread(db.get_wo_by_id, wo_id)
    if not wo:
        await update.message.reply_text(f"⚠️ WO ID *{wo_id}* tidak ditemukan. Silakan ketik ulang atau ketik /cancel:")
        return STATE_MANUAL_INPUT_WO
        
    context.user_data["update_wo_id"] = wo.get("WO ID")
    
    # Tampilkan pilihan status
    keyboard = []
    for cat in config.STATUS_CATEGORIES:
        keyboard.append([InlineKeyboardButton(f"📌 {cat}", callback_data=f"upstat_{cat}")])
    keyboard.append([InlineKeyboardButton("❌ Batalkan", callback_data="menu_back")])
    
    await update.message.reply_text(
        f"WO ID ditemukan!\n"
        f"Pelanggan: {wo.get('Pelanggan')}\n"
        f"Teknisi: {wo.get('Teknisi')}\n"
        f"Status saat ini: {wo.get('Status')}\n\n"
        f"Pilih status baru untuk *{wo.get('WO ID')}*:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return STATE_UPDATE_SELECT_STATUS

async def update_status_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menyimpan status baru ke Google Sheet."""
    query = update.callback_query
    await query.answer()
    
    new_status = query.data.split("_")[1]
    wo_id = context.user_data.get("update_wo_id")
    
    await query.edit_message_text(f"⏳ Sedang memperbarui status {wo_id} menjadi {new_status}...")
    
    # Cari nomor baris WO
    wo = await asyncio.to_thread(db.get_wo_by_id, wo_id)
    if not wo:
        await query.edit_message_text("❌ Kesalahan: Baris data tidak ditemukan kembali.")
        context.user_data.clear()
        return ConversationHandler.END
        
    row_num = wo["_row_num"]
    success = await asyncio.to_thread(db.update_wo_status, row_num, new_status)
    
    if success:
        await query.edit_message_text(f"✅ Status WO *{wo_id}* berhasil diperbarui menjadi *{new_status}*!", parse_mode="Markdown")
    else:
        await query.edit_message_text("❌ Gagal memperbarui status ke Google Sheet. Silakan coba lagi nanti.")
        
    context.user_data.clear()
    
    # Munculkan menu awal kembali
    await start(update, context)
    return ConversationHandler.END

# --- ALUR PERCAKAPAN: TAMBAH WO BARU ---

async def start_add_wo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mulai tambah WO baru."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("Silakan ketik *WO ID* baru (contoh: WO-005):", parse_mode="Markdown")
    return STATE_ADD_ID

async def add_wo_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wo_id = update.message.text.strip()
    
    # Cek apakah WO ID sudah ada
    existing = await asyncio.to_thread(db.get_wo_by_id, wo_id)
    if existing:
        await update.message.reply_text(f"⚠️ WO ID *{wo_id}* sudah terdaftar. Silakan gunakan ID lain atau ketik /cancel:")
        return STATE_ADD_ID
        
    context.user_data["new_wo_id"] = wo_id
    
    # Ambil daftar teknisi untuk keyboard
    techs = await asyncio.to_thread(db.get_unique_technicians)
    keyboard = []
    for t in techs:
        keyboard.append([InlineKeyboardButton(t, callback_data=f"addtech_{t}")])
    keyboard.append([InlineKeyboardButton("❌ Batalkan", callback_data="cancel_conv")])
    
    await update.message.reply_text(
        f"WO ID: *{wo_id}*\n\nPilih *Teknisi* penanggung jawab:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return STATE_ADD_TECH

async def add_wo_tech(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    tech_name = query.data.split("_")[1]
    context.user_data["new_wo_tech"] = tech_name
    
    await query.edit_message_text(f"Teknisi terpilih: *{tech_name}*\n\nSilakan ketik nama *Pelanggan / Customer*:", parse_mode="Markdown")
    return STATE_ADD_CLIENT

async def add_wo_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client_name = update.message.text.strip()
    context.user_data["new_wo_client"] = client_name
    
    await update.message.reply_text(f"Pelanggan: *{client_name}*\n\nSilakan ketik rincian *Pekerjaan*:", parse_mode="Markdown")
    return STATE_ADD_JOB

async def add_wo_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    job_desc = update.message.text.strip()
    
    # Kumpulkan semua data
    wo_id = context.user_data.get("new_wo_id")
    teknisi = context.user_data.get("new_wo_tech")
    pelanggan = context.user_data.get("new_wo_client")
    tanggal = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    await update.message.reply_text("⏳ Sedang menyimpan data ke Google Sheets...")
    
    success = await asyncio.to_thread(
        db.add_new_wo,
        wo_id=wo_id,
        tanggal=tanggal,
        teknisi=teknisi,
        pelanggan=pelanggan,
        pekerjaan=job_desc,
        status="Pending",
        keterangan="Baru dibuat via Bot",
        berkas="Belum Lengkap"
    )
    
    if success:
        msg = (
            f"✅ *Work Order Baru Berhasil Ditambahkan!*\n\n"
            f"🆔 ID: {wo_id}\n"
            f"📅 Tanggal: {tanggal}\n"
            f"👤 Teknisi: {teknisi}\n"
            f"🏢 Pelanggan: {pelanggan}\n"
            f"🛠️ Pekerjaan: {job_desc}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Gagal menyimpan data ke Google Sheets. Silakan coba kembali.")
        
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END

# --- ALUR PERCAKAPAN: TAGIHAN GEMINI AI ---

async def start_gemini_billing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mulai proses pembuatan pesan tagihan dengan Gemini."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("⏳ Mencari WO yang membutuhkan kelengkapan berkas...")
    wos = await asyncio.to_thread(db.get_all_wos)
    
    # Filter WO yang statusnya "Selesai" atau berkasnya tidak lengkap
    need_docs = [wo for wo in wos if wo.get("Kelengkapan Berkas", "").strip().lower() != "lengkap"]
    
    if not need_docs:
        keyboard = [[InlineKeyboardButton("⬅️ Kembali", callback_data="menu_back")]]
        await query.edit_message_text("🎉 Semua berkas sudah lengkap di Google Sheets!", reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    keyboard = []
    # Tampilkan maksimal 8 WO di tombol
    for wo in need_docs[:8]:
        wo_id = wo.get("WO ID", "-")
        client = wo.get("Pelanggan", "-")
        keyboard.append([InlineKeyboardButton(f"{wo_id} - {client}", callback_data=f"gemwo_{wo_id}")])
        
    keyboard.append([InlineKeyboardButton("❌ Batalkan", callback_data="menu_back")])
    
    await query.edit_message_text("Pilih Work Order untuk dirumuskan pesan pengingat berkas oleh Gemini AI:", reply_markup=InlineKeyboardMarkup(keyboard))
    return STATE_GEMINI_SELECT_WO

async def gemini_wo_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima pilihan WO, memanggil Gemini API."""
    query = update.callback_query
    await query.answer()
    
    wo_id = query.data.split("_")[1]
    
    await query.edit_message_text(f"🤖 Meminta Gemini AI merumuskan draf pesan pengingat berkas untuk *{wo_id}*...", parse_mode="Markdown")
    
    # Cari detail WO
    wo = await asyncio.to_thread(db.get_wo_by_id, wo_id)
    if not wo:
        await query.edit_message_text("❌ Kesalahan: Detail WO tidak ditemukan.")
        context.user_data.clear()
        return ConversationHandler.END
        
    # Panggil Gemini AI secara asinkron
    draft_message = await asyncio.to_thread(db.generate_billing_reminder, wo)
    
    keyboard = [[InlineKeyboardButton("⬅️ Selesai & Kembali", callback_data="menu_back")]]
    
    await query.edit_message_text(
        f"🤖 *Draf Pesan Pengingat Berkas (Salin pesan di bawah):*\n\n"
        f"```\n{draft_message}\n```",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    context.user_data.clear()
    return ConversationHandler.END

# --- BACKGROUND SCHEDULER / TIMER ---

async def send_scheduled_dashboard_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Mengirim laporan Dashboard Produktivitas secara otomatis ke grup target (-1001342185601)
    setiap 2 jam sekali dari jam 07.00 - 20.00 WIB setiap hari.
    """
    target_chat_id = config.TARGET_GROUP_ID
    logger.info(f"Auto Dashboard Job: Mengambil data dan screenshot dashboard untuk grup {target_chat_id}...")
    try:
        screenshot_bytes, desc = await capture_productivity_dashboard()

        photo_file = io.BytesIO(screenshot_bytes)
        photo_file.name = "dashboard_produktivitas.png"

        # Kirim foto langsung tanpa tombol navigasi agar anggota grup tidak melakukan interaksi perintah
        if len(desc) <= 1024:
            await context.bot.send_photo(
                chat_id=target_chat_id,
                photo=photo_file,
                caption=desc,
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_photo(
                chat_id=target_chat_id,
                photo=photo_file
            )
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=desc,
                parse_mode="Markdown"
            )
        logger.info(f"Auto Dashboard Job: Berhasil mengirim laporan otomatis ke grup {target_chat_id}")
    except Exception as e:
        logger.error(f"Auto Dashboard Job: Gagal mengirim ke grup {target_chat_id}: {e}", exc_info=True)

async def run_daily_recap_job(context: ContextTypes.DEFAULT_TYPE):
    """Pekerjaan terjadwal: Mengirimkan rekap WO belum lengkap berkas setiap hari."""
    logger.info("Scheduler: Memulai pengecekan berkas tertunda harian...")
    wos = await asyncio.to_thread(db.get_all_wos)
    
    pending_docs = []
    for wo in wos:
        status = wo.get("Status", "").strip().lower()
        berkas = wo.get("Kelengkapan Berkas", "").strip().lower()
        if status == "selesai" and (not berkas or "lengkap" not in berkas):
            pending_docs.append(wo)
            
    if not pending_docs:
        logger.info("Scheduler: Berkas lengkap semua, tidak ada notifikasi dikirim.")
        return

    # Susun pesan
    alert_text = (
        f"🔔 *[LAPORAN HARIAN MPWOK]*\n\n"
        f"Terdapat *{len(pending_docs)}* Work Order (WO) berstatus 'Selesai' namun dokumen penagihannya belum lengkap:\n\n"
    )
    for wo in pending_docs:
        alert_text += (
            f"• *{wo.get('WO ID')}* - Pelanggan: {wo.get('Pelanggan')}\n"
            f"  Teknisi: {wo.get('Teknisi')}\n"
            f"  Kekurangan: {wo.get('Kelengkapan Berkas', 'Belum ada keterangan berkas')}\n\n"
        )
    alert_text += "Silakan gunakan bot dan pilih menu *Tagihan Gemini AI* untuk mem-follow up teknisi secara otomatis."

    # Kirim ke seluruh ID Telegram yang diizinkan (Korlap/Ops)
    if config.ALLOWED_TELEGRAM_IDS:
        for chat_id in config.ALLOWED_TELEGRAM_IDS:
            try:
                await context.bot.send_message(chat_id=chat_id, text=alert_text, parse_mode="Markdown")
                logger.info(f"Scheduler: Notifikasi harian dikirim ke chat_id {chat_id}")
            except Exception as e:
                logger.error(f"Scheduler: Gagal mengirim ke {chat_id}: {e}")
    else:
        logger.warning("Scheduler: ALLOWED_TELEGRAM_IDS kosong, tidak ada tujuan pengiriman notifikasi.")

# --- HANDLER LAIN-LAIN ---

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan panduan perintah bot MPWOK."""
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("Maaf, Anda tidak memiliki izin untuk menggunakan bot ini.")
        return

    help_text = (
        "🤖 *Panduan Perintah Bot SINGKAWANG*\n\n"
        "Berikut daftar perintah yang dapat Anda gunakan di bot ini:\n\n"
        "• `/start` - Menampilkan menu interaktif utama\n"
        "• `/report` - Menampilkan laporan operasional SINGKAWANG\n"
        "  _Argumen opsional:_\n"
        "  - `/report [jml_teknisi]` contoh: `/report 10`\n"
        "  - `/report [jml_teknisi] [kendala...]` contoh: `/report 12 Hujan deras di beberapa titik`\n"
        "• `/rekap` atau `/laporan` - Perintah cepat untuk laporan operasional\n"
        "• `/singkawang` - Perintah khusus laporan SINGKAWANG\n"
        "• `/dashboard` - Tangkapan layar tabel produktivitas teknisi & analisis rekap\n"
        "• `/help` - Menampilkan panduan bantuan ini\n\n"
        "💡 Anda juga dapat mengklik tombol pada menu utama `/start` untuk mengelola WO, memperbarui status, dan menyusun pesan tagihan otomatis."
    )
    keyboard = [
        [
            InlineKeyboardButton("🏢 Laporan SINGKAWANG", callback_data="menu_singkawang"),
            InlineKeyboardButton("📸 Dashboard Produktivitas", callback_data="menu_dashboard")
        ],
        [InlineKeyboardButton("📱 Menu Utama", callback_data="menu_back")]
    ]
    await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def post_init(application):
    """Mendaftarkan menu perintah (Bot Commands) ke aplikasi Telegram."""
    commands = [
        BotCommand("start", "Mulai bot & menu utama"),
        BotCommand("report", "Laporan SINGKAWANG (/report [teknisi] [kendala])"),
        BotCommand("singkawang", "Laporan SINGKAWANG"),
        BotCommand("dashboard", "Screenshot dashboard & rekap teknisi"),
        BotCommand("laporan", "Laporan operasional & tiket"),
        BotCommand("rekap", "Rekapitulasi berkas & order"),
        BotCommand("help", "Panduan dan bantuan penggunaan bot")
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Bot commands berhasil didaftarkan ke Telegram.")
    except Exception as e:
        logger.warning(f"Gagal mendaftarkan bot commands ke Telegram: {e}")

async def handle_menu_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani tombol Kembali ke Menu Utama."""
    query = update.callback_query
    await query.answer()
    await start(update, context)
    return ConversationHandler.END

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Membatalkan alur percakapan lewat tombol."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Proses dibatalkan.")
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END

# --- INSTANSIASI BOT & RUN ---

def main():
    # 1. Validasi konfigurasi awal
    is_valid, errors = config.validate_config()
    if not is_valid:
        print("\n❌ GAGAL START BOT KARENA KONFIGURASI TIDAK LENGKAP:")
        for err in errors:
            print(f"- {err}")
        print("\nHarap lengkapi file .env terlebih dahulu sesuai petunjuk!\n")
        return

    # 2. Hubungkan awal ke Google Sheet untuk testing koneksi
    print("⏳ Menghubungkan ke Google Sheets...")
    if db.connect():
        print("✅ Berhasil memuat sheet data.")
    else:
        print("❌ Gagal terhubung ke Google Sheets. Periksa kredensial / ID sheet Anda.")
        print("Bot akan tetap berjalan tetapi fitur sheet akan bermasalah sampai diperbaiki.\n")

    # 3. Setup Aplikasi Telegram dengan post_init untuk mendaftarkan command Telegram
    application = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # 4. Registrasi Schedulers (Background Jobs)
    job_queue = application.job_queue
    if job_queue:
        # Jadwalkan laporan Dashboard Produktivitas setiap 2 jam sekali dari jam 07.00 s/d 20.00 WIB setiap hari
        tz_wib = timezone(timedelta(hours=7))
        dashboard_hours = [7, 9, 11, 13, 15, 17, 19, 20]
        for h in dashboard_hours:
            job_queue.run_daily(
                send_scheduled_dashboard_job,
                time=time(hour=h, minute=0, second=0, tzinfo=tz_wib),
                name=f"auto_dashboard_{h:02d}00"
            )
        logger.info(f"Auto dashboard terdaftar untuk jam: {dashboard_hours} WIB ke grup {config.TARGET_GROUP_ID}")

        # Recap berkas harian tetap berjalan setiap 12 jam
        job_queue.run_repeating(run_daily_recap_job, interval=43200, first=60)
        logger.info("Job harian berhasil didaftarkan ke JobQueue.")
    else:
        logger.warning("JobQueue tidak aktif. Pastikan python-telegram-bot[job-queue] terinstall.")

    # 5. Konfigurasi Percakapan untuk Update Status
    conv_update_status = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_update_wo, pattern="^menu_update_wo$")],
        states={
            STATE_UPDATE_SELECT_WO: [CallbackQueryHandler(update_wo_selected, pattern="^upwo_")],
            STATE_MANUAL_INPUT_WO: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_wo_manual_input)],
            STATE_UPDATE_SELECT_STATUS: [CallbackQueryHandler(update_status_selected, pattern="^upstat_")]
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel_callback, pattern="^menu_back$")
        ],
        per_message=False # Diperlukan karena callback data berubah-ubah antar pesan
    )

    # 6. Konfigurasi Percakapan untuk Tambah WO Baru
    conv_add_wo = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_wo, pattern="^menu_add_wo$")],
        states={
            STATE_ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wo_id)],
            STATE_ADD_TECH: [CallbackQueryHandler(add_wo_tech, pattern="^addtech_")],
            STATE_ADD_CLIENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wo_client)],
            STATE_ADD_JOB: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wo_job)]
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel_callback, pattern="^cancel_conv$")
        ],
        per_message=False
    )

    # 7. Konfigurasi Percakapan untuk Gemini AI Reminder
    conv_gemini = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_gemini_billing, pattern="^menu_gemini_wo$")],
        states={
            STATE_GEMINI_SELECT_WO: [CallbackQueryHandler(gemini_wo_selected, pattern="^gemwo_")]
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel_callback, pattern="^menu_back$")
        ],
        per_message=False
    )

    # 8. Registrasi Handlers ke Application
    # Blokir seluruh perintah & tombol dari anggota di dalam grup target (-1001342185601)
    application.add_handler(TypeHandler(Update, block_group_interactions), group=-1)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("bantuan", help_command))
    
    # Perintah Dashboard Produktivitas Teknisi
    application.add_handler(CommandHandler(
        ["dashboard", "produktivitas", "capture", "teknisi"],
        handle_dashboard
    ))

    # Perintah Laporan / Report (dengan berbagai alias & variasi penulisan)
    application.add_handler(CommandHandler(
        ["report", "reaport", "laporan", "rekap", "SINGKAWANG", "singkawang", "sasingkawang", "SAMEMPAWAH", "samempawah"],
        handle_report
    ))
    
    application.add_handler(conv_update_status)
    application.add_handler(conv_add_wo)
    application.add_handler(conv_gemini)
    
    # Handler Menu Utama Non-percakapan
    application.add_handler(CallbackQueryHandler(handle_list_wo, pattern="^menu_list_wo$"))
    application.add_handler(CallbackQueryHandler(handle_menu_singkawang, pattern="^(menu_singkawang|menu_sa_mempawah)$"))
    application.add_handler(CallbackQueryHandler(handle_singkawang_callback, pattern="^(singkawang_|sa_mempawah_|report_)"))
    application.add_handler(CallbackQueryHandler(handle_dashboard, pattern="^menu_dashboard$"))
    application.add_handler(CallbackQueryHandler(handle_check_docs, pattern="^menu_check_docs$"))
    application.add_handler(CallbackQueryHandler(handle_menu_back, pattern="^menu_back$"))

    # 9. Jalankan Bot
    print("🚀 Bot SINGKAWANG sedang berjalan... Tekan Ctrl+C untuk menghentikan.")
    application.run_polling()

if __name__ == "__main__":
    main()
