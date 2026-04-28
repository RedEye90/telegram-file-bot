import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ─── Logging setup ───────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────
BOT_TOKEN   = os.environ["BOT_TOKEN"]          # set in Railway/Render env vars
ADMIN_ID    = int(os.environ["ADMIN_ID"])      # your Telegram user ID (integer)

# In-memory file store  { file_id: { "name": str, "type": "apk"|"zip", "file_id": str } }
file_store: dict[str, dict] = {}


# ─── Helper ──────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def build_file_list_keyboard():
    """Build inline keyboard listing all uploaded files."""
    if not file_store:
        return None
    buttons = [
        [InlineKeyboardButton(f"📦 {meta['name']}", callback_data=f"dl:{fid}")]
        for fid, meta in file_store.items()
    ]
    return InlineKeyboardMarkup(buttons)


# ─── /start ──────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = build_file_list_keyboard()

    if keyboard:
        await update.message.reply_text(
            f"👋 Namaste {user.first_name}!\n\n"
            "Neeche available files hain. Jo chahiye wo tap karo 👇",
            reply_markup=keyboard,
        )
    else:
        await update.message.reply_text(
            f"👋 Namaste {user.first_name}!\n\n"
            "Abhi koi file available nahi hai. Thodi der baad try karo. 🙏"
        )


# ─── /list ───────────────────────────────────────────────────────
async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = build_file_list_keyboard()
    if keyboard:
        await update.message.reply_text(
            "📂 Available files:", reply_markup=keyboard
        )
    else:
        await update.message.reply_text("❌ Koi file upload nahi hui abhi tak.")


# ─── /delete (admin only) ────────────────────────────────────────
async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf admin yeh kar sakta hai.")
        return

    if not context.args:
        if not file_store:
            await update.message.reply_text("Koi file nahi hai.")
            return
        lines = [f"`{fid}` — {meta['name']}" for fid, meta in file_store.items()]
        await update.message.reply_text(
            "Delete karne ke liye:\n`/delete <file_id>`\n\n" + "\n".join(lines),
            parse_mode="Markdown",
        )
        return

    fid = context.args[0]
    if fid in file_store:
        name = file_store.pop(fid)["name"]
        await update.message.reply_text(f"✅ `{name}` delete ho gaya.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ File ID nahi mili.")


# ─── /help ───────────────────────────────────────────────────────
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        text = (
            "🛠 *Admin Commands*\n\n"
            "📤 *File upload* — Mujhe seedha APK/ZIP bhejo, main store kar lunga\n"
            "/list — Sab files dekho\n"
            "/delete `<file_id>` — Koi file hatao\n"
            "/help — Yeh message"
        )
    else:
        text = (
            "📖 *Help*\n\n"
            "/start — Files ki list dekho\n"
            "/list  — Available files\n"
            "/help  — Yeh message"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── Admin: receive APK / ZIP uploads ────────────────────────────
async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🚫 Sirf admin files upload kar sakta hai."
        )
        return

    doc = update.message.document
    if doc is None:
        return

    fname = doc.file_name or "file"
    ext   = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""

    if ext not in ("apk", "zip"):
        await update.message.reply_text(
            "⚠️ Sirf .apk aur .zip files accept hoti hain."
        )
        return

    # Use Telegram's file_id as the key (stable, no re-upload needed)
    fid = doc.file_id
    file_store[fid] = {"name": fname, "type": ext, "file_id": fid}

    await update.message.reply_text(
        f"✅ *{fname}* store ho gaya!\n\n"
        f"File ID: `{fid}`\n"
        f"Users ab /list se download kar sakte hain. 🎉",
        parse_mode="Markdown",
    )
    logger.info("Admin uploaded: %s (%s)", fname, fid)


# ─── User: button tap → send file ────────────────────────────────
async def handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, fid = query.data.split(":", 1)
    meta   = file_store.get(fid)

    if not meta:
        await query.edit_message_text("❌ File nahi mili. Shayad delete ho gayi.")
        return

    await query.message.reply_document(
        document=meta["file_id"],
        caption=f"📦 *{meta['name']}*\n\nEnjoy! 🚀",
        parse_mode="Markdown",
    )
    logger.info(
        "User %s downloaded %s", query.from_user.id, meta["name"]
    )


# ─── Unknown messages ─────────────────────────────────────────────
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Samajh nahi aaya 🤔\n/help likho commands dekhne ke liye."
    )


# ─── Main ────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("list",   list_files))
    app.add_handler(CommandHandler("delete", delete_file))
    app.add_handler(CommandHandler("help",   help_cmd))

    # Admin uploads (documents only)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_upload))

    # Inline button taps (downloads)
    app.add_handler(CallbackQueryHandler(handle_download, pattern=r"^dl:"))

    # Catch-all
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    logger.info("Bot chal raha hai... 🚀")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
