import logging
import asyncio
import io
import sys
from datetime import datetime, time, timezone, timedelta

# Pastikan output konsol mendukung karakter emoji (khususnya di Windows)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from telegram import (
    Update,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat
)
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
from mybranch_capture import (
    MyBranchAuthNeeded,
    capture_absen_assurance,
    capture_ukur_masal,
    extract_nd_numbers,
    set_pending_ukur_context,
    get_pending_ukur_context,
    get_pending_auth_step,
    initiate_tactical_login,
    submit_manual_captcha,
    submit_otp_code,
    clear_pending_auth,
    keepalive_mybranch_session,
)
from prov_bot import (
    build_prov_application,
    prov_post_init,
    keepalive_prov_session,
)
from ass_bot import (
    build_ass_application,
    ass_post_init,
)

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

# Helper untuk memeriksa izin akses user (semua anggota grup & user diizinkan)
def is_authorized(user_id: int) -> bool:
    return True

async def block_group_interactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Memastikan anggota di grup REPORT_GROUP_ID (-1003737866244) bebas menggunakan seluruh perintah,
    dan hanya memblokir grup lain jika TARGET_GROUP_ID berbeda dari REPORT_GROUP_ID.
    """
    chat = update.effective_chat
    if chat and chat.id == config.TARGET_GROUP_ID and chat.id != config.REPORT_GROUP_ID:
        # Jika ada perintah teks dari anggota grup auto-schedule lama
        if update.message and update.message.text and update.message.text.startswith("/"):
            raise ApplicationHandlerStop
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
            InlineKeyboardButton("📋 Absen Assurance Singkawang (/absen_assurance)", callback_data="menu_absen_assurance")
        ],
        [
            InlineKeyboardButton("📡 Ukur Masal iBooster (/ukur_masal)", callback_data="menu_ukur_masal")
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
        chat_id = update.effective_chat.id

        if int(chat_id) == int(config.REPORT_GROUP_ID):
            await loading_msg.edit_text(report["text"], reply_markup=reply_markup)
        else:
            await context.bot.send_message(
                chat_id=config.REPORT_GROUP_ID,
                text=report["text"]
            )
            await loading_msg.edit_text(
                f"✅ *Laporan berhasil dikirimkan ke grup (`{config.REPORT_GROUP_ID}`)!*\n\n{report['text']}",
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Gagal memproses laporan: {e}")
        await loading_msg.edit_text(f"❌ Terjadi kesalahan saat membaca/mengirim data laporan:\n{e}")

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
        target_group_id = getattr(config, "REPORT_GROUP_ID", config.TARGET_GROUP_ID)
        chat_id = update.effective_chat.id

        if int(chat_id) == int(target_group_id):
            await query.edit_message_text(report["text"], reply_markup=reply_markup)
        else:
            await context.bot.send_message(
                chat_id=target_group_id,
                text=report["text"]
            )
            await query.edit_message_text(
                f"✅ *Laporan SINGKAWANG berhasil dikirim ke grup* (`{target_group_id}`)!\n\n{report['text']}",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Gagal memproses laporan: {e}")
        keyboard = [[InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu_back")]]
        await query.edit_message_text(f"❌ Terjadi kesalahan saat membaca/mengirim data laporan:\n{e}", reply_markup=InlineKeyboardMarkup(keyboard))

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
    context.user_data.clear()
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

        target_group_id = config.REPORT_GROUP_ID

        # Kirim hasil ke grup REPORT_GROUP_ID (-1003737866244) tanpa tombol
        if len(desc) <= 1024:
            await context.bot.send_photo(
                chat_id=target_group_id,
                photo=photo_file,
                caption=desc,
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_photo(
                chat_id=target_group_id,
                photo=photo_file
            )
            await context.bot.send_message(
                chat_id=target_group_id,
                text=desc,
                parse_mode="Markdown"
            )

        keyboard = [
            [
                InlineKeyboardButton("🔄 Kirim Ulang Dashboard", callback_data="menu_dashboard"),
                InlineKeyboardButton("🏢 Laporan SINGKAWANG", callback_data="menu_singkawang")
            ],
            [
                InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu_back")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if int(chat_id) == int(target_group_id):
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
        else:
            confirm_msg = f"✅ *Laporan Dashboard Produktivitas berhasil dikirimkan ke grup (`{target_group_id}`)!*"
            if status_msg:
                await status_msg.edit_text(confirm_msg, parse_mode="Markdown", reply_markup=reply_markup)
            else:
                await context.bot.send_message(chat_id=chat_id, text=confirm_msg, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Gagal memproses dashboard capture: {e}", exc_info=True)
        err_msg = f"❌ Terjadi kesalahan saat mengambil dashboard:\n`{str(e)}`"
        if status_msg:
            await status_msg.edit_text(err_msg, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=chat_id, text=err_msg, parse_mode="Markdown")

# --- ALUR: ABSEN ASSURANCE SINGKAWANG (/absen_assurance) ---

async def _send_mybranch_auth_prompt(context: ContextTypes.DEFAULT_TYPE, requester_chat_id: int, status_msg=None):
    """Memulai login Tactical MyBranch dan mengirimkan permintaan OTP (atau Captcha) ke chat pribadi Admin."""
    otp_dest_chat_id = config.MYBRANCH_OTP_CHAT_ID if int(requester_chat_id) < 0 else requester_chat_id
    logger.info(f"Memulai login MyBranch untuk requester_chat_id={requester_chat_id} (OTP dikirim ke {otp_dest_chat_id})")
    try:
        res = await asyncio.to_thread(initiate_tactical_login, requester_chat_id)
        step = res.get("step")

        if step == "otp":
            otp_prompt = (
                f"🔐 *Verifikasi Login MyBranch (takalbagbar.id)*\n\n"
                f"✅ Login akun Tactical `{config.MYBRANCH_NIK}` berhasil melewati Captcha!\n"
                f"Kode OTP baru saja dikirim ke Telegram Anda (Telkom Akses).\n\n"
                f"👉 *Silakan ketik/balas langsung 6 digit kode OTP tersebut di sini:*"
            )
            if int(requester_chat_id) < 0:
                await context.bot.send_message(chat_id=otp_dest_chat_id, text=otp_prompt, parse_mode="Markdown")
                if status_msg:
                    await status_msg.edit_text(
                        "🔐 Sesi MyBranch memerlukan verifikasi OTP. Permintaan OTP telah dikirimkan ke chat pribadi Admin.",
                        parse_mode="Markdown"
                    )
            else:
                if status_msg:
                    await status_msg.edit_text(otp_prompt, parse_mode="Markdown")
                else:
                    await context.bot.send_message(chat_id=otp_dest_chat_id, text=otp_prompt, parse_mode="Markdown")

        elif step == "captcha":
            captcha_photo = io.BytesIO(res["captcha_bytes"])
            captcha_photo.name = "captcha_mybranch.jpg"
            caption = (
                f"🔤 *Langkah 1/2: Captcha Login MyBranch*\n"
                f"Akun Tactical: `{config.MYBRANCH_NIK}`\n\n"
                f"👉 *Silakan ketik 3 huruf Captcha pada gambar di atas* (lalu pada langkah berikutnya bot akan meminta kode OTP Telegram Anda):"
            )
            await context.bot.send_photo(
                chat_id=otp_dest_chat_id,
                photo=captcha_photo,
                caption=caption,
                parse_mode="Markdown"
            )
            if int(requester_chat_id) < 0 and status_msg:
                await status_msg.edit_text("🔐 Sesi MyBranch memerlukan verifikasi login di chat pribadi Admin.")
            elif status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Gagal memulai login MyBranch: {e}", exc_info=True)
        err_text = f"❌ Gagal menghubungi server login `takalbagbar.id/mybranch`:\n`{e}`"
        if status_msg:
            await status_msg.edit_text(err_text, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=requester_chat_id, text=err_text, parse_mode="Markdown")


async def _deliver_absen_assurance_photo(context: ContextTypes.DEFAULT_TYPE, chat_id: int, status_msg=None):
    """Mengambil screenshot Absen Assurance Singkawang dan mengirimkannya ke grup REPORT_GROUP_ID (-1003737866244)."""
    screenshot_bytes, caption = await capture_absen_assurance()
    photo_file = io.BytesIO(screenshot_bytes)
    photo_file.name = "absen_assurance_singkawang.png"

    target_group_id = config.REPORT_GROUP_ID

    # Kirim hasil tangkapan layar & laporan langsung ke grup target (-1003737866244)
    if len(caption) <= 1024:
        await context.bot.send_photo(
            chat_id=target_group_id,
            photo=photo_file,
            caption=caption,
            parse_mode="Markdown"
        )
    else:
        await context.bot.send_photo(
            chat_id=target_group_id,
            photo=photo_file
        )
        await context.bot.send_message(
            chat_id=target_group_id,
            text=caption,
            parse_mode="Markdown"
        )

    keyboard = [
        [
            InlineKeyboardButton("🔄 Kirim Ulang Absen", callback_data="menu_absen_assurance"),
            InlineKeyboardButton("📸 Dashboard Produktivitas", callback_data="menu_dashboard")
        ],
        [
            InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu_back")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if int(chat_id) == int(target_group_id):
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
    else:
        confirm_text = (
            f"✅ *Laporan Absen Assurance (Singkawang) berhasil dikirimkan ke grup (`{target_group_id}`)!*"
        )
        if status_msg:
            await status_msg.edit_text(confirm_text, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id=chat_id, text=confirm_text, parse_mode="Markdown", reply_markup=reply_markup)


async def handle_absen_assurance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani perintah /absen_assurance untuk tangkapan layar dashboard Absen Assurance Singkawang."""
    user = update.effective_user
    if not is_authorized(user.id):
        if update.message:
            await update.message.reply_text("Maaf, Anda tidak memiliki izin untuk menggunakan bot ini.")
        elif update.callback_query:
            await update.callback_query.answer("Akses ditolak.", show_alert=True)
        return

    chat_id = update.effective_chat.id
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        status_msg = await update.callback_query.message.reply_text(
            "⏳ Sedang membuka dashboard *Absen Assurance (Singkawang)* di `takalbagbar.id/mybranch`...\n_(Membutuhkan beberapa detik)_",
            parse_mode="Markdown"
        )
    else:
        status_msg = await update.message.reply_text(
            "⏳ Sedang membuka dashboard *Absen Assurance (Singkawang)* di `takalbagbar.id/mybranch`...\n_(Membutuhkan beberapa detik)_",
            parse_mode="Markdown"
        )

    try:
        await _deliver_absen_assurance_photo(context, chat_id, status_msg)
    except MyBranchAuthNeeded:
        await status_msg.edit_text(
            "🔐 Sesi login MyBranch belum aktif atau telah kedaluwarsa. Memulai proses login Tactical & meminta OTP..."
        )
        await _send_mybranch_auth_prompt(context, chat_id, status_msg)
    except Exception as e:
        logger.error(f"Gagal memproses absen_assurance: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Terjadi kesalahan saat mengambil Absen Assurance:\n`{e}`", parse_mode="Markdown")


