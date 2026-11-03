import re
import io
import asyncio
import logging
from telegram import (
    Update,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
import config
from insera_service import (
    InseraAuthNeeded,
    has_insera_session,
    is_insera_login_pending,
    get_pending_insera_chat_id,
    start_insera_login_flow,
    submit_insera_totp_code,
    cancel_insera_login,
    set_pending_insera_action,
    pop_pending_insera_action,
    execute_close_ujung,
    execute_close_mediacare,
)

logger = logging.getLogger(__name__)


def _get_ass_main_keyboard() -> InlineKeyboardMarkup:
    """Keyboard menu awal saat bot baru disapa atau /start."""
    keyboard = [
        [
            InlineKeyboardButton("🎫 Close Ujung", callback_data="ass_menu_ujung"),
            InlineKeyboardButton("🌐 Close Mediacare", callback_data="ass_menu_mediacare"),
        ],
        [
            InlineKeyboardButton("🔐 Login / Cek Sesi Insera", callback_data="ass_menu_login"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def ass_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk /start dan /help pada @ass_snw_bot."""
    if not update.effective_message:
        return
    text = (
        "🤖 *Selamat Datang di Bot Assurance & Close Tiket Insera (@ass_snw_bot)*\n\n"
        "Perintah yang tersedia:\n"
        "1️⃣ `/close_ujung <NO_TIKET> <ACTUAL_SOLUTION>`\n"
        "   _Contoh: `/close_ujung IN12345678 GANTI_DROPCORE`_\n\n"
        "2️⃣ `/close_mediacare <NO_TIKET> <ACTUAL_SOLUTION>`\n"
        "   _Contoh: `/close_mediacare IN12345678 KENDALA_PELANGGAN`_\n\n"
        "3️⃣ `/login` - Memulai alur login dan verifikasi Token Insera SSO\n"
        "4️⃣ `/cancel` - Membatalkan proses aktif\n\n"
        "💡 _Anda juga dapat langsung mengetikkan perintah di atas atau menggunakan menu di bawah:_"
    )
    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=_get_ass_main_keyboard(),
    )


async def ass_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Membatalkan alur yang sedang aktif."""
    context.user_data.clear()
    await cancel_insera_login()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("❌ Proses dibatalkan.")
    elif update.effective_message:
        await update.effective_message.reply_text("❌ Proses dibatalkan.")


async def _start_insera_login_prompt(chat_id: int, context: ContextTypes.DEFAULT_TYPE, status_msg=None):
    """Memulai alur login Insera SSO dan meminta kode Token TOTP kepada user."""
    if status_msg is None:
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ _Sedang membuka portal login Insera SSO Telkom..._",
            parse_mode="Markdown",
        )
    else:
        await status_msg.edit_text("⏳ _Sedang membuka portal login Insera SSO Telkom..._", parse_mode="Markdown")

    ok, msg, ss_bytes = await start_insera_login_flow(chat_id)
    if ok:
        prompt_text = (
            "🔐 *Verifikasi Token Insera SSO Diperlukan*\n\n"
            f"👤 *Username:* `{config.INSERA_USERNAME}`\n"
            "Form login telah diisi dan syarat telah disetujui.\n\n"
            "📲 Silakan **ketik / kirimkan 6 digit kode Token / TOTP** Anda di chat ini untuk melanjutkan:"
        )
        await status_msg.delete()
        if ss_bytes:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=io.BytesIO(ss_bytes),
                caption=prompt_text,
                parse_mode="Markdown",
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=prompt_text,
                parse_mode="Markdown",
            )
    else:
        await status_msg.delete()
        err_caption = f"❌ *Gagal Membuka Halaman Login Insera:*\n`{msg}`"
        if ss_bytes:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=io.BytesIO(ss_bytes),
                caption=err_caption,
                parse_mode="Markdown",
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=err_caption,
                parse_mode="Markdown",
            )


async def _execute_close_ticket(chat_id: int, context: ContextTypes.DEFAULT_TYPE, action_type: str, ticket_no: str, actual_solution: str, status_msg=None):
    """Menjalankan proses close ticket (Ujung atau Mediacare) dan mengirimkan hasil SS saja tanpa tombol menu."""
    clean_ticket = ticket_no.strip().upper()
    clean_actual = actual_solution.strip()
    action_label = "Close Ujung" if action_type == "ujung" else "Close Mediacare"

    if status_msg is None:
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏳ _Sedang memproses {action_label} di Insera..._\n"
                f"🎫 *No Tiket:* `{clean_ticket}`\n"
                f"🛠️ *Actual Solution:* `{clean_actual}`"
            ),
            parse_mode="Markdown",
        )
    else:
        await status_msg.edit_text(
            (
                f"⏳ _Sedang memproses {action_label} di Insera..._\n"
                f"🎫 *No Tiket:* `{clean_ticket}`\n"
                f"🛠️ *Actual Solution:* `{clean_actual}`"
            ),
            parse_mode="Markdown",
        )

    try:
        if action_type == "ujung":
            ss_bytes, caption = await execute_close_ujung(clean_ticket, clean_actual)
        else:
            ss_bytes, caption = await execute_close_mediacare(clean_ticket, clean_actual)

        await status_msg.delete()
        # Kirim hasil saja tanpa tombol menu
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(ss_bytes),
            caption=caption,
            parse_mode="Markdown",
        )

    except InseraAuthNeeded:
        set_pending_insera_action(action_type, clean_ticket, clean_actual, chat_id)
        await _start_insera_login_prompt(chat_id, context, status_msg)

    except Exception as e:
        logger.error(f"[AssBot] Error saat eksekusi {action_label}: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ *Terjadi kesalahan saat {action_label}:*\n`{e}`",
            parse_mode="Markdown",
        )


