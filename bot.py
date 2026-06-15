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
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")

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
    currency TEXT,
    method TEXT,
    status TEXT,
    created_at REAL,
    activator_admin_id INTEGER,
    handled_by TEXT,
    deal_message_id INTEGER
)
""")
conn.commit()

# ================= HELPERS =================
def clean(u):
    return (u or "").replace("@", "").strip().lower()

def safe(user):
    return (user.username or str(user.id)).lower()

def did(d): return f"#{d:03d}"

def duration(t):
    s = int(time.time() - t)
    m = s // 60
    h = m // 60
    return f"{h}h {m % 60}m" if h else f"{m}m"

# ================= PROOF =================
async def send_proof(context, text):
    for chat in [GROUP_CHAT_ID, PROOF_CHANNEL]:
        if chat:
            try:
                await context.bot.send_message(chat_id=chat, text=text)
            except Exception as e:
                logger.error(e)

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Escrow Bot Running ✅")

# ================= FORM =================
async def deal_form(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    if text.lower() == "form":
        await update.message.reply_text(
            "@admins\n\nSeller:\nBuyer:\nAmount:\nCurrency:\nMethod:"
        )
        return

    if "seller:" not in text.lower():
        return

    data = {}
    for line in text.split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            data[k.lower().strip()] = v.strip()

    seller = clean(data.get("seller"))
    buyer = clean(data.get("buyer"))
    amount = data.get("amount", "")
    currency = data.get("currency", "").upper()
    method = data.get("method", "")

    if not all([seller, buyer, amount, currency, method]):
        return await update.message.reply_text("❌ Invalid form")

    cursor.execute("""
    INSERT INTO deals
    (seller_username,buyer_username,amount,currency,method,status,created_at)
    VALUES (?,?,?,?,?,?,?)
    """, (seller, buyer, amount, currency, method, "PENDING", time.time()))
    conn.commit()

    did_id = cursor.lastrowid

    kb = [[
        InlineKeyboardButton("✅ Activate", callback_data=f"activate_{did_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"admincancel_{did_id}")
    ]]

    await update.message.reply_text(
        f"🚨 NEW DEAL {did(did_id)}\n"
        f"Seller: @{seller}\nBuyer: @{buyer}\n"
        f"Amount: {amount} {currency}\nMethod: {method}",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ================= ADMIN BUTTONS (FIXED) =================
async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    if q.from_user.id not in ADMIN_IDS:
        return await q.answer("Admin only", show_alert=True)

    try:
        action, did_id = q.data.split("_")
        did_id = int(did_id)

        # ================= ACTIVATE =================
        if action == "activate":

            cursor.execute("""
            SELECT seller_username,buyer_username,amount,currency,method
            FROM deals WHERE id=?
            """, (did_id,))

            row = cursor.fetchone()
            if not row:
                return await q.answer("Deal not found", show_alert=True)

            seller, buyer, amount, currency, method = row

            kb = [[
                InlineKeyboardButton("💸 Release", callback_data=f"release_{did_id}"),
                InlineKeyboardButton("♻ Refund", callback_data=f"refund_{did_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{did_id}")
            ]]

            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=(
                    f"⚠ @{seller} pls choose action\n\n"
                    f"🆔 {did(did_id)}\n"
                    f"Buyer: @{buyer}\n"
                    f"Amount: {amount} {currency}"
                ),
                reply_markup=InlineKeyboardMarkup(kb)
            )

            cursor.execute("""
            UPDATE deals SET status=?, activator_admin_id=?
            WHERE id=?
            """, ("ACTIVE", q.from_user.id, did_id))

            conn.commit()

            await q.edit_message_text(f"✅ Activated {did(did_id)}")
            return

        # ================= CANCEL =================
        if action == "admincancel":

            cursor.execute("""
            UPDATE deals SET status=?, handled_by=?
            WHERE id=?
            """, ("CANCELLED", safe(q.from_user), did_id))

            conn.commit()

            await q.edit_message_text(f"❌ Cancelled {did(did_id)}")
            return

    except Exception as e:
        logger.error(e)
        await q.answer("Error", show_alert=True)

# ================= SELLER =================
async def seller_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    action, did_id = q.data.split("_")
    did_id = int(did_id)

    cursor.execute("""
    SELECT seller_username,buyer_username
    FROM deals WHERE id=?
    """, (did_id,))
    seller, buyer = cursor.fetchone()

    if clean(q.from_user.username) != seller:
        return await q.answer("Only seller", show_alert=True)

    kb = [[
        InlineKeyboardButton("✅ Accept", callback_data=f"buyerok_{did_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"buyerrej_{did_id}")
    ]]

    await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=f"⚠ @{buyer} pls confirm seller action {action}",
        reply_markup=InlineKeyboardMarkup(kb)
    )

    cursor.execute("UPDATE deals SET status=? WHERE id=?", (action.upper(), did_id))
    conn.commit()

# ================= BUYER =================
async def buyer_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    action, did_id = q.data.split("_")
    did_id = int(did_id)

    cursor.execute("""
    SELECT buyer_username,seller_username,amount,currency,method,created_at,activator_admin_id
    FROM deals WHERE id=?
    """, (did_id,))

    buyer, seller, amount, currency, method, created, admin = cursor.fetchone()

    if clean(q.from_user.username) != buyer:
        return await q.answer("Only buyer", show_alert=True)

    if action == "buyerrej":
        return await q.edit_message_text("Rejected")

    # ================= FINAL ADMIN APPROVAL =================
    kb = [[
        InlineKeyboardButton("✅ Final Approve", callback_data=f"finalok_{did_id}"),
        InlineKeyboardButton("❌ Final Reject", callback_data=f"finalno_{did_id}")
    ]]

    await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=(
            f"📌 FINAL APPROVAL REQUIRED\n\n"
            f"🆔 {did(did_id)}\n"
            f"Buyer: @{buyer}\nSeller: @{seller}\n"
            f"Amount: {amount} {currency}"
        ),
        reply_markup=InlineKeyboardMarkup(kb)
    )

    cursor.execute("UPDATE deals SET status=? WHERE id=?", (action.upper(), did_id))
    conn.commit()

# ================= FINAL ADMIN =================
async def final_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    action, did_id = q.data.split("_")
    did_id = int(did_id)

    cursor.execute("""
    SELECT buyer_username,seller_username,amount,currency,method,created_at,handled_by
    FROM deals WHERE id=?
    """, (did_id,))

    buyer, seller, amount, currency, method, created, handled = cursor.fetchone()

    if action == "finalno":
        await q.edit_message_text("Rejected by admin")
        return

    text = (
        f"📢 SUCCESSFUL DEAL\n\n"
        f"🛒 Buyer: @{buyer}\n"
        f"🏪 Seller: @{seller}\n"
        f"💰 Amount: {amount}\n"
        f"💱 Currency: {currency}\n"
        f"💳 Method: {method}\n"
        f"⏱ Duration: {duration(created)}\n"
        f"🛡 Handled By: @{safe(q.from_user)}"
    )

    await send_proof(context, text)
    await q.edit_message_text("Posted to group + proof")

# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, deal_form))

app.add_handler(CallbackQueryHandler(admin_buttons, pattern="^(activate|admincancel)_"))
app.add_handler(CallbackQueryHandler(seller_buttons, pattern="^(release|refund|cancel)_"))
app.add_handler(CallbackQueryHandler(buyer_buttons, pattern="^(buyerok|buyerrej)_"))
app.add_handler(CallbackQueryHandler(final_approval, pattern="^(finalok|finalno)_"))

print("BOT RUNNING")
app.run_polling()