async def _deliver_ukur_masal_photo(context: ContextTypes.DEFAULT_TYPE, chat_id: int, nd_list: list[str], status_msg=None):
    """Menjalankan Ukur Masal di MyBranch, lalu mengirim SS hasil dan file Excel (.xlsx) saja."""
    screenshot_bytes, excel_bytes, excel_filename = await capture_ukur_masal(nd_list)

    # 1. Kirim SS dan file Excel ke chat tempat perintah dijalankan
    user_photo = io.BytesIO(screenshot_bytes)
    user_photo.name = "hasil_ukur_masal.png"
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=user_photo,
    )

    user_excel = io.BytesIO(excel_bytes)
    user_excel.name = excel_filename
    await context.bot.send_document(
        chat_id=chat_id,
        document=user_excel,
        filename=excel_filename,
    )

    # 2. Kirim juga SS dan file Excel ke grup report (-1003737866244) jika peminta bukan grup tersebut
    target_group_id = getattr(config, "REPORT_GROUP_ID", None)
    if target_group_id and int(chat_id) != int(target_group_id):
        try:
            group_photo = io.BytesIO(screenshot_bytes)
            group_photo.name = "hasil_ukur_masal.png"
            await context.bot.send_photo(
                chat_id=target_group_id,
                photo=group_photo,
            )

            group_excel = io.BytesIO(excel_bytes)
            group_excel.name = excel_filename
            await context.bot.send_document(
                chat_id=target_group_id,
                document=group_excel,
                filename=excel_filename,
            )
        except Exception as ge:
            logger.warning(f"Gagal meneruskan SS & Excel ukur masal ke grup {target_group_id}: {ge}")

    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass


