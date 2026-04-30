import os
import logging
import pymysql
import pymysql.cursors
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

# ── Service catalogue — naam/price yahan badlo ─────────────────────────────────
SERVICES = {
    "customize": "🛠 Customize File",
    "gfx":       "🎨 Paid GFX",
    "firewall":  "🔥 File + Firewall",
}

AUTO_DELETE_SECONDS = 300  # custom file 5 min mein delete


# ═══════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

def parse_mysql_url(url: str) -> dict:
    url = url.replace("mysql://", "")
    user_pass, rest = url.split("@", 1)
    user, password = user_pass.split(":", 1)
    host_port, dbname = rest.split("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":", 1)
        port = int(port)
    else:
        host, port = host_port, 3306
    return dict(host=host, port=port, user=user, password=password, database=dbname)


DB_CFG = parse_mysql_url(MYSQL_URL)


def get_db():
    return pymysql.connect(
        **DB_CFG,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=10,
    )


def run_query(sql: str, args=None, fetch: str = "none"):
    for attempt in range(3):
        conn = None
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(sql, args or ())
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                return None
        except pymysql.err.OperationalError as e:
            logger.warning("DB error attempt %d: %s", attempt + 1, e)
            if attempt == 2:
                raise
        finally:
            if conn:
                conn.close()


def init_db():
    run_query("""
        CREATE TABLE IF NOT EXISTS files (
            id      INT AUTO_INCREMENT PRIMARY KEY,
            file_id VARCHAR(255) NOT NULL UNIQUE,
            name    VARCHAR(255) NOT NULL,
            type    VARCHAR(10)  NOT NULL
        )
    """)
    run_query("""
        CREATE TABLE IF NOT EXISTS pending_orders (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            user_id      BIGINT       NOT NULL,
            username     VARCHAR(255),
            service      VARCHAR(50)  NOT NULL,
            file_row_id  INT,
            screenshot_id VARCHAR(255),
            status       VARCHAR(20)  DEFAULT 'pending',
            created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_user (user_id)
        )
    """)
    run_query("""
        CREATE TABLE IF NOT EXISTS config (
            `key`  VARCHAR(100) PRIMARY KEY,
            value  TEXT
        )
    """)
    logger.info("Database ready ✅")


# ── files ──────────────────────────────────────────────────────────────────────

def db_save_file(file_id: str, name: str, ftype: str):
    run_query(
        "INSERT IGNORE INTO files (file_id, name, type) VALUES (%s, %s, %s)",
        (file_id, name, ftype),
    )


def db_get_all_files() -> list:
    return run_query("SELECT id, file_id, name, type FROM files", fetch="all") or []


def db_get_file_by_id(row_id: int):
    return run_query(
        "SELECT id, file_id, name, type FROM files WHERE id = %s",
        (row_id,), fetch="one",
    )


def db_delete_file(row_id: int):
    meta = db_get_file_by_id(row_id)
    if not meta:
        return None
    run_query("DELETE FROM files WHERE id = %s", (row_id,))
    return meta["name"]


# ── orders ─────────────────────────────────────────────────────────────────────

def db_save_order(user_id: int, username: str, service: str, file_row_id: int, screenshot_id: str):
    run_query(
        """
        INSERT INTO pending_orders (user_id, username, service, file_row_id, screenshot_id)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            service=%s, file_row_id=%s, screenshot_id=%s,
            status='pending', created_at=CURRENT_TIMESTAMP
        """,
        (user_id, username, service, file_row_id, screenshot_id,
         service, file_row_id, screenshot_id),
    )


def db_get_order(user_id: int):
    return run_query(
        "SELECT * FROM pending_orders WHERE user_id = %s", (user_id,), fetch="one"
    )


def db_get_pending_orders() -> list:
    return run_query(
        "SELECT * FROM pending_orders WHERE status='pending' ORDER BY created_at",
        fetch="all",
    ) or []


def db_update_order_status(user_id: int, status: str):
    run_query(
        "UPDATE pending_orders SET status=%s WHERE user_id=%s", (status, user_id)
    )


def db_delete_order(user_id: int):
    run_query("DELETE FROM pending_orders WHERE user_id=%s", (user_id,))


# ── config (QR image etc.) ─────────────────────────────────────────────────────

def db_get_config(key: str):
    row = run_query("SELECT value FROM config WHERE `key`=%s", (key,), fetch="one")
    return row["value"] if row else None


def db_set_config(key: str, value: str):
    run_query(
        "INSERT INTO config (`key`, value) VALUES (%s,%s) ON DUPLICATE KEY UPDATE value=%s",
        (key, value, value),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  STATE HELPERS  (runtime — bot_data mein store hota hai)
# ═══════════════════════════════════════════════════════════════════════════════

def get_user_state(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict:
    return context.bot_data.get("user_states", {}).get(user_id, {})


def set_user_state(context: ContextTypes.DEFAULT_TYPE, user_id: int, **kwargs):
    context.bot_data.setdefault("user_states", {}).setdefault(user_id, {}).update(kwargs)


def clear_user_state(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    context.bot_data.get("user_states", {}).pop(user_id, None)


# ═══════════════════════════════════════════════════════════════════════════════
#  MISC HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def build_file_list_keyboard() -> InlineKeyboardMarkup | None:
    files = db_get_all_files()
    if not files:
        return None
    buttons = [
        [
            InlineKeyboardButton(f"📦 {row['name']}", callback_data=f"dl:{row['id']}"),
            InlineKeyboardButton("🛒 Buy",            callback_data=f"buy:{row['id']}"),
        ]
        for row in files
    ]
    return InlineKeyboardMarkup(buttons)


async def _forward_or_copy(context: ContextTypes.DEFAULT_TYPE, msg, target_uid: int):
    """Admin ke message/file ko target user tak pohoncha do."""
    if msg.text:
        await context.bot.send_message(chat_id=target_uid, text=msg.text)
    elif msg.document:
        await context.bot.send_document(
            chat_id=target_uid, document=msg.document.file_id, caption=msg.caption or ""
        )
    elif msg.photo:
        await context.bot.send_photo(
            chat_id=target_uid, photo=msg.photo[-1].file_id, caption=msg.caption or ""
        )
    elif msg.video:
        await context.bot.send_video(
            chat_id=target_uid, video=msg.video.file_id, caption=msg.caption or ""
        )
    elif msg.audio:
        await context.bot.send_audio(
            chat_id=target_uid, audio=msg.audio.file_id, caption=msg.caption or ""
        )
    elif msg.sticker:
        await context.bot.send_sticker(chat_id=target_uid, sticker=msg.sticker.file_id)


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = build_file_list_keyboard()
    if keyboard:
        await update.message.reply_text(
            f"👋 Namaste {user.first_name}!\n\n"
            "Neeche available files hain.\n"
            "📦 Download karo ya 🛒 Buy karo 👇",
            reply_markup=keyboard,
        )
    else:
        await update.message.reply_text(
            f"👋 Namaste {user.first_name}!\n\nAbhi koi file available nahi hai. 🙏"
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
            "Delete ke liye:\n`/delete <id>`\n\n" + "\n".join(lines),
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
            "📤 *File upload* — APK/ZIP seedha bhejo\n"
            "/list — Sab files dekho\n"
            "/delete `<id>` — File hatao\n"
            "/setqr — Payment QR image set karo\n"
            "/approve `<user\\_id>` — Payment approve karo\n"
            "/sendto `<user\\_id>` — Agla msg/file us user ko bhejo\n"
            "/customfile `<user\\_id>` — Next file custom user ko jayegi\n"
            "/cancelsend — Active sendto/customfile cancel karo\n"
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


async def setqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin QR image set kare."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only.")
        return
    context.bot_data["admin_state"] = "awaiting_qr"
    await update.message.reply_text(
        "📸 Ab QR wali photo bhejo — main store kar lunga.\nCancel ke liye kuch bhi /help bhejo."
    )


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin payment approve kare."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only.")
        return

    # Agar user_id nahi diya → pending list dikhao
    if not context.args:
        orders = db_get_pending_orders()
        if not orders:
            await update.message.reply_text("✅ Koi pending order nahi hai.")
            return
        lines = [
            f"`{o['user_id']}` — @{o['username'] or 'N/A'} — {SERVICES.get(o['service'], o['service'])}"
            for o in orders
        ]
        await update.message.reply_text(
            "📋 *Pending Orders:*\n\n" + "\n".join(lines) + "\n\n`/approve <user_id>`",
            parse_mode="Markdown",
        )
        return

    try:
        target_uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id number hona chahiye.")
        return

    order = db_get_order(target_uid)
    if not order:
        await update.message.reply_text("❌ Is user ka koi pending order nahi mila.")
        return

    service  = order["service"]
    svc_name = SERVICES.get(service, service)
    db_update_order_status(target_uid, "approved")

    # ── Customize File ─────────────────────────────────────────────────────────
    if service == "customize":
        set_user_state(context, target_uid,
                       state="awaiting_features",
                       service=service,
                       file_row_id=order["file_row_id"])
        await context.bot.send_message(
            chat_id=target_uid,
            text=(
                "✅ *Payment verify ho gaya!* 🎉\n\n"
                "🛠 *Customize File* order confirm hai.\n\n"
                "Ab batao — kya kya *features* chahiye?\n"
                "Detail mein likhke bhejo 👇"
            ),
            parse_mode="Markdown",
        )
        await update.message.reply_text(
            f"✅ Approved! User `{target_uid}` ko features maanga hai.",
            parse_mode="Markdown",
        )

    # ── Paid GFX / File+Firewall ───────────────────────────────────────────────
    elif service in ("gfx", "firewall"):
        await context.bot.send_message(
            chat_id=target_uid,
            text=(
                f"✅ *Payment verify ho gaya!* 🎉\n\n"
                f"*{svc_name}* taiyar ho raha hai.\n"
                "Thodi der mein milega — wait karo! ⏳"
            ),
            parse_mode="Markdown",
        )
        await update.message.reply_text(
            f"✅ Approved! Ab `/sendto {target_uid}` karo aur agla message/file us user ko jayega.",
            parse_mode="Markdown",
        )

    else:
        await update.message.reply_text(f"⚠️ Unknown service `{service}`.", parse_mode="Markdown")


async def sendto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sendto <user_id> — agla message/file us user ko forward karo."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/sendto <user_id>`", parse_mode="Markdown")
        return
    try:
        target_uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id number hona chahiye.")
        return

    context.bot_data["sendto_user"] = target_uid
    context.bot_data.pop("customfile_user", None)  # conflict avoid karo
    await update.message.reply_text(
        f"✅ Ready!\nAb jo bhi bhejoge — user `{target_uid}` ko jayega.\n"
        "Cancel ke liye `/cancelsend`",
        parse_mode="Markdown",
    )


async def customfile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/customfile <user_id> — next document us user ko bhejo (auto-delete)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only.")
        return

    if context.args:
        try:
            target_uid = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ user_id number hona chahiye.")
            return
    else:
        # Pehle approved customize order dhundho
        row = run_query(
            "SELECT user_id FROM pending_orders WHERE service='customize' AND status='approved' "
            "ORDER BY created_at LIMIT 1",
            fetch="one",
        )
        if not row:
            await update.message.reply_text(
                "❌ Koi user custom file ka wait nahi kar raha.\n"
                "Usage: `/customfile <user_id>`",
                parse_mode="Markdown",
            )
            return
        target_uid = row["user_id"]

    context.bot_data["customfile_user"] = target_uid
    context.bot_data.pop("sendto_user", None)  # conflict avoid karo
    await update.message.reply_text(
        f"✅ Ready!\nAb jo *file/document* bhejoge — user `{target_uid}` ko jayegi.\n"
        f"⚠️ File {AUTO_DELETE_SECONDS // 60} min mein auto-delete ho jayegi.\n"
        "Cancel ke liye `/cancelsend`",
        parse_mode="Markdown",
    )


async def cancelsend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only.")
        return
    context.bot_data.pop("sendto_user", None)
    context.bot_data.pop("customfile_user", None)
    await update.message.reply_text("❌ Cancelled. Sab normal ho gaya.")


# ═══════════════════════════════════════════════════════════════════════════════
#  CALLBACK HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📦 Download button."""
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


async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🛒 Buy button — service options dikhao."""
    query = update.callback_query
    await query.answer()
    _, row_id_str = query.data.split(":", 1)
    meta = db_get_file_by_id(int(row_id_str))
    if not meta:
        await query.answer("❌ File nahi mili.", show_alert=True)
        return

    buttons = [
        [InlineKeyboardButton(name, callback_data=f"svc:{key}:{row_id_str}")]
        for key, name in SERVICES.items()
    ]
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await query.message.reply_text(
        f"🛒 *{meta['name']}* ke liye service chuno:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


async def handle_service_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Service select → QR + instructions bhejo."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 2)
    _, service, row_id_str = parts
    user_id  = query.from_user.id
    svc_name = SERVICES.get(service, service)
    qr_fid   = db_get_config("qr_file_id")

    # User ko awaiting_screenshot state mein daalo
    set_user_state(context, user_id,
                   state="awaiting_screenshot",
                   service=service,
                   file_row_id=int(row_id_str))

    await query.message.reply_text(
        f"💳 *{svc_name}* — Payment Process\n\n"
        "Neeche QR se payment karo, phir *screenshot* is chat mein bhejo 👇",
        parse_mode="Markdown",
    )

    if qr_fid:
        await query.message.reply_photo(
            photo=qr_fid,
            caption="📲 Yeh QR scan karo → payment karo → screenshot bhejo ✅",
        )
    else:
        await query.message.reply_text(
            "⚠️ QR abhi set nahi hai. Admin se contact karo.\n"
            "Admin: `/setqr` use karo.",
        )

    # Service selection buttons hata do
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def handle_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Cancelled ❌")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN MESSAGE ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sab non-command messages yahan aate hain — state ke hisaab se route hote hain."""
    if not update.message:
        return

    user_id = update.effective_user.id
    msg     = update.message

    # ══ ADMIN FLOWS ════════════════════════════════════════════════════════════
    if is_admin(user_id):

        # 1. QR set karna
        if context.bot_data.get("admin_state") == "awaiting_qr":
            if msg.photo:
                fid = msg.photo[-1].file_id
                db_set_config("qr_file_id", fid)
                context.bot_data.pop("admin_state", None)
                await msg.reply_text("✅ QR image store ho gaya! Ab users ko dikhega.")
            else:
                await msg.reply_text("⚠️ Photo chahiye. QR ki photo bhejo.")
            return

        # 2. sendto active — agla message/file target user ko bhejo
        sendto_uid = context.bot_data.get("sendto_user")
        if sendto_uid:
            try:
                await _forward_or_copy(context, msg, sendto_uid)
                db_delete_order(sendto_uid)
            except Exception as e:
                logger.error("sendto error: %s", e)
                await msg.reply_text(f"❌ Send nahi ho saka: {e}")
                return
            context.bot_data.pop("sendto_user", None)
            await msg.reply_text(
                f"✅ User `{sendto_uid}` ko bhej diya!", parse_mode="Markdown"
            )
            return

        # 3. customfile active — next document custom user ko bhejo (auto-delete)
        customfile_uid = context.bot_data.get("customfile_user")
        if customfile_uid:
            if not msg.document:
                await msg.reply_text(
                    "⚠️ Document/file chahiye custom delivery ke liye. "
                    "Cancel ke liye `/cancelsend`",
                    parse_mode="Markdown",
                )
                return
            try:
                sent = await context.bot.send_document(
                    chat_id=customfile_uid,
                    document=msg.document.file_id,
                    caption=(
                        f"📦 *{msg.document.file_name or 'Custom File'}*\n\n"
                        "Tera custom order taiyar hai! 🎉\n"
                        f"⚠️ Yeh file {AUTO_DELETE_SECONDS // 60} minute mein delete ho jayegi."
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error("customfile send error: %s", e)
                await msg.reply_text(f"❌ File send nahi ho saki: {e}")
                return

            context.bot_data.pop("customfile_user", None)
            clear_user_state(context, customfile_uid)
            db_delete_order(customfile_uid)

            await msg.reply_text(
                f"✅ Custom file user `{customfile_uid}` ko bhej di!\n"
                f"⏱ {AUTO_DELETE_SECONDS // 60} min mein auto-delete hogi.",
                parse_mode="Markdown",
            )

            # Auto-delete job schedule karo
            context.job_queue.run_once(
                _delete_message_job,
                when=AUTO_DELETE_SECONDS,
                data={"chat_id": customfile_uid, "message_id": sent.message_id},
                name=f"del_{customfile_uid}_{sent.message_id}",
            )
            return

        # 4. Normal admin file upload
        if msg.document:
            await _handle_upload(update, context)
            return

        await msg.reply_text("Samajh nahi aaya 🤔\n/help likho.")
        return

    # ══ USER FLOWS ═════════════════════════════════════════════════════════════
    user_state = get_user_state(context, user_id)
    state      = user_state.get("state")

    # Awaiting screenshot
    if state == "awaiting_screenshot":
        if msg.photo or (msg.document and msg.document.mime_type and "image" in msg.document.mime_type):
            await _handle_screenshot(update, context, user_id, user_state)
        else:
            await msg.reply_text("📸 Payment ka *screenshot (photo)* bhejo.", parse_mode="Markdown")
        return

    # Awaiting features (after customize approve)
    if state == "awaiting_features":
        if msg.text:
            await _handle_features(update, context, user_id, user_state)
        else:
            await msg.reply_text("📝 Features *text mein* likhke bhejo.", parse_mode="Markdown")
        return

    # Default
    await msg.reply_text("Samajh nahi aaya 🤔\n/help likho commands dekhne ke liye.")


# ═══════════════════════════════════════════════════════════════════════════════
#  INNER HANDLERS  (routed from handle_any_message)
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_screenshot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    user_state: dict,
):
    """User ne payment screenshot bheja."""
    msg         = update.message
    service     = user_state.get("service", "unknown")
    file_row_id = user_state.get("file_row_id")
    username    = update.effective_user.username
    svc_name    = SERVICES.get(service, service)

    ss_fid = msg.photo[-1].file_id if msg.photo else msg.document.file_id

    # DB mein order save karo
    db_save_order(user_id, username, service, file_row_id, ss_fid)
    clear_user_state(context, user_id)

    # Admin ko forward karo
    file_meta = db_get_file_by_id(file_row_id) if file_row_id else None
    file_name = file_meta["name"] if file_meta else "N/A"

    caption = (
        f"💰 *New Payment Screenshot*\n\n"
        f"👤 User: `{user_id}` (@{username or 'N/A'})\n"
        f"📦 File: {file_name}\n"
        f"🛠 Service: {svc_name}\n\n"
        f"Approve: `/approve {user_id}`"
    )
    if msg.photo:
        await context.bot.send_photo(
            chat_id=ADMIN_ID, photo=ss_fid, caption=caption, parse_mode="Markdown"
        )
    else:
        await context.bot.send_document(
            chat_id=ADMIN_ID, document=ss_fid, caption=caption, parse_mode="Markdown"
        )

    await msg.reply_text(
        "✅ *Screenshot mil gaya!*\n\n"
        "Payment verify ho rahi hai... ⏳\n"
        "Admin approve karega toh notify karenge. 🙏",
        parse_mode="Markdown",
    )
    logger.info("User %s submitted payment SS for service %s", user_id, service)


async def _handle_features(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    user_state: dict,
):
    """User ne customize features text bheja."""
    msg         = update.message
    username    = update.effective_user.username
    file_row_id = user_state.get("file_row_id")

    file_meta = db_get_file_by_id(file_row_id) if file_row_id else None
    file_name = file_meta["name"] if file_meta else "N/A"

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"📋 *Customize Request*\n\n"
            f"👤 User: `{user_id}` (@{username or 'N/A'})\n"
            f"📦 File: {file_name}\n\n"
            f"*Features requested:*\n{msg.text}\n\n"
            f"File ready hone par: `/customfile {user_id}`"
        ),
        parse_mode="Markdown",
    )

    clear_user_state(context, user_id)

    await msg.reply_text(
        "✅ *Features note kar liye!* 📝\n\n"
        "Admin abhi kaam shuru karega.\n"
        "File taiyar hone par yahan bhej denge. 🚀",
        parse_mode="Markdown",
    )
    logger.info("User %s submitted features for customize", user_id)


async def _handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin ne normal APK/ZIP upload kiya."""
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
        f"✅ *{fname}* store ho gaya!\n\nUsers ab /list se download kar sakte hain. 🎉",
        parse_mode="Markdown",
    )
    logger.info("Admin uploaded: %s", fname)


# ═══════════════════════════════════════════════════════════════════════════════
#  JOB
# ═══════════════════════════════════════════════════════════════════════════════

async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job — custom file auto-delete."""
    data = context.job.data
    try:
        await context.bot.delete_message(
            chat_id=data["chat_id"], message_id=data["message_id"]
        )
        logger.info("Auto-deleted msg %s for chat %s", data["message_id"], data["chat_id"])
    except Exception as e:
        logger.warning("Auto-delete failed: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("list",       list_files))
    app.add_handler(CommandHandler("delete",     delete_file))
    app.add_handler(CommandHandler("help",       help_cmd))
    app.add_handler(CommandHandler("setqr",      setqr_cmd))
    app.add_handler(CommandHandler("approve",    approve_cmd))
    app.add_handler(CommandHandler("sendto",     sendto_cmd))
    app.add_handler(CommandHandler("customfile", customfile_cmd))
    app.add_handler(CommandHandler("cancelsend", cancelsend_cmd))

    # ── Inline button callbacks ───────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(handle_download,       pattern=r"^dl:"))
    app.add_handler(CallbackQueryHandler(handle_buy,            pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(handle_service_select, pattern=r"^svc:"))
    app.add_handler(CallbackQueryHandler(handle_cancel_callback, pattern=r"^cancel$"))

    # ── All non-command messages (photos, docs, text) → single router ─────────
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_any_message))

    logger.info("Bot chal raha hai... 🚀")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
