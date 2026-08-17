import os, re, json, requests
from dotenv import load_dotenv
from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import threading

# Env file se token lega, code se nahi
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
IG_BUSINESS_ID = os.getenv("IG_BUSINESS_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "auto123")

app = Flask(__name__)
DATA_FILE = "data.json"
user_states = {}

def load_data():
    if not os.path.exists(DATA_FILE): return {}
    with open(DATA_FILE, 'r') as f: return json.load(f)
def save_data(data):
    with open(DATA_FILE, 'w') as f: json.dump(data, f, indent=2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Reel ka link bhejo:")
    user_states[update.effective_user.id] = {"step": "reel_link"}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = user_states.get(user_id, {})
    data = load_data()
    chat_data = data.get(str(user_id), {})

    if state.get("step") == "reel_link":
        match = re.search(r'/reel/([^/]+)/', text)
        shortcode = match.group(1) if match else None
        chat_data["shortcode"] = shortcode
        save_data({**data, str(user_id): chat_data})
        keyboard = [[InlineKeyboardButton("🌍 All Comments", callback_data="kw_all")]]
        await update.message.reply_text("Keyword type select karo:", reply_markup=InlineKeyboardMarkup(keyboard))
        user_states[user_id]["step"] = "kw_type"

    elif state.get("step") == "dm_link":
        chat_data["dm_link"] = text # Ek hi link, DM aur Button dono ke liye
        save_data({**data, str(user_id): chat_data})
        await update.message.reply_text("Button Name bhejo - skip ke liye `skip`:")
        user_states[user_id]["step"] = "btn_name"

    elif state.get("step") == "btn_name":
        chat_data["button_name"] = None if text.lower() == 'skip' else text
        save_data({**data, str(user_id): chat_data})
        keyboard = [[InlineKeyboardButton("✅ Followers Only", callback_data="follow_on")],
                    [InlineKeyboardButton("❌ Sabko DM", callback_data="follow_off")]]
        await update.message.reply_text("DM kisko bheju?", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = load_data()
    chat_data = data.get(str(user_id), {})
    if query.data == "kw_all":
        chat_data["keyword_type"] = "all"
        save_data({**data, str(user_id): chat_data})
        await query.message.reply_text("✅ All selected.\nAb DM LINK bhejo (Yahi Button URL bhi hai):")
        user_states[user_id] = {"step": "dm_link"}
    elif query.data.startswith("follow_"):
        chat_data["follow_only"] = True if query.data == "follow_on" else False
        save_data({**data, str(user_id): chat_data})
        await query.message.reply_text(f"✅ Bot Active!\nFollow Only: {chat_data['follow_only']}")

@app.route('/webhook', methods=['GET','POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == VERIFY_TOKEN: return request.args.get('hub.challenge')
        return "Fail", 403
    # ... (DM/Comment logic same as before)
    return "OK", 200

def run_flask(): app.run(host='0.0.0.0', port=5000)

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()
