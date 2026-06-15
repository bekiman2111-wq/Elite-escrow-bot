import os
import sqlite3
import time
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

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

# ================= ADMINS =================
ADMIN_IDS = [
    6138132255,
    5635739078
]

# ================= DATABASE =================
conn = sqlite3.connect(
    "escrow.db",
    check_same_thread=False
)

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

# ================= PROOF CHANNEL =================
async def send_to_proof(context, text):

    if not PROOF_CHANNEL:
        return

    try:

        chat_id = PROOF_CHANNEL

        if (
            isinstance(chat_id, str)
            and not chat_id.startswith("@")
            and not chat_id.startswith("-100")
        ):
            chat_id = "@" + chat_id

        await context.bot.send_message(
            chat_id=chat_id,
            text=text
        )

    except Exception as e:
        logger.error(f"Proof channel error: {e}")

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Escrow Bot Running ✅"
    )

# ================= DEAL FORM =================
async def deal_form(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    # ================= TEMPLATE =================
    if text.lower() == "form":

        await update.message.reply_text(
            """@admins

Seller:
Buyer:
Amount:
Currency:
Method:"""
        )

        return

    # ================= REAL FORM =================
    if (
        "seller:" not in text.lower()
        or "buyer:" not in text.lower()
    ):
        return

    try:

        data = {}

        for line in text.split("\n"):

            if ":" in line:

                key, value = line.split(":", 1)

                data[
                    key.strip().lower()
                ] = value.strip()

        seller = clean_username(
            data.get("seller")
        )

        buyer = clean_username(
            data.get("buyer")
        )

        amount = data.get("amount", "")
        currency = data.get("currency", "")
        method = data.get("method", "")

        if (
            not seller
            or not buyer
            or not amount
            or not currency
            or not method
        ):
            return await update.message.reply_text(
                "❌ Invalid form"
            )

        # ================= SAVE DEAL =================
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
        """, (
            seller,
            buyer,
            amount,
            currency.upper(),
            method,
            "PENDING",
            time.time()
        ))

        conn.commit()

        did = cursor.lastrowid

        # ================= ADMIN BUTTONS =================
        keyboard = [[
            InlineKeyboardButton(
                "✅ Activate Deal",
                callback_data=f"activate_{did}"
            ),

            InlineKeyboardButton(
                "❌ Cancel Deal",
                callback_data=f"admincancel_{did}"
            )
        ]]

        msg = await update.message.reply_text(
            f"🚨 NEW DEAL {deal_id(did)}\n\n"
            f"👤 Seller: @{seller}\n"
            f"👤 Buyer: @{buyer}\n"
            f"💰 Amount: {amount} {currency.upper()}\n"
            f"💳 Method: {method}\n\n"
            f"⏳ Waiting admin action...",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        cursor.execute("""
        UPDATE deals
        SET deal_message_id=?
        WHERE id=?
        """, (
            msg.message_id,
            did
        ))

        conn.commit()

    except Exception as e:
        logger.error(e)

# ================= ADMIN BUTTONS =================
async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    if q.from_user.id not in ADMIN_IDS:
        return await q.answer(
            "❌ Admin only",
            show_alert=True
        )

    action, did = q.data.split("_")
    did = int(did)

    # ================= ACTIVATE =================
    if action == "activate":

        cursor.execute("""
        SELECT seller_username,
               buyer_username,
               amount,
               currency,
               method
        FROM deals
        WHERE id=?
        """, (did,))

        row = cursor.fetchone()

        if not row:
            return

        seller, buyer, amount, currency, method = row

        keyboard = [[
            InlineKeyboardButton(
                "💸 Release",
                callback_data=f"release_{did}"
            ),

            InlineKeyboardButton(
                "♻ Refund",
                callback_data=f"refund_{did}"
            ),

            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=f"cancel_{did}"
            )
        ]]

        msg = await q.message.reply_text(
            f"✅ DEAL ACTIVATED {deal_id(did)}\n\n"
            f"👤 Seller: @{seller}\n"
            f"👤 Buyer: @{buyer}\n"
            f"💰 {amount} {currency}\n"
            f"💳 {method}\n\n"
            f"👉 Seller can now choose action",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        cursor.execute("""
        UPDATE deals
        SET status=?,
            activator_admin_id=?,
            control_message_id=?
        WHERE id=?
        """, (
            "ACTIVE",
            q.from_user.id,
            msg.message_id,
            did
        ))

        conn.commit()

        await q.edit_message_text(
            f"✅ Deal {deal_id(did)} activated"
        )

    # ================= ADMIN CANCEL =================
    elif action == "admincancel":

        cursor.execute("""
        UPDATE deals
        SET status=?,
            handled_by=?
        WHERE id=?
        """, (
            "CANCELLED",
            safe_user(q.from_user),
            did
        ))

        conn.commit()

        await q.edit_message_text(
            f"❌ Deal {deal_id(did)} cancelled"
        )

        cursor.execute("""
        SELECT seller_username,
               buyer_username,
               amount,
               currency,
               method
        FROM deals
        WHERE id=?
        """, (did,))

        row = cursor.fetchone()

        if row:

            seller, buyer, amount, currency, method = row

            await send_to_proof(
                context,
                f"❌ CANCELLED DEAL\n\n"
                f"🆔 {deal_id(did)}\n"
                f"👤 @{seller} ↔ @{buyer}\n"
                f"💰 {amount} {currency}\n"
                f"💳 {method}"
            )

# ================= SELLER BUTTONS =================
async def seller_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    action, did = q.data.split("_")
    did = int(did)

    cursor.execute("""
    SELECT seller_username,
           buyer_username
    FROM deals
    WHERE id=?
    """, (did,))

    row = cursor.fetchone()

    if not row:
        return

    seller, buyer = row

    user = clean_username(
        safe_user(q.from_user)
    )

    if user != seller:
        return await q.answer(
            "❌ Only seller",
            show_alert=True
        )

    keyboard = [[
        InlineKeyboardButton(
            "✅ Accept",
            callback_data=f"buyerok_{did}"
        ),

        InlineKeyboardButton(
            "❌ Reject",
            callback_data=f"buyerrej_{did}"
        )
    ]]

    await q.message.reply_text(
        f"⚠ SELLER REQUEST\n\n"
        f"🆔 {deal_id(did)}\n"
        f"📌 Action: {action.upper()}\n\n"
        f"👉 @{buyer} please accept or reject",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    cursor.execute("""
    UPDATE deals
    SET status=?
    WHERE id=?
    """, (
        action.upper(),
        did
    ))

    conn.commit()

# ================= BUYER BUTTONS =================
async def buyer_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    action, did = q.data.split("_")
    did = int(did)

    cursor.execute("""
    SELECT buyer_username,
           seller_username,
           amount,
           currency,
           method,
           status
    FROM deals
    WHERE id=?
    """, (did,))

    row = cursor.fetchone()

    if not row:
        return

    buyer, seller, amount, currency, method, status = row

    user = clean_username(
        safe_user(q.from_user)
    )

    if user != buyer:
        return await q.answer(
            "❌ Only buyer",
            show_alert=True
        )

    # ================= REJECT =================
    if action == "buyerrej":

        await q.edit_message_text(
            "❌ Buyer rejected request"
        )

        return

    # ================= FINAL STATUS =================
    if status == "RELEASE":
        final = "SUCCESSFUL"

    elif status == "REFUND":
        final = "REFUNDED"

    else:
        final = "CANCELLED"

    cursor.execute("""
    UPDATE deals
    SET status=?,
        handled_by=?
    WHERE id=?
    """, (
        final,
        seller,
        did
    ))

    conn.commit()

    await q.edit_message_text(
        f"✅ Deal {deal_id(did)} completed\n"
        f"📌 Status: {final}"
    )

    # ================= PROOF CHANNEL =================
    await send_to_proof(
        context,
        f"📢 {final} DEAL\n\n"
        f"🆔 {deal_id(did)}\n"
        f"👤 @{seller} ↔ @{buyer}\n"
        f"💰 {amount} {currency}\n"
        f"💳 {method}"
    )

# ================= EDIT DEAL =================
async def edit_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text(
            "❌ Admin only"
        )

    try:

        args = context.args

        if len(args) < 3:
            return await update.message.reply_text(
                "Usage:\n"
                "/edit dealid field value"
            )

        did = int(args[0])
        field = args[1].lower()
        value = " ".join(args[2:])

        allowed = [
            "amount",
            "currency",
            "method"
        ]

        if field not in allowed:
            return await update.message.reply_text(
                "❌ Invalid field"
            )

        cursor.execute(
            f"UPDATE deals SET {field}=? WHERE id=?",
            (
                value.upper()
                if field == "currency"
                else value,
                did
            )
        )

        conn.commit()

        await update.message.reply_text(
            f"✅ Deal {deal_id(did)} updated\n\n"
            f"{field.upper()} → {value}"
        )

    except Exception as e:

        logger.error(e)

        await update.message.reply_text(
            "❌ Edit failed"
        )

# ================= USER STATS =================
async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):

    username = clean_username(
        safe_user(update.effective_user)
    )

    # ETB
    cursor.execute("""
    SELECT COUNT(*)
    FROM deals
    WHERE (
        seller_username=?
        OR buyer_username=?
    )
    AND currency='ETB'
    AND status='SUCCESSFUL'
    """, (
        username,
        username
    ))

    etb = cursor.fetchone()[0]

    # USDT
    cursor.execute("""
    SELECT COUNT(*)
    FROM deals
    WHERE (
        seller_username=?
        OR buyer_username=?
    )
    AND currency='USDT'
    AND status='SUCCESSFUL'
    """, (
        username,
        username
    ))

    usdt = cursor.fetchone()[0]

    total = etb + usdt

    await update.message.reply_text(
        f"👤 YOUR STATS\n\n"
        f"📦 Total Deals: {total}\n"
        f"🇪🇹 ETB Deals: {etb}\n"
        f"💵 USDT Deals: {usdt}"
    )

# ================= ADMIN STATS =================
async def mee(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text(
            "❌ Admin only"
        )

    admin = clean_username(
        safe_user(update.effective_user)
    )

    # ETB
    cursor.execute("""
    SELECT COUNT(*)
    FROM deals
    WHERE handled_by=?
    AND currency='ETB'
    AND status='SUCCESSFUL'
    """, (admin,))

    etb = cursor.fetchone()[0]

    # USDT
    cursor.execute("""
    SELECT COUNT(*)
    FROM deals
    WHERE handled_by=?
    AND currency='USDT'
    AND status='SUCCESSFUL'
    """, (admin,))

    usdt = cursor.fetchone()[0]

    total = etb + usdt

    await update.message.reply_text(
        f"🛡 ADMIN STATS\n\n"
        f"📦 Total Handled: {total}\n"
        f"🇪🇹 ETB Deals: {etb}\n"
        f"💵 USDT Deals: {usdt}"
    )

# ================= MAIN =================
app = ApplicationBuilder().token(
    TOKEN
).build()

app.add_handler(
    CommandHandler("start", start)
)

app.add_handler(
    CommandHandler("edit", edit_deal)
)

app.add_handler(
    CommandHandler("me", me)
)

app.add_handler(
    CommandHandler("mee", mee)
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        deal_form
    )
)

# ADMIN
app.add_handler(
    CallbackQueryHandler(
        admin_buttons,
        pattern="^(activate|admincancel)_"
    )
)

# SELLER
app.add_handler(
    CallbackQueryHandler(
        seller_buttons,
        pattern="^(release|refund|cancel)_"
    )
)

# BUYER
app.add_handler(
    CallbackQueryHandler(
        buyer_buttons,
        pattern="^(buyerok|buyerrej)_"
    )
)

print("BOT RUNNING ✅")

app.run_polling()
