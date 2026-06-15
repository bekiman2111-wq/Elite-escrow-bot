import os
import sqlite3
import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")
PROOF_CHANNEL = os.getenv("PROOF_CHANNEL")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

ADMIN_IDS = [6138132255, 5635739078]

# ================= DB =================
conn = sqlite3.connect("escrow.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_username TEXT,
    buyer_username TEXT,
    amount TEXT,
    method TEXT,
    status TEXT,
    action_type TEXT,
    deal_message_id INTEGER,
    activation_message_id INTEGER,
    created_at REAL,
    buyer_confirmed INTEGER DEFAULT 0,
    handled_by TEXT,
    action_locked INTEGER DEFAULT 0,
    activator_admin_id INTEGER
)
""")

conn.commit()

# ================= HELPERS =================
def clean_username(u):
    return (u or "").replace("@", "").strip().lower()

def safe_user(user):
    return (user.username or str(user.id)).lower()

def deal_id(did):
    return f"#{did:03d}"

def duration(start):
    s = int(time.time() - start)
    m = s // 60
    h = m // 60
    m = m % 60
    return f"{h}h {m}m" if h else f"{m}m"

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Escrow Bot Running ✅")

# ================= COPY FORM HANDLER =================
async def copy_form(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    form_text = """🧾 DEAL FORM

@admins

Seller:
Buyer:
Amount:
Method:
"""

    await q.message.reply_text(f"<pre>{form_text}</pre>", parse_mode="HTML")

# ================= DEAL FORM =================
async def deal_form(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.message is None or update.message.text is None:
        return

    text = update.message.text

    # ================= FORM REQUEST =================
    if text.strip().lower() == "form":

        form_text = """🧾 DEAL FORM

@admins

Seller:
Buyer:
Amount:
Method:
"""

        keyboard = [[
            InlineKeyboardButton("📋 Click to Copy Form", callback_data="copy_form")
        ]]

        await update.message.reply_text(
            f"<pre>{form_text}</pre>\n\n📌 Tap and hold or press button to copy",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # ================= REAL DEAL =================
    if "buyer:" not in text.lower() or "seller:" not in text.lower():
        return

    try:
        lines = text.split("\n")
        data = {}

        for line in lines:
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip().lower()] = value.strip()

        seller = clean_username(data.get("seller"))
        buyer = clean_username(data.get("buyer"))
        amount = data.get("amount", "")
        method = data.get("method", "")

        if not seller or not buyer or not amount or not method:
            return await update.message.reply_text("❌ Invalid form")

        cursor.execute("""
        INSERT INTO deals (
            seller_username,
            buyer_username,
            amount,
            method,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """, (seller, buyer, amount, method, "PENDING", time.time()))

        conn.commit()

        did = cursor.lastrowid

        msg = await update.message.reply_text(
            f"🚨 NEW DEAL {deal_id(did)}\n\n"
            f"👤 Seller: @{seller}\n"
            f"👤 Buyer: @{buyer}\n"
            f"💰 Amount: {amount}\n"
            f"💳 Method: {method}\n\n"
            f"⏳ Waiting admin activation..."
        )

        cursor.execute("""
        UPDATE deals
        SET deal_message_id=?
        WHERE id=?
        """, (msg.message_id, did))

        conn.commit()

    except Exception as e:
        logger.error(e)

# ================= MAIN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, deal_form))

app.add_handler(CallbackQueryHandler(copy_form, pattern="^copy_form$"))

app.run_polling()