async def handle_ukur_masal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Menangani perintah /ukur_masal (atau /ukur).
    Jika belum menyertakan nomor internet, bot meminta user mengirimkan daftar nomor internet (ND).
    """
    user = update.effective_user
    if not is_authorized(user.id):
        if update.message:
            await update.message.reply_text("Maaf, Anda tidak memiliki izin untuk menggunakan bot ini.")
        elif update.callback_query:
            await update.callback_query.answer("Akses ditolak.", show_alert=True)
        return

    chat_id = update.effective_chat.id

    # Cek apakah user langsung menyertakan nomor ND bersama perintah (misal: /ukur_masal 162607207120)
    raw_args_text = ""
    if update.message and update.message.text:
        parts = update.message.text.split(maxsplit=1)
        if len(parts) > 1:
            raw_args_text = parts[1]

    nd_list = extract_nd_numbers(raw_args_text) if raw_args_text else []

    if not nd_list:
        context.user_data.clear()
        context.user_data["awaiting_ukur_masal_nd"] = True
        context.chat_data["awaiting_ukur_masal_nd"] = True
        prompt_text = (
            "📡 *Ukur Masal iBooster (MyBranch)*\n\n"
            "Silakan *balas (reply) pesan ini* dengan *Nomor Internet (ND)* yang akan dimasukkan ke *Daftar ND*:\n"
            "_(Bisa 1 nomor atau banyak nomor sekaligus, satu per baris / pisahkan dengan Enter, spasi, atau koma)_.\n\n"
            "Contoh:\n"
            "`162607207120`\n"
            "`162603401214`"
        )
        if update.callback_query:
            await update.callback_query.answer()
            if int(chat_id) < 0:
                await update.callback_query.message.reply_text(
                    prompt_text,
                    parse_mode="Markdown",
                    reply_markup=ForceReply(selective=False, input_field_placeholder="Tempel Nomor Internet (ND) di sini...")
                )
            else:
                keyboard = [[InlineKeyboardButton("❌ Batalkan", callback_data="menu_back")]]
                await update.callback_query.message.reply_text(prompt_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            if int(chat_id) < 0:
                await update.message.reply_text(
                    prompt_text,
                    parse_mode="Markdown",
                    reply_markup=ForceReply(selective=True, input_field_placeholder="Tempel Nomor Internet (ND) di sini...")
                )
            else:
                keyboard = [[InlineKeyboardButton("❌ Batalkan", callback_data="menu_back")]]
                await update.message.reply_text(prompt_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Jika user langsung mengirimkan nomor bersama command
    context.user_data.clear()
    status_msg = await update.message.reply_text(
        f"⏳ Sedang memasukkan *{len(nd_list)} Nomor Internet (ND)* ke menu *Ukur Masal* di `takalbagbar.id/mybranch` dan mengambil SS hasilnya...",
        parse_mode="Markdown"
    )
    try:
        await _deliver_ukur_masal_photo(context, chat_id, nd_list, status_msg)
    except MyBranchAuthNeeded:
        await asyncio.to_thread(set_pending_ukur_context, nd_list, chat_id)
        await status_msg.edit_text(
            "🔐 Sesi login MyBranch belum aktif atau telah kedaluwarsa. Memulai proses login Tactical & meminta OTP..."
        )
        await _send_mybranch_auth_prompt(context, chat_id, status_msg)
    except Exception as e:
        logger.error(f"Gagal memproses ukur_masal: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Terjadi kesalahan saat menjalankan Ukur Masal:\n`{e}`", parse_mode="Markdown")


async def handle_mybranch_auth_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani balasan kode Captcha/OTP Tactical atau input Daftar ND untuk Ukur Masal dari chat Telegram."""
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    step = get_pending_auth_step()
    if step == "captcha":
        wait_msg = await update.message.reply_text("⏳ Memverifikasi Captcha & meminta OTP Tactical ke server...")
        res = await asyncio.to_thread(submit_manual_captcha, text)
        if res.get("ok") and res.get("step") == "otp":
            await wait_msg.edit_text(
                "🔐 *Langkah 2/2: Kode OTP Tactical*\n\n"
                "✅ Captcha berhasil diverifikasi! Kode OTP baru saja dikirim ke Telegram Anda.\n\n"
                "👉 *Silakan balas/ketik kode OTP tersebut di sini:*",
                parse_mode="Markdown"
            )
        else:
            await wait_msg.edit_text(f"⚠️ {res.get('message', 'Captcha salah.')}\nMengambil Captcha baru...")
            await _send_mybranch_auth_prompt(context, chat_id, wait_msg)
        raise ApplicationHandlerStop

    elif step == "otp":
        wait_msg = await update.message.reply_text("⏳ Memverifikasi kode OTP Tactical ke `takalbagbar.id/mybranch`...", parse_mode="Markdown")
        pending_action, pending_nds = await asyncio.to_thread(get_pending_ukur_context)
        res = await asyncio.to_thread(submit_otp_code, text)
        if res.get("ok"):
            target_chat_id = res.get("target_chat_id") or chat_id
            if pending_action == "ukur_masal" and pending_nds:
                await wait_msg.edit_text(
                    f"✅ *Login MyBranch Berhasil!* Sesi telah disimpan.\n"
                    f"⏳ Sedang memproses *Ukur Masal* untuk *{len(pending_nds)} ND*...",
                    parse_mode="Markdown"
                )
                try:
                    await _deliver_ukur_masal_photo(context, target_chat_id, pending_nds, wait_msg)
                except Exception as e:
                    logger.error(f"Gagal ukur masal setelah login OTP: {e}", exc_info=True)
                    await wait_msg.edit_text(f"❌ Login berhasil, namun gagal mengambil hasil Ukur Masal:\n`{e}`", parse_mode="Markdown")
            else:
                await wait_msg.edit_text(
                    "✅ *Login MyBranch Berhasil!* Sesi telah disimpan.\n"
                    "⏳ Sedang mengambil tangkapan layar *Absen Assurance (Singkawang)*...",
                    parse_mode="Markdown"
                )
                try:
                    await _deliver_absen_assurance_photo(context, target_chat_id, wait_msg)
                except Exception as e:
                    logger.error(f"Gagal capture setelah login OTP: {e}", exc_info=True)
                    await wait_msg.edit_text(f"❌ Login berhasil, namun gagal mengambil tangkapan layar:\n`{e}`", parse_mode="Markdown")
        else:
            msg = res.get("message", "Kode OTP salah atau kedaluwarsa.")
            if res.get("need_restart"):
                await wait_msg.edit_text(f"❌ {msg}\nMengulang permintaan login baru...")
                await _send_mybranch_auth_prompt(context, chat_id, wait_msg)
            else:
                await wait_msg.edit_text(
                    f"❌ *{msg}*\nSilakan ketik ulang kode OTP yang benar (atau ketik `/absen_assurance` untuk meminta OTP baru):",
                    parse_mode="Markdown"
                )
        raise ApplicationHandlerStop

    # Jika user mengetik "ukur masal" secara langsung tanpa garis miring (/)
    if text.lower() in ("ukur masal", "ukur_masal", "ukurmasal"):
        await handle_ukur_masal(update, context)
        raise ApplicationHandlerStop

    # Jika bot sedang menunggu daftar Nomor Internet (ND) setelah user memanggil /ukur_masal
    is_reply_to_ukur = False
    if update.message.reply_to_message and update.message.reply_to_message.text:
        if "Ukur Masal iBooster" in update.message.reply_to_message.text:
            is_reply_to_ukur = True

    if context.user_data.get("awaiting_ukur_masal_nd") or context.chat_data.get("awaiting_ukur_masal_nd") or is_reply_to_ukur:
        nd_list = extract_nd_numbers(text)
        if not nd_list:
            if int(chat_id) > 0 or is_reply_to_ukur:
                await update.message.reply_text(
                    "⚠️ Nomor Internet (ND) tidak terbaca. Silakan kirimkan nomor internet (angka) yang valid, atau ketik /cancel untuk membatalkan."
                )
                raise ApplicationHandlerStop
            return

        context.user_data["awaiting_ukur_masal_nd"] = False
        context.chat_data["awaiting_ukur_masal_nd"] = False
        status_msg = await update.message.reply_text(
            f"⏳ Sedang memasukkan *{len(nd_list)} Nomor Internet (ND)* ke menu *Ukur Masal* di `takalbagbar.id/mybranch` dan mengambil SS hasilnya...",
            parse_mode="Markdown"
        )
        try:
            await _deliver_ukur_masal_photo(context, chat_id, nd_list, status_msg)
        except MyBranchAuthNeeded:
            await asyncio.to_thread(set_pending_ukur_context, nd_list, chat_id)
            await status_msg.edit_text(
                "🔐 Sesi login MyBranch belum aktif atau telah kedaluwarsa. Memulai proses login Tactical & meminta OTP..."
            )
            await _send_mybranch_auth_prompt(context, chat_id, status_msg)
        except Exception as e:
            logger.error(f"Gagal memproses ukur_masal: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ Terjadi kesalahan saat menjalankan Ukur Masal:\n`{e}`", parse_mode="Markdown")
        raise ApplicationHandlerStop

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
        "• `/absen_assurance` - Tangkapan layar dashboard Absen Assurance (Singkawang)\n"
        "• `/ukur_masal` - Ukur masal nomor internet (ND) via iBooster MyBranch & kirim SS hasilnya\n"
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
    """Mendaftarkan menu perintah (Bot Commands) ke aplikasi Telegram untuk chat pribadi & grup -1003737866244."""
    commands = [
        BotCommand("start", "Mulai bot & menu utama"),
        BotCommand("ukur_masal", "Ukur masal nomor internet (ND) di MyBranch"),
        BotCommand("absen_assurance", "Screenshot dashboard Absen Assurance Singkawang"),
        BotCommand("report", "Laporan SINGKAWANG (/report [teknisi] [kendala])"),
        BotCommand("singkawang", "Laporan SINGKAWANG"),
        BotCommand("dashboard", "Screenshot dashboard & rekap teknisi"),
        BotCommand("laporan", "Laporan operasional & tiket"),
        BotCommand("rekap", "Rekapitulasi berkas & order"),
        BotCommand("help", "Panduan dan bantuan penggunaan bot")
    ]
    try:
        # Daftarkan tombol perintah (/) di seluruh chat pribadi, grup, dan khusus grup -1003737866244
        await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await application.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await application.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
        try:
            await application.bot.set_my_commands(commands, scope=BotCommandScopeChat(config.REPORT_GROUP_ID))
        except Exception as ge:
            logger.warning(f"Gagal set_my_commands khusus grup {config.REPORT_GROUP_ID}: {ge}")

        logger.info(f"Bot commands (/) berhasil dimunculkan di chat pribadi dan grup {config.REPORT_GROUP_ID}.")
    except Exception as e:
        logger.warning(f"Gagal mengatur bot commands ke Telegram: {e}")

    # Jalankan Bot Kedua (@prov_snw_bot - Assign WO PSB) secara bersamaan di event loop yang sama
    try:
        prov_app = build_prov_application()
        if prov_app:
            await prov_app.initialize()
            await prov_post_init(prov_app)
            await prov_app.start()
            await prov_app.updater.start_polling(drop_pending_updates=True)
            application.bot_data["prov_app"] = prov_app
            logger.info("🚀 Bot Kedua (@prov_snw_bot - Assign WO PSB) berhasil dijalankan bersamaan!")
    except Exception as pe:
        logger.error(f"Gagal menjalankan Bot Kedua (@prov_snw_bot): {pe}", exc_info=True)

    # Jalankan Bot Ketiga (@ass_snw_bot) secara bersamaan di event loop yang sama
    try:
        ass_app = build_ass_application()
        if ass_app:
            await ass_app.initialize()
            await ass_post_init(ass_app)
            await ass_app.start()
            await ass_app.updater.start_polling(drop_pending_updates=True)
            application.bot_data["ass_app"] = ass_app
            logger.info("🚀 Bot Ketiga (@ass_snw_bot) berhasil dijalankan bersamaan!")
    except Exception as ae:
        logger.error(f"Gagal menjalankan Bot Ketiga (@ass_snw_bot): {ae}", exc_info=True)