async def handle_close_ujung_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler perintah /close_ujung atau /ujung."""
    chat_id = update.effective_chat.id
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batalkan", callback_data="ass_cancel")]])

    if update.callback_query:
        await update.callback_query.answer()
        context.user_data["ass_step"] = "awaiting_ujung_ticket"
        await update.callback_query.message.reply_text(
            "🎫 *Close Ujung Insera*\n\n"
            "Silakan masukkan **Nomor Tiket Incident**:\n"
            "_(Contoh: `IN12345678`)_",
            parse_mode="Markdown",
            reply_markup=cancel_kb,
        )
        return

    args = list(context.args) if context.args else []
    if len(args) >= 2:
        ticket_no = args[0]
        actual_sol = " ".join(args[1:])
        context.user_data.clear()
        await _execute_close_ticket(chat_id, context, "ujung", ticket_no, actual_sol)
        return

    if len(args) == 1:
        context.user_data["ass_ticket_no"] = args[0].strip()
        context.user_data["ass_step"] = "awaiting_ujung_actual"
        await update.effective_message.reply_text(
            f"🎫 *No Tiket:* `{args[0].strip()}`\n\n"
            "Sekarang masukkan kode **Actual Solution** (classification_code):\n"
            "_(Contoh: `GANTI_DROPCORE`)_",
            parse_mode="Markdown",
            reply_markup=cancel_kb,
        )
        return

    context.user_data["ass_step"] = "awaiting_ujung_ticket"
    await update.effective_message.reply_text(
        "🎫 *Close Ujung Insera*\n\n"
        "Silakan masukkan **Nomor Tiket Incident**:\n"
        "_(Contoh: `IN12345678`)_",
        parse_mode="Markdown",
        reply_markup=cancel_kb,
    )


async def handle_close_mediacare_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler perintah /close_mediacare atau /mediacare."""
    chat_id = update.effective_chat.id
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batalkan", callback_data="ass_cancel")]])

    if update.callback_query:
        await update.callback_query.answer()
        context.user_data["ass_step"] = "awaiting_mediacare_ticket"
        await update.callback_query.message.reply_text(
            "🌐 *Close Mediacare Insera*\n\n"
            "Silakan masukkan **Nomor Tiket Incident**:\n"
            "_(Contoh: `IN12345678`)_",
            parse_mode="Markdown",
            reply_markup=cancel_kb,
        )
        return

    args = list(context.args) if context.args else []
    if len(args) >= 2:
        ticket_no = args[0]
        actual_sol = " ".join(args[1:])
        context.user_data.clear()
        await _execute_close_ticket(chat_id, context, "mediacare", ticket_no, actual_sol)
        return

    if len(args) == 1:
        context.user_data["ass_ticket_no"] = args[0].strip()
        context.user_data["ass_step"] = "awaiting_mediacare_actual"
        await update.effective_message.reply_text(
            f"🎫 *No Tiket:* `{args[0].strip()}`\n\n"
            "Sekarang masukkan kode **Actual Solution** (classification_code):\n"
            "_(Contoh: `KENDALA_PELANGGAN`)_",
            parse_mode="Markdown",
            reply_markup=cancel_kb,
        )
        return

    context.user_data["ass_step"] = "awaiting_mediacare_ticket"
    await update.effective_message.reply_text(
        "🌐 *Close Mediacare Insera*\n\n"
        "Silakan masukkan **Nomor Tiket Incident**:\n"
        "_(Contoh: `IN12345678`)_",
        parse_mode="Markdown",
        reply_markup=cancel_kb,
    )


