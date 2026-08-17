import os
import re
import json
import requests
from flask import Flask, request
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import threading
import time

load_dotenv()

IG_USER_ID = os.getenv("IG_USER_ID")
IG_TOKEN = os.getenv("IG_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "auto123").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0")) if os.getenv("ADMIN_TELEGRAM_ID") else 0

CONFIG_FILE = "reels_config.json"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_config(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def extract_shortcode(url):
    m = re.search(r'/(reel|p|reels)/([A-Za-z0-9_-]+)/?', url)
    if m:
        return m.group(2)
    return None

def get_media_id_from_shortcode(shortcode):
    url = f"https://graph.facebook.com/v20.0/{IG_USER_ID}/media"
    params = {"fields": "id,shortcode", "access_token": IG_TOKEN, "limit": 100}
    try:
        r = requests.get(url, params=params, timeout=20)
        data = r.json()
        for media in data.get("data", []):
            if media.get("shortcode") == shortcode:
                return media.get("id")
        return None
    except Exception as e:
        print("Error fetching media:", e)
        return None

def reply_to_comment(comment_id, text):
    url = f"https://graph.facebook.com/v20.0/{comment_id}/replies"
    payload = {"message": text, "access_token": IG_TOKEN}
    r = requests.post(url, data=payload)
    print(f"Comment Reply {comment_id}:", r.text)
    return r.json()

def send_private_reply(comment_id, dm_text):
    private_url = f"https://graph.facebook.com/v20.0/{comment_id}/private_replies"
    payload = {"message": dm_text, "access_token": IG_TOKEN}
    r = requests.post(private_url, data=payload)
    print(f"Private Reply {comment_id}:", r.text)
    return r.json()

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is Running! Webhook is at /webhook"

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    print(f"Verify attempt: mode={mode} token={token} expected={VERIFY_TOKEN}")
    if mode == 'subscribe' and token and token.strip() == VERIFY_TOKEN:
        print("WEBHOOK VERIFIED SUCCESS")
        return challenge, 200
    print("Verification failed")
    return "Verification failed", 403

@app.route('/webhook', methods=['POST'])
def webhook_handler():
    data = request.get_json()
    print("Incoming webhook:", json.dumps(data, indent=2))
    if not data:
        return "ok", 200
    if "entry" in data:
        for entry in data["entry"]:
            if "changes" in entry:
                for change in entry["changes"]:
                    field = change.get("field")
                    value = change.get("value", {})
                    if field == "comments":
                        media_id = value.get("media", {}).get("id")
                        comment_id = value.get("id")
                        comment_text = value.get("text", "").lower()
                        from_user = value.get("from", {}).get("id")
                        # Avoid self comment
                        if str(from_user) == str(IG_USER_ID):
                            continue
                        print(f"New comment on {media_id}: {comment_text}")
                        config = load_config()
                        # Match by media_id OR shortcode lookup
                        reel_cfg = None
                        if media_id in config:
                            reel_cfg = config[media_id]
                        else:
                            # Try to get shortcode of this media_id and find in config
                            try:
                                media_info_url = f"https://graph.facebook.com/v20.0/{media_id}"
                                params = {"fields": "shortcode", "access_token": IG_TOKEN}
                                rr = requests.get(media_info_url, params=params, timeout=10)
                                sc = rr.json().get("shortcode")
                                if sc:
                                    for mid, cfg in config.items():
                                        if cfg.get("shortcode") == sc:
                                            reel_cfg = cfg
                                            break
                            except Exception as e:
                                print("Shortcode lookup failed", e)
                        
                        if not reel_cfg:
                            print(f"No config for media_id {media_id}")
                            continue

                        should_reply = False
                        if reel_cfg.get("type") == "all":
                            should_reply = True
                        elif reel_cfg.get("type") == "custom":
                            keywords = [k.lower() for k in reel_cfg.get("keywords", [])]
                            if any(kw in comment_text for kw in keywords):
                                should_reply = True
                        
                        if should_reply:
                            public_reply = reel_cfg.get("comment_text", "Check your DM! 🔥")
                            reply_to_comment(comment_id, public_reply)
                            dm_link = reel_cfg.get("dm_link", "")
                            btn_name = reel_cfg.get("button_name", "")
                            btn_url = reel_cfg.get("button_url", "")
                            dm_message = f"Hey! Here's your link 👇\n{dm_link}"
                            if btn_name and btn_url:
                                dm_message += f"\n\n{btn_name}: {btn_url}"
                            send_private_reply(comment_id, dm_message)
    return "ok", 200

# Telegram Bot Logic
user_state = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if ADMIN_ID != 0 and chat_id != ADMIN_ID:
        await update.message.reply_text("Unauthorized")
        return
    keyboard = [
        [InlineKeyboardButton("➕ New AutoDM Setup", callback_data="new_setup")],
        [InlineKeyboardButton("📋 My Active Reels", callback_data="list_reels")],
    ]
    await update.message.reply_text("Welcome to Auto DM Bot!\n\nReel ka link bhejo jisme AutoDM lagana hai.", reply_markup=InlineKeyboardMarkup(keyboard))
    user_state[chat_id] = {"step": "awaiting_reel_link"}

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    if data == "new_setup":
        user_state[chat_id] = {"step": "awaiting_reel_link"}
        await query.message.reply_text("🔗 Reel ka link bhejo:")

    elif data == "list_reels":
        config = load_config()
        if not config:
            await query.message.reply_text("Koi active reel nahi hai.")
        else:
            text = "📋 Active Reels:\n\n"
            for mid, cfg in config.items():
                text += f"• {cfg.get('shortcode')} - {cfg.get('type')} - {cfg.get('reel_url')}\n"
            await query.message.reply_text(text)

    elif data == "all_keywords":
        user_state[chat_id]["type"] = "all"
        user_state[chat_id]["step"] = "awaiting_dm_link"
        await query.message.reply_text("✅ All selected.\nAb DM LINK bhejo:")

    elif data == "custom_keywords":
        user_state[chat_id]["type"] = "custom"
        user_state[chat_id]["step"] = "awaiting_keywords"
        await query.message.reply_text("Keywords bhejo comma se alag karke (jaise: link, dm, price):")

    elif data == "cancel_dm":
        keyboard = [[InlineKeyboardButton("❌ Cancel DM", callback_data="confirm_cancel")]]
        await query.message.reply_text("Cancel kaunsa? Reel ka link bhejo:", reply_markup=InlineKeyboardMarkup(keyboard))
        user_state[chat_id] = {"step": "awaiting_cancel_link"}

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    if chat_id not in user_state:
        user_state[chat_id] = {"step": "awaiting_reel_link"}
        await update.message.reply_text("Reel ka link bhejo:")
        return

    state = user_state[chat_id]
    step = state.get("step")

    if step == "awaiting_reel_link":
        if "instagram.com" not in text:
            await update.message.reply_text("Sahi reel link bhejo. (instagram.com/reel/...)")
            return
        shortcode = extract_shortcode(text)
        if not shortcode:
            await update.message.reply_text("Link se shortcode nahi mila, sahi link bhejo.")
            return
        await update.message.reply_text(f"Reel lock! Shortcode: {shortcode}\nMedia ID nikal raha hu...")
        media_id = get_media_id_from_shortcode(shortcode)
        if not media_id:
            # Use shortcode as fallback ID for now, webhook will map via shortcode later
            media_id = shortcode
            print(f"Media ID not found for {shortcode}, using shortcode as ID fallback")
        user_state[chat_id].update({"media_id": media_id, "shortcode": shortcode, "reel_url": text, "step": "awaiting_keyword_type"})
        keyboard = [
            [InlineKeyboardButton("🌍 All", callback_data="all_keywords")],
            [InlineKeyboardButton("🎯 Custom", callback_data="custom_keywords")]
        ]
        await update.message.reply_text("Keywords type select karo:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif step == "awaiting_keywords":
        keywords = [k.strip() for k in text.split(",") if k.strip()]
        user_state[chat_id]["keywords"] = keywords
        user_state[chat_id]["step"] = "awaiting_dm_link"
        await update.message.reply_text(f"Keywords: {', '.join(keywords)}\n\nAb DM LINK bhejo:")

    elif step == "awaiting_dm_link":
        user_state[chat_id]["dm_link"] = text
        user_state[chat_id]["step"] = "awaiting_button_name"
        await update.message.reply_text("🔗 DM Link save.\nButton Name bhejo (skip likho agar nahi chahiye):")

    elif step == "awaiting_button_name":
        if text.lower() == "skip":
            user_state[chat_id]["button_name"] = ""
            user_state[chat_id]["button_url"] = ""
            user_state[chat_id]["step"] = "awaiting_comment_text"
            await update.message.reply_text("💬 Ab Comment ka text bhejo jo public reply me jayega:")
        else:
            user_state[chat_id]["button_name"] = text
            user_state[chat_id]["step"] = "awaiting_button_url"
            await update.message.reply_text(f"Button: {text}\nAb Button ka URL bhejo:")

    elif step == "awaiting_button_url":
        if text.lower() == "skip":
            user_state[chat_id]["button_url"] = user_state[chat_id].get("dm_link", "")
        else:
            user_state[chat_id]["button_url"] = text
        user_state[chat_id]["step"] = "awaiting_comment_text"
        await update.message.reply_text("💬 Ab Comment me jo reply jayega wo text bhejo (jaise: Check your DM! 🔥):")

    elif step == "awaiting_comment_text":
        comment_text = text
        media_id = user_state[chat_id]["media_id"]
        config = load_config()
        config[media_id] = {
            "reel_url": user_state[chat_id].get("reel_url"),
            "shortcode": user_state[chat_id].get("shortcode"),
            "type": user_state[chat_id].get("type", "all"),
            "keywords": user_state[chat_id].get("keywords", []),
            "dm_link": user_state[chat_id].get("dm_link"),
            "button_name": user_state[chat_id].get("button_name", ""),
            "button_url": user_state[chat_id].get("button_url", ""),
            "comment_text": comment_text
        }
        save_config(config)
        user_state.pop(chat_id, None)
        await update.message.reply_text(f"✅ AutoDM Active!\n\nReel: {media_id}\nShortcode: {config[media_id]['shortcode']}\nComment: {comment_text}\n\nAb test karo!")

    elif step == "awaiting_cancel_link":
        shortcode = extract_shortcode(text)
        media_id = get_media_id_from_shortcode(shortcode) or shortcode
        config = load_config()
        deleted = False
        if media_id in config:
            del config[media_id]
            deleted = True
        for mid in list(config.keys()):
            if config[mid].get("shortcode") == shortcode:
                del config[mid]
                deleted = True
        if deleted:
            save_config(config)
            await update.message.reply_text("✅ AutoDM band kar diya!")
        else:
            await update.message.reply_text("❌ Reel active list me nahi mili.")
        user_state.pop(chat_id, None)

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def run_telegram():
    if not TG_TOKEN:
        print("TELEGRAM_BOT_TOKEN missing!")
        return
    application = Application.builder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    print("Telegram Bot Polling Started...")
    application.run_polling()

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    time.sleep(2)
    run_telegram()
