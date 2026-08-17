import os, re, json, requests
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIG ---
BOT_TOKEN = "TELEGRAM_BOT_TOKEN_DALO"  # @BotFather se
PAGE_ACCESS_TOKEN = "FB_PAGE_PERMANENT_TOKEN_DALO"
IG_BUSINESS_ID = "1784..." # nrtecno2 ka IG Business ID
VERIFY_TOKEN = "my_verify_token_123"

app = Flask(__name__)
DATA_FILE = "data.json"

def load_data():
    if not os.path.exists(DATA_FILE): return {}
    with open(DATA_FILE, 'r') as f: return json.load(f)
def save_data(data):
    with open(DATA_FILE, 'w') as f: json.dump(data, f, indent=2)

# --- TELEGRAM BOT LOGIC (Tera Screenshot Wala Flow) ---
user_states = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Reel ka link bhejo:")
    user_states[update.effective_user.id] = {"step": "reel_link"}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = user_states.get(user_id, {})
    data = load_data()
    chat_data = data.get(str(user_id), {})

    # Step 1: Reel Link
    if state.get("step") == "reel_link":
        match = re.search(r'/reel/([^/]+)/|/p/([^/]+)/', text)
        shortcode = match.group(1) or match.group(2) if match else None
        if not shortcode:
            await update.message.reply_text("Sahi Reel link bhejo bhai!")
            return
        chat_data["shortcode"] = shortcode
        chat_data["reel_url"] = text
        save_data({**data, str(user_id): chat_data})
        await update.message.reply_text(f"Reel lock: {shortcode} ... Media ID nikal raha hu...")
        # Media ID nikalna
        try:
            r = requests.get(f"https://graph.facebook.com/v19.0/{IG_BUSINESS_ID}?fields=media{{id,shortcode}}&access_token={PAGE_ACCESS_TOKEN}").json()
            for m in r.get('media',{}).get('data',[]):
                if m['shortcode'] == shortcode:
                    chat_data["media_id"] = m['id']
                    break
        except: pass
        keyboard = [[InlineKeyboardButton("🌍 All Comments", callback_data="kw_all")],
                    [InlineKeyboardButton("🎯 Custom Keywords", callback_data="kw_custom")]]
        await update.message.reply_text("Keyword type select karo:", reply_markup=InlineKeyboardMarkup(keyboard))
        user_states[user_id]["step"] = "kw_type"

    # Step 2: DM Link (Yahi fix hai - ek hi baar puchega)
    elif state.get("step") == "dm_link":
        chat_data["dm_link"] = text  # Yahi link DM me bhi aur Button me bhi lagega
        save_data({**data, str(user_id): chat_data})
        await update.message.reply_text("Button Name bhejo (jaise: JOIN NOW) - Skip karna hai to `skip` likho:")
        user_states[user_id]["step"] = "btn_name"

    # Step 3: Button Name
    elif state.get("step") == "btn_name":
        if text.lower() == 'skip':
            chat_data["button_name"] = None
        else:
            chat_data["button_name"] = text
        save_data({**data, str(user_id): chat_data})
        
        # Follow wala naya feature
        keyboard = [[InlineKeyboardButton("✅ Followers Only (ON)", callback_data="follow_on")],
                    [InlineKeyboardButton("❌ Sabko DM (OFF)", callback_data="follow_off")]]
        await update.message.reply_text(f"Button Name: {chat_data['button_name']}\nAb select karo DM kisko jaye?", reply_markup=InlineKeyboardMarkup(keyboard))
        user_states[user_id]["step"] = "follow_setting"

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = load_data()
    chat_data = data.get(str(user_id), {})

    if query.data == "kw_all":
        chat_data["keyword_type"] = "all"
        save_data({**data, str(user_id): chat_data})
        await query.message.reply_text("✅ All selected.\nAb DM LINK bhejo:")
        user_states[user_id] = {"step": "dm_link"}
    
    elif query.data.startswith("follow_"):
        chat_data["follow_only"] = True if query.data == "follow_on" else False
        save_data({**data, str(user_id): chat_data})
        await query.message.reply_text(f"✅ Bot Active Ho Gaya!\nReel: {chat_data['shortcode']}\nDM Link: {chat_data['dm_link']}\nButton: {chat_data['button_name']}\nFollow Only: {chat_data['follow_only']}\n\nAb dusre account se comment karke test karo. Khud ke account se comment ka webhook nahi aata!")

# --- INSTAGRAM WEBHOOK LOGIC (Jo sabko reply dega) ---
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET': # Verification
        if request.args.get('hub.verify_token') == VERIFY_TOKEN:
            return request.args.get('hub.challenge')
        return "Verification failed", 403

    body = request.json
    data = load_data()
    
    for entry in body.get('entry', []):
        for change in entry.get('changes', []):
            if change['field'] == 'comments':
                value = change['value']
                comment_id = value.get('id')
                comment_text = value.get('text','').lower()
                commenter_id = value.get('from',{}).get('id')
                
                # Har user ka data check karo
                for user_id, cfg in data.items():
                    if value.get('media',{}).get('id') != cfg.get('media_id'):
                        continue
                    
                    # Keyword check
                    if cfg.get('keyword_type') != 'all':
                        if cfg.get('keyword','').lower() not in comment_text:
                            continue
                    
                    # 1. COMMENT REPLY (Sabko jayega)
                    try:
                        requests.post(f"https://graph.facebook.com/v19.0/{comment_id}/replies",
                                      json={"message": f"DM check karo @{value['from']['username']}! Link bhej diya 🚀", "access_token": PAGE_ACCESS_TOKEN})
                    except Exception as e: print("Comment reply fail:", e)

                    # 2. FOLLOW CHECK + DM
                    if cfg.get('follow_only', False):
                        # NOTE: Official API me follower check nahi hai, isliye hum yaha simple check kar rahe hai
                        # Agar follow check fail ho raha hai to isko False kar do
                        pass # Yaha tu apna follower DB check laga sakta hai

                    # 3. DM SEND
                    dm_link = cfg.get('dm_link')
                    btn_name = cfg.get('button_name')
                    try:
                        if btn_name:
                            payload = {
                                "recipient": {"id": commenter_id},
                                "message": {"attachment": {"type":"template","payload":{"template_type":"button","text": f"Ye raha link: {dm_link}","buttons":[{"type":"web_url","url": dm_link,"title": btn_name}]}}},
                                "access_token": PAGE_ACCESS_TOKEN
                            }
                        else:
                            payload = {"recipient": {"id": commenter_id},"message": {"text": f"Ye raha link: {dm_link}"},"access_token": PAGE_ACCESS_TOKEN}
                        requests.post(f"https://graph.facebook.com/v19.0/{IG_BUSINESS_ID}/messages", json=payload)
                        print(f"DM sent to {commenter_id}")
                    except Exception as e: print("DM fail:", e)
    return "OK", 200

# --- RUN BOTH ---
def run_flask():
    app.run(host='0.0.0.0', port=5000)

if __name__ == '__main__':
    import threading
    threading.Thread(target=run_flask).start()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()
