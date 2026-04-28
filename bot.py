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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID  = int(os.environ["ADMIN_ID"])
MYSQL_URL = os.environ["MYSQL_URL"]


def get_db():
    url = MYSQL_URL.replace("mysql://", "")
    user_pass, rest = url.split("@", 1)
    user, password  = user_pass.split(":", 1)
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
                id      INT AUTO_INCREMENT PRIMARY KEY,
                file_id VARCHAR(255) NOT NULL,
                name    VARCHAR(255) NOT NULL,
                type    VARCHAR(10)  NOT NULL
            )
        """)
    conn.close()
    logger.info("Database ready ✅")


def db_save_file(file_id: str, name: str, ftype: str):
    conn = get_db()
    with conn.cursor() as cur:
        # Avoid duplicates
        cur.execute("SELECT id FROM files WHERE file_id = %s", (file_id,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO files (file_id, name, type) VALUES (%s, %s, %s)",
                (file_id, name, ftype)
            )
    conn.close()


def db_get_all_files() -> list:
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, file_id, name, type FROM files")
        rows = cur.fetchall()
    conn.close()
    return rows  # list of dicts


def db_get_file_by_id(row_id: int) -> dict | None:
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, file_id, name, type FROM files WHERE id = %s", (row_id,))
        row = cur.fetchone()
    conn.close()
    return row


def db_delete_file(row_id: int):
    meta = db_get_file_by_id(row_id)
    if not meta:
        return None
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM files WHERE id = %s", (row_id,))
    conn.close()
    return meta["name"]


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def build_file_list_keyboard():
    files = db_get_all_files()
    if not files:
        return None
    buttons = [
        # ✅ callback_data uses short int id — well within 64 byte limit
        [InlineKeyboardButton(f"📦 {row['name']}", callback_data=f"dl:{row['id']}")]
        for row in files
    ]
    return InlineKeyboardMarkup(buttons)


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


async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = build_file_list_keyboard()
    if keyboard:
        await update.message.reply_text("📂 Available files:", reply_markup=keyboard)
    else:
        await update.message.reply_text("❌ Koi file upload nahi hui abhi tak.")


async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf admin yeh kar sakta hai.")
        return

    if not context.args:
        files = db_get_all_files()
        if not files:
            await update.message.reply_text("Koi file nahi hai.")
            return
        lines = [f"`{row['id']}` — {row['name']}" for row in files]
        await update.message.reply_text(
            "Delete karne ke liye:\n`/delete <id>`\n\n" + "\n".join(lines),
            parse_mode="Markdown",
        )
        return

    try:
        row_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID number hona chahiye.")
        return

    name = db_delete_file(row_id)
    if name:
        await update.message.reply_text(f"✅ `{name}` delete ho gaya.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ ID nahi mili.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        text = (
            "🛠 *Admin Commands*\n\n"
            "📤 *File upload* — Mujhe seedha APK/ZIP bhejo, main store kar lunga\n"
            "/list — Sab files dekho\n"
            "/delete `<id>` — Koi file hatao\n"
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

    db_save_file(doc.file_id, fname, ext)

    await update.message.reply_text(
        f"✅ *{fname}* store ho gaya!\n\n"
        f"Users ab /list se download kar sakte hain. 🎉",
        parse_mode="Markdown",
    )
    logger.info("Admin uploaded: %s", fname)


async def handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, row_id_str = query.data.split(":", 1)
    meta = db_get_file_by_id(int(row_id_str))

    if not meta:
        await query.edit_message_text("❌ File nahi mili. Shayad delete ho gayi.")
        return

    await query.message.reply_document(
        document=meta["file_id"],
        caption=f"📦 *{meta['name']}*\n\nEnjoy! 🚀",
        parse_mode="Markdown",
    )
    logger.info("User %s downloaded %s", query.from_user.id, meta["name"])


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Samajh nahi aaya 🤔\n/help likho commands dekhne ke liye."
    )


def main():
    init_db()

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