async def handle_login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler perintah /login untuk login atau memperbarui sesi Insera."""
    chat_id = update.effective_chat.id
    if update.callback_query:
        await update.callback_query.answer()
    await _start_insera_login_prompt(chat_id, context)


async def handle_ass_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani pesan teks di @ass_snw_bot (Input Token TOTP, No Tiket, Actual Solution)."""
    if not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text.strip()
    chat_id = update.effective_chat.id

    # 1. Cek apakah sedang menunggu kode Token TOTP
    if is_insera_login_pending():
        # User mengirim kode token (biasanya 6 digit)
        clean_token = re.sub(r"\s+", "", text)
        status_msg = await update.effective_message.reply_text("⏳ _Memverifikasi kode Token ke Insera SSO..._", parse_mode="Markdown")
        ok, msg, ss_bytes = await submit_insera_totp_code(clean_token)

        if ok:
            pending_act = pop_pending_insera_action()
            if pending_act:
                await status_msg.edit_text(
                    "✅ *Login Insera SSO Berhasil!*\n"
                    f"⏳ _Melanjutkan {pending_act['type'].upper()} untuk tiket `{pending_act['ticket']}`..._",
                    parse_mode="Markdown",
                )
                await _execute_close_ticket(
                    chat_id,
                    context,
                    pending_act["type"],
                    pending_act["ticket"],
                    pending_act["actual"],
                    status_msg=status_msg,
                )
            else:
                await status_msg.edit_text(
                    "✅ *Login Insera SSO Berhasil!*\n"
                    "Sesi telah disimpan secara permanen. Anda dapat menggunakan `/close_ujung` atau `/close_mediacare`.",
                    parse_mode="Markdown",
                )
            return
        else:
            await status_msg.edit_text(
                f"❌ *Verifikasi Token Gagal:*\n`{msg}`\n\nSilakan masukkan kembali 6 digit kode Token yang valid:",
                parse_mode="Markdown",
            )
            return

    # 2. Cek teks perintah natural tanpa slash
    lower = text.lower()
    if lower.startswith("close ujung") or lower.startswith("close_ujung"):
        parts = text.split()
        args = parts[2:] if lower.startswith("close ujung") else parts[1:]
        context.args = args
        await handle_close_ujung_command(update, context)
        return

    if lower.startswith("close mediacare") or lower.startswith("close_mediacare"):
        parts = text.split()
        args = parts[2:] if lower.startswith("close mediacare") else parts[1:]
        context.args = args
        await handle_close_mediacare_command(update, context)
        return

    # 3. Alur interaktif step-by-step
    step = context.user_data.get("ass_step")
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batalkan", callback_data="ass_cancel")]])

    if step == "awaiting_ujung_ticket":
        tokens = text.split()
        if len(tokens) >= 2:
            ticket = tokens[0]
            actual = " ".join(tokens[1:])
            context.user_data.clear()
            await _execute_close_ticket(chat_id, context, "ujung", ticket, actual)
            return

        context.user_data["ass_ticket_no"] = tokens[0]
        context.user_data["ass_step"] = "awaiting_ujung_actual"
        await update.effective_message.reply_text(
            f"🎫 *No Tiket:* `{tokens[0]}`\n\n"
            "Sekarang masukkan kode **Actual Solution** (classification_code):\n"
            "_(Contoh: `GANTI_DROPCORE`)_",
            parse_mode="Markdown",
            reply_markup=cancel_kb,
        )
        return

    if step == "awaiting_ujung_actual":
        ticket = context.user_data.get("ass_ticket_no", "")
        actual = text
        context.user_data.clear()
        await _execute_close_ticket(chat_id, context, "ujung", ticket, actual)
        return

    if step == "awaiting_mediacare_ticket":
        tokens = text.split()
        if len(tokens) >= 2:
            ticket = tokens[0]
            actual = " ".join(tokens[1:])
            context.user_data.clear()
            await _execute_close_ticket(chat_id, context, "mediacare", ticket, actual)
            return

        context.user_data["ass_ticket_no"] = tokens[0]
        context.user_data["ass_step"] = "awaiting_mediacare_actual"
        await update.effective_message.reply_text(
            f"🎫 *No Tiket:* `{tokens[0]}`\n\n"
            "Sekarang masukkan kode **Actual Solution** (classification_code):\n"
            "_(Contoh: `KENDALA_PELANGGAN`)_",
            parse_mode="Markdown",
            reply_markup=cancel_kb,
        )
        return

    if step == "awaiting_mediacare_actual":
        ticket = context.user_data.get("ass_ticket_no", "")
        actual = text
        context.user_data.clear()
        await _execute_close_ticket(chat_id, context, "mediacare", ticket, actual)
        return


