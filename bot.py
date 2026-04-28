import os
import logging
import pymysql
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
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID  = int(os.environ["ADMIN_ID"])
MYSQL_URL = os.environ["MYSQL_URL"]


# ─── MySQL Connection ─────────────────────────────────────────────
def get_db():
    url = MYSQL_URL.replace("mysql://", "")
    user_pass, rest = url.split("@", 1)
    user, password   = user_pass.split(":", 1)
    host_port, dbname = rest.split("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":", 1)
        port = int(port)
    else:
        host, port = host_port, 3306

    return pymysql.connect(
        host=host, port=port,
        user=user, password=password,
        database=dbname,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def init_db():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS files (
                file_id VARCHAR(255) PRIMARY KEY,
                name    VARCHAR(255) NOT NULL,
                type    VARCHAR(10)  NOT NULL
            )
        """)
    conn.close()
    logger.info("Database ready ✅")


# ─── DB Helpers ──────────────────────────────────────────────────
def db_save_file(file_id: str, name: str, ftype: str):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "REPLACE INTO files (file_id, name, type) VALUES (%s, %s, %s)",
            (file_id, name, ftype)
        )
    conn.close()


def db_get_all_files() -> dict:
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT file_id, name, type FROM files")
        rows = cur.fetchall()
    conn.close()
    return {row["file_id"]: {"name": row["name"], "type": row["type"], "file_id": row["file_id"]} for row in rows}


def db_get_file(file_id: str):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT file_id, name, type FROM files WHERE file_id = %s", (file_id,))
        row = cur.fetchone()
    conn.close()
    if row:
        return {"name": row["name"], "type": row["type"], "file_id": row["file_id"]}
    return None


def db_delete_file(file_id: str):
    meta = db_get_file(file_id)
    if not meta:
        return None
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM files WHERE file_id = %s", (file_id,))
    conn.close()
    return meta["name"]


# ─── Helper ──────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def build_file_list_keyboard():
    files = db_get_all_files()
    if not files:
        return None
    buttons = [
        [InlineKeyboardButton(f"📦 {meta['name']}", callback_data=f"dl:{fid}")]
        for fid, meta in files.items()
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
        await update.message.reply_text("📂 Available files:", reply_markup=keyboard)
    else:
        await update.message.reply_text("❌ Koi file upload nahi hui abhi tak.")


# ─── /delete (admin only) ────────────────────────────────────────
async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf admin yeh kar sakta hai.")
        return

    if not context.args:
        files = db_get_all_files()
        if not files:
            await update.message.reply_text("Koi file nahi hai.")
            return
        lines = [f"`{fid}` — {meta['name']}" for fid, meta in files.items()]
        await update.message.reply_text(
            "Delete karne ke liye:\n`/delete <file_id>`\n\n" + "\n".join(lines),
            parse_mode="Markdown",
        )
        return

    fid  = context.args[0]
    name = db_delete_file(fid)
    if name:
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
        await update.message.reply_text("🚫 Sirf admin files upload kar sakta hai.")
        return

    doc = update.message.document
    if doc is None:
        return

    fname = doc.file_name or "file"
    ext   = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""

    if ext not in ("apk", "zip"):
        await update.message.reply_text("⚠️ Sirf .apk aur .zip files accept hoti hain.")
        return

    fid = doc.file_id
    db_save_file(fid, fname, ext)  # ✅ MySQL mein save

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
    meta   = db_get_file(fid)

    if not meta:
        await query.edit_message_text("❌ File nahi mili. Shayad delete ho gayi.")
        return

    await query.message.reply_document(
        document=meta["file_id"],
        caption=f"📦 *{meta['name']}*\n\nEnjoy! 🚀",
        parse_mode="Markdown",
    )
    logger.info("User %s downloaded %s", query.from_user.id, meta["name"])


# ─── Unknown messages ─────────────────────────────────────────────
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Samajh nahi aaya 🤔\n/help likho commands dekhne ke liye."
    )


# ─── Main ────────────────────────────────────────────────────────
def main():
    init_db()  # ✅ Table create karo agar nahi hai

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("list",   list_files))
    app.add_handler(CommandHandler("delete", delete_file))
    app.add_handler(CommandHandler("help",   help_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_upload))
    app.add_handler(CallbackQueryHandler(handle_download, pattern=r"^dl:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    logger.info("Bot chal raha hai... 🚀")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