async def post_shutdown(application):
    """Menghentikan Bot Kedua (@prov_snw_bot) dan Bot Ketiga (@ass_snw_bot) dengan bersih saat aplikasi dimatikan."""
    for key in ("prov_app", "ass_app"):
        sub_app = application.bot_data.get(key)
        if sub_app:
            try:
                if sub_app.updater and sub_app.updater.running:
                    await sub_app.updater.stop()
                if sub_app.running:
                    await sub_app.stop()
                await sub_app.shutdown()
            except Exception as e:
                logger.warning(f"Gagal menghentikan {key} saat shutdown: {e}")

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
        .post_shutdown(post_shutdown)
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

        # Keep-Alive sesi login MyBranch setiap 10 menit (600 detik) agar sesi tidak kadaluarsa karena idle
        async def _run_mybranch_keepalive(context: ContextTypes.DEFAULT_TYPE):
            await asyncio.to_thread(keepalive_mybranch_session)
            await asyncio.to_thread(keepalive_prov_session)

        job_queue.run_repeating(_run_mybranch_keepalive, interval=600, first=120, name="mybranch_keepalive")
        logger.info("Job Keep-Alive sesi MyBranch (setiap 10 menit) berhasil didaftarkan.")
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

    # Perintah Dashboard Absen Assurance Singkawang
    application.add_handler(CommandHandler(
        ["absen_assurance", "absen"],
        handle_absen_assurance
    ))

    # Perintah Ukur Masal iBooster MyBranch
    application.add_handler(CommandHandler(
        ["ukur_masal", "ukurmasal", "ukur"],
        handle_ukur_masal
    ))

    # Handler input kode OTP / Captcha MyBranch & Daftar ND Ukur Masal di chat pribadi maupun grup (group=-2 agar dieksekusi sebelum handler lainnya)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mybranch_auth_input), group=-2)

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
    application.add_handler(CallbackQueryHandler(handle_absen_assurance, pattern="^menu_absen_assurance$"))
    application.add_handler(CallbackQueryHandler(handle_ukur_masal, pattern="^menu_ukur_masal$"))
    application.add_handler(CallbackQueryHandler(handle_check_docs, pattern="^menu_check_docs$"))
    application.add_handler(CallbackQueryHandler(handle_menu_back, pattern="^menu_back$"))

    # 9. Jalankan Bot
    print("🚀 Bot SINGKAWANG sedang berjalan... Tekan Ctrl+C untuk menghentikan.")
    application.run_polling()

if __name__ == "__main__":
    main()