async def ass_post_init(application):
    """Mendaftarkan menu command (/) untuk @ass_snw_bot."""
    commands = [
        BotCommand("close_ujung", "🎫 Close Tiket Ujung (QC SPAC & PASSED)"),
        BotCommand("close_mediacare", "🌐 Close Tiket Mediacare (RESOLVED)"),
        BotCommand("login", "🔐 Login & Verifikasi Token Insera SSO"),
        BotCommand("start", "🏠 Menu Utama @ass_snw_bot"),
        BotCommand("cancel", "❌ Batalkan Proses"),
    ]
    try:
        await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await application.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await application.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
        logger.info("[AssBot] Menu command (/) @ass_snw_bot berhasil didaftarkan.")
    except Exception as e:
        logger.warning(f"[AssBot] Gagal mengatur command menu @ass_snw_bot: {e}")


def build_ass_application():
    """Membangun instance Application untuk Bot Ketiga (@ass_snw_bot)."""
    token = getattr(config, "ASS_BOT_TOKEN", "")
    if not token:
        return None

    app = (
        ApplicationBuilder()
        .token(token)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .post_init(ass_post_init)
        .build()
    )

    app.add_handler(CommandHandler(["start", "help"], ass_start))
    app.add_handler(CommandHandler(["cancel", "batal"], ass_cancel))
    app.add_handler(CommandHandler(["login", "masuk"], handle_login_command))
    app.add_handler(CommandHandler(["close_ujung", "ujung"], handle_close_ujung_command))
    app.add_handler(CommandHandler(["close_mediacare", "mediacare"], handle_close_mediacare_command))

    app.add_handler(CallbackQueryHandler(handle_close_ujung_command, pattern="^ass_menu_ujung$"))
    app.add_handler(CallbackQueryHandler(handle_close_mediacare_command, pattern="^ass_menu_mediacare$"))
    app.add_handler(CallbackQueryHandler(handle_login_command, pattern="^ass_menu_login$"))
    app.add_handler(CallbackQueryHandler(ass_cancel, pattern="^ass_cancel$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ass_text_input))

    return app
