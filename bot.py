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

if not TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

# ================= DATABASE =================
conn = sqlite3.connect("escrow.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller TEXT,
    buyer TEXT,
    amount TEXT,
    method TEXT,
    status TEXT DEFAULT 'ACTIVE',
    created_by TEXT,
    created_at REAL,
    dispute INTEGER DEFAULT 0
)
""")
conn.commit()

# ================= HELPERS =================
def deal_id(did):
    return f"#{did:03d}"


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💼 ESCROW BOT ACTIVE\n\nType /form to get deal template."
    )


# ================= FORM =================
async def form(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """@admins

Seller:
Buyer:
Amount:
Method:
"""

    await update.message.reply_text(text)


# ================= PARSER =================
def parse_form(text: str):
    data = {}

    for line in text.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip().lower()] = value.strip()

    return data


# ================= DEAL HANDLER =================
async def deal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text
    data = parse_form(text)

    required = ["seller", "buyer", "amount", "method"]

    if not all(k in data and data[k] for k in required):
        return

    cursor.execute("""
    INSERT INTO deals (seller, buyer, amount, method, status, created_by, created_at)
    VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)
    """, (
        data["seller"],
        data["buyer"],
        data["amount"],
        data["method"],
        str(update.effective_user.id),
        time.time()
    ))

    conn.commit()

    did = cursor.lastrowid

    keyboard = [
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"accept_{did}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{did}")
        ],
        [
            InlineKeyboardButton("🛡 Cancel", callback_data=f"cancel_{did}")
        ]
    ]

    await update.message.reply_text(
        f"""🚨 NEW DEAL CREATED

🆔 {deal_id(did)}

👤 Seller: {data['seller']}
👤 Buyer: {data['buyer']}
💰 Amount: {data['amount']}
💳 Method: {data['method']}

💼 STATUS: ACTIVE""",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ================= BUYER ACCEPT =================
async def accept_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    did = int(q.data.split("_")[1])

    cursor.execute("""
    UPDATE deals
    SET status='ACCEPTED'
    WHERE id=?
    """, (did,))

    conn.commit()

    await q.message.reply_text(f"✅ Deal #{did} ACCEPTED by buyer")


# ================= BUYER REJECT =================
async def reject_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    did = int(q.data.split("_")[1])

    cursor.execute("""
    UPDATE deals
    SET status='REJECTED'
    WHERE id=?
    """, (did,))

    conn.commit()

    await q.message.reply_text(f"❌ Deal #{did} REJECTED by buyer")


# ================= CANCEL =================
async def cancel_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    did = int(q.data.split("_")[1])

    cursor.execute("""
    UPDATE deals
    SET status='CANCELLED'
    WHERE id=?
    """, (did,))

    conn.commit()

    await q.message.reply_text(f"🛡 Deal #{did} CANCELLED")


# ================= ROUTER =================
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text

    if text and text.lower() == "form":
        await form(update, context)
        return

    # try auto-create deal
    await deal_handler(update, context)


# ================= MAIN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("form", form))

app.add_handler(CallbackQueryHandler(accept_deal, pattern="^accept_"))
app.add_handler(CallbackQueryHandler(reject_deal, pattern="^reject_"))
app.add_handler(CallbackQueryHandler(cancel_deal, pattern="^cancel_"))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))

app.run_polling()
