import os, re, json, requests, threading, asyncio
from flask import Flask, request

app = Flask(__name__)

# --- FIX: Env ko safe tarike se read karo ---
def get_env(key):
    val = os.environ.get(key, "")
    # Render kabhi-kabhi " " ke sath token deta hai, usko saaf karo
    return val.strip().strip('"').strip("'").strip()

BOT_TOKEN = get_env("BOT_TOKEN")
PAGE_ACCESS_TOKEN = get_env("PAGE_ACCESS_TOKEN")
IG_BUSINESS_ID = get_env("IG_BUSINESS_ID")
VERIFY_TOKEN = get_env("VERIFY_TOKEN") or "auto123"

print(f"--- ENV CHECK ---")
print(f"BOT_TOKEN found: {bool(BOT_TOKEN)} len: {len(BOT_TOKEN) if BOT_TOKEN else 0}")
print(f"PAGE_TOKEN found: {bool(PAGE_ACCESS_TOKEN)}")
print(f"VERIFY_TOKEN: {VERIFY_TOKEN}")

@app.route('/')
def home():
    return "Bot Live Hai"

@app.route('/webhook', methods=['GET','POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == VERIFY_TOKEN:
            return request.args.get('hub.challenge')
        return "Fail", 403
    print("Webhook hit:", request.json)
    return "OK", 200

# Telegram Bot - Alag thread me, crash nahi karega
def run_telegram():
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        print(f"CRITICAL: BOT_TOKEN galat hai. Render me check kar. Value mili: '{BOT_TOKEN[:10]}...'")
        return
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
        from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

        user_states = {}
        def load_data():
            if not os.path.exists("data.json"): return {}
            with open("data.json", 'r') as f: return json.load(f)
        def save_data(d):
            with open("data.json", 'w') as f: json.dump(d, f, indent=2)

        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_states[update.effective_user.id] = {"step": "reel_link"}
            await update.message.reply_text("Reel ka link bhejo:")

        async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
            uid = update.effective_user.id
            txt = update.message.text.strip()
            step = user_states.get(uid, {}).get("step")
            data = load_data()
            cfg = data.get(str(uid), {})
            if step == "reel_link":
                m = re.search(r'/reel/([^/]+)/', txt)
                cfg["shortcode"] = m.group(1) if m else "test"
                save_data({**data, str(uid): cfg})
                kb = [[InlineKeyboardButton("🌍 All Comments", callback_data="kw_all")]]
                await update.message.reply_text("Keyword:", reply_markup=InlineKeyboardMarkup(kb))
            elif step == "dm_link":
                cfg["dm_link"] = txt # FIX: Ek hi link DM + Button ke liye
                save_data({**data, str(uid): cfg})
                await update.message.reply_text("Button Name bhejo (skip = skip):")
                user_states[uid] = {"step": "btn_name"}
            elif step == "btn_name":
                cfg["button_name"] = None if txt.lower()=="skip" else txt
                save_data({**data, str(uid): cfg})
                kb = [[InlineKeyboardButton("✅ Follow Only ON", callback_data="follow_on")],[InlineKeyboardButton("❌ Sabko DM", callback_data="follow_off")]]
                await update.message.reply_text("Follow check?", reply_markup=InlineKeyboardMarkup(kb))

        async def handle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
            q = update.callback_query
            await q.answer()
            uid = q.from_user.id
            data = load_data()
            cfg = data.get(str(uid), {})
            if q.data == "kw_all":
                await q.message.reply_text("✅ All Selected\nAb DM LINK bhejo (Yahi Button URL hai):")
                user_states[uid] = {"step": "dm_link"}
            else:
                cfg["follow_only"] = q.data=="follow_on"
                save_data({**data, str(uid): cfg})
                await q.message.reply_text(f"✅ Bot Active! Follow Only: {cfg['follow_only']}")

        async def main():
            app_tg = Application.builder().token(BOT_TOKEN).build()
            app_tg.add_handler(CommandHandler("start", start))
            app_tg.add_handler(CallbackQueryHandler(handle_cb))
            app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
            await app_tg.initialize()
            await app_tg.start()
            print("Telegram Polling Started")
            await app_tg.updater.start_polling()
            await asyncio.Event().wait()

        asyncio.run(main())
    except Exception as e:
        print(f"Telegram Thread Error: {e}")

threading.Thread(target=run_telegram, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
