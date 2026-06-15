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
    status TEXT DEFAULT 'DRAFT',
    created_by TEXT,
    created_at REAL,
    dispute INTEGER DEFAULT 0
)
""")
conn.commit()

# ================= STATE =================
user_state = {}

# ================= HELPERS =================
def deal_id(did):
    return f"#{did:03d}"


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        [InlineKeyboardButton("🧾 Create Deal", callback_data="start_deal")]
    ]

    await update.message.reply_text(
        "💼 ESCROW BOT\nChoose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ================= FORM (FIXED - ONLY TEMPLATE) =================
async def form(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """@admins

Seller:
Buyer:
Amount:
Method:
"""

    await update.message.reply_text(text)


# ================= DEAL WIZARD (ONLY FOR CREATE DEAL BUTTON) =================
async def start_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    user_state[q.from_user.id] = {
        "step": "seller",
        "data": {}
    }

    await q.message.reply_text("👤 Send Seller username:")


async def deal_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    text = update.message.text

    if user_id not in user_state:
        return

    state = user_state[user_id]
    step = state["step"]

    if step == "seller":
        state["data"]["seller"] = text
        state["step"] = "buyer"
        return await update.message.reply_text("👤 Send Buyer username:")

    if step == "buyer":
        state["data"]["buyer"] = text
        state["step"] = "amount"
        return await update.message.reply_text("💰 Send Amount:")

    if step == "amount":
        state["data"]["amount"] = text
        state["step"] = "method"
        return await update.message.reply_text("💳 Send Method:")

    if step == "method":
        state["data"]["method"] = text

        d = state["data"]
        user_state.pop(user_id)

        keyboard = [
            [InlineKeyboardButton("✅ Confirm Deal", callback_data="confirm_deal")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_deal")]
        ]

        await update.message.reply_text(
            f"""🚨 DEAL PREVIEW

👤 Seller: {d['seller']}
👤 Buyer: {d['buyer']}
💰 Amount: {d['amount']}
💳 Method: {d['method']}

⚠ Confirm to create deal""",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ================= CONFIRM DEAL =================
async def confirm_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    user_id = q.from_user.id
    data = user_state.get(user_id, {}).get("data", {})

    cursor.execute("""
    INSERT INTO deals (seller, buyer, amount, method, status, created_by, created_at)
    VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)
    """, (
        data.get("seller"),
        data.get("buyer"),
        data.get("amount"),
        data.get("method"),
        str(user_id),
        time.time()
    ))

    conn.commit()

    did = cursor.lastrowid

    await q.message.reply_text(
        f"✅ DEAL CREATED\n\n🆔 {deal_id(did)}\n\n💼 ACTIVE"
    )


# ================= CANCEL =================
async def cancel_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    user_state.pop(q.from_user.id, None)

    await q.message.reply_text("❌ Deal cancelled")


# ================= PROFILE =================
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = str(update.effective_user.id)

    cursor.execute("SELECT status FROM deals WHERE created_by=?", (user,))
    rows = cursor.fetchall()

    await update.message.reply_text(
        f"""👤 PROFILE

📦 Total Deals: {len(rows)}
✅ Active: {sum(1 for r in rows if r[0]=='ACTIVE')}
❌ Cancelled: {sum(1 for r in rows if r[0]=='CANCELLED')}
"""
    )


# ================= MY DEALS =================
async def mydeals(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = str(update.effective_user.id)

    cursor.execute("""
    SELECT id, seller, buyer, amount, method, status
    FROM deals WHERE created_by=?
    ORDER BY id DESC
    """, (user,))

    rows = cursor.fetchall()

    if not rows:
        return await update.message.reply_text("No deals found.")

    text = "📊 YOUR DEALS\n\n"

    for r in rows:
        did, seller, buyer, amount, method, status = r

        text += (
            f"🆔 {deal_id(did)}\n"
            f"👤 {seller} → {buyer}\n"
            f"💰 {amount} | 💳 {method}\n"
            f"📌 {status}\n\n"
        )

    await update.message.reply_text(text)


# ================= DISPUTE =================
async def dispute(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        return await update.message.reply_text("Usage: /dispute <deal_id>")

    did = context.args[0]

    cursor.execute("""
    UPDATE deals
    SET status='DISPUTE', dispute=1
    WHERE id=?
    """, (did,))

    conn.commit()

    await update.message.reply_text(f"⚠ Deal #{did} is now under dispute")


# ================= ROUTER =================
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text

    if text and text.lower() == "form":
        await form(update, context)


# ================= MAIN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("profile", profile))
app.add_handler(CommandHandler("mydeals", mydeals))
app.add_handler(CommandHandler("dispute", dispute))

app.add_handler(CallbackQueryHandler(start_deal, pattern="^start_deal$"))
app.add_handler(CallbackQueryHandler(confirm_deal, pattern="^confirm_deal$"))
app.add_handler(CallbackQueryHandler(cancel_deal, pattern="^cancel_deal$"))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, deal_wizard))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))

app.run_polling()
