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
    currency TEXT,
    method TEXT,
    status TEXT,
    created_at REAL,
    activator_admin_id INTEGER,
    handled_by TEXT,
    deal_message_id INTEGER,
    control_message_id INTEGER
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

def duration(created):
    seconds = int(time.time() - created)
    minutes = seconds // 60
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m" if hours else f"{minutes}m"

# ================= PROOF =================
async def send_to_proof(context, text):
    if not PROOF_CHANNEL:
        return
    try:
        chat_id = PROOF_CHANNEL
        if isinstance(chat_id, str) and not chat_id.startswith("@") and not chat_id.startswith("-100"):
            chat_id = "@" + chat_id

        await context.bot.send_message(chat_id=chat_id, text=text)
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

    # FORM TEMPLATE
    if text.lower() == "form":
        await update.message.reply_text(
            "@admins\n\nSeller:\nBuyer:\nAmount:\nCurrency:\nMethod:"
        )
        return

    # IGNORE NON FORM
    if "seller:" not in text.lower() or "buyer:" not in text.lower():
        return

    try:
        data = {}
        for line in text.split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip().lower()] = v.strip()

        seller = clean_username(data.get("seller"))
        buyer = clean_username(data.get("buyer"))
        amount = data.get("amount", "")
        currency = data.get("currency", "").upper()
        method = data.get("method", "")

        if not all([seller, buyer, amount, currency, method]):
            return await update.message.reply_text("❌ Invalid form")

        cursor.execute("""
        INSERT INTO deals (
            seller_username,
            buyer_username,
            amount,
            currency,
            method,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (seller, buyer, amount, currency, method, "PENDING", time.time()))

        conn.commit()
        did = cursor.lastrowid

        keyboard = [[
            InlineKeyboardButton("✅ Activate", callback_data=f"activate_{did}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"admincancel_{did}")
        ]]

        await update.message.reply_text(
            f"🚨 NEW DEAL {deal_id(did)}\n\n"
            f"Seller: @{seller}\n"
            f"Buyer: @{buyer}\n"
            f"Amount: {amount} {currency}\n"
            f"Method: {method}\n\n"
            f"Waiting admin...",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(e)

# ================= ADMIN =================
async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    if q.from_user.id not in ADMIN_IDS:
        return await q.answer("Admin only", show_alert=True)

    action, did = q.data.split("_")
    did = int(did)

    if action == "activate":

        cursor.execute("""
        SELECT seller_username, buyer_username, amount, currency, method
        FROM deals WHERE id=?
        """, (did,))
        seller, buyer, amount, currency, method = cursor.fetchone()

        keyboard = [[
            InlineKeyboardButton("💸 Release", callback_data=f"release_{did}"),
            InlineKeyboardButton("♻ Refund", callback_data=f"refund_{did}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{did}")
        ]]

        msg = await q.message.reply_text(
            f"✅ DEAL ACTIVE {deal_id(did)}\n\n"
            f"Seller: @{seller}\nBuyer: @{buyer}\n"
            f"{amount} {currency}\n{method}\n\nSeller actions enabled",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        cursor.execute("""
        UPDATE deals SET status=?, activator_admin_id=?, control_message_id=?
        WHERE id=?
        """, ("ACTIVE", q.from_user.id, msg.message_id, did))
        conn.commit()

        await q.edit_message_text(f"Activated {deal_id(did)}")

    elif action == "admincancel":

        cursor.execute("""
        UPDATE deals SET status=?, handled_by=?
        WHERE id=?
        """, ("CANCELLED", safe_user(q.from_user), did))
        conn.commit()

        await q.edit_message_text("Cancelled")

        cursor.execute("""
        SELECT seller_username, buyer_username, amount, currency, method
        FROM deals WHERE id=?
        """, (did,))
        seller, buyer, amount, currency, method = cursor.fetchone()

        await send_to_proof(
            context,
            f"❌ CANCELLED DEAL\n\n"
            f"🆔 {deal_id(did)}\n"
            f"🛒 Buyer: @{buyer}\n🏪 Seller: @{seller}\n"
            f"💰 {amount} {currency}\n💳 {method}"
        )

# ================= SELLER =================
async def seller_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    action, did = q.data.split("_")
    did = int(did)

    cursor.execute("""
    SELECT seller_username, buyer_username
    FROM deals WHERE id=?
    """, (did,))
    seller, buyer = cursor.fetchone()

    if clean_username(q.from_user.username) != seller:
        return await q.answer("Only seller", show_alert=True)

    cursor.execute("""
    UPDATE deals SET status=? WHERE id=?
    """, (action.upper(), did))
    conn.commit()

    keyboard = [[
        InlineKeyboardButton("✅ Accept", callback_data=f"buyerok_{did}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"buyerrej_{did}")
    ]]

    await q.message.reply_text(
        f"Seller action: {action}\nBuyer must confirm",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= BUYER =================
async def buyer_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    action, did = q.data.split("_")
    did = int(did)

    cursor.execute("""
    SELECT buyer_username, seller_username, amount, currency, method, status, created_at
    FROM deals WHERE id=?
    """, (did,))
    buyer, seller, amount, currency, method, status, created = cursor.fetchone()

    if clean_username(q.from_user.username) != buyer:
        return await q.answer("Only buyer", show_alert=True)

    if action == "buyerrej":
        return await q.edit_message_text("Rejected")

    if status == "RELEASE":
        final = "SUCCESSFUL"
    elif status == "REFUND":
        final = "REFUNDED"
    else:
        final = "CANCELLED"

    cursor.execute("""
    UPDATE deals SET status=?, handled_by=?
    WHERE id=?
    """, (final, safe_user(q.from_user), did))
    conn.commit()

    await q.edit_message_text(f"Completed: {final}")

    proof = (
        f"📢 {final} DEAL\n\n"
        f"🛒 Buyer: @{buyer}\n"
        f"🏪 Seller: @{seller}\n"
        f"💰 Amount: {amount}\n"
        f"💱 Currency: {currency}\n"
        f"💳 Method: {method}\n"
        f"⏱ Duration: {duration(created)}\n"
        f"🛡 Handled By: @{safe_user(q.from_user)}"
    )

    await send_to_proof(context, proof)

# ================= EDIT =================
async def edit_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("Admin only")

    did = int(context.args[0])
    field = context.args[1]
    value = " ".join(context.args[2:])

    if field not in ["amount", "currency", "method"]:
        return await update.message.reply_text("Invalid field")

    cursor.execute(f"UPDATE deals SET {field}=? WHERE id=?", (value, did))
    conn.commit()

    await update.message.reply_text("Updated")

# ================= STATS =================
async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):

    u = clean_username(update.effective_user.username)

    cursor.execute("""
    SELECT COUNT(*) FROM deals
    WHERE (seller_username=? OR buyer_username=?)
    AND status='SUCCESSFUL'
    """, (u, u))

    total = cursor.fetchone()[0]

    await update.message.reply_text(f"My deals: {total}")

async def mee(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id not in ADMIN_IDS:
        return

    u = clean_username(update.effective_user.username)

    cursor.execute("""
    SELECT COUNT(*) FROM deals
    WHERE handled_by=?
    AND status='SUCCESSFUL'
    """, (u,))

    total = cursor.fetchone()[0]

    await update.message.reply_text(f"Admin deals: {total}")

# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("edit", edit_deal))
app.add_handler(CommandHandler("me", me))
app.add_handler(CommandHandler("mee", mee))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, deal_form))

app.add_handler(CallbackQueryHandler(admin_buttons, pattern="^(activate|admincancel)_"))
app.add_handler(CallbackQueryHandler(seller_buttons, pattern="^(release|refund|cancel)_"))
app.add_handler(CallbackQueryHandler(buyer_buttons, pattern="^(buyerok|buyerrej)_"))

print("BOT RUNNING")
app.run_polling()
