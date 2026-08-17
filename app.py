import os, re, json, requests, threading, asyncio
from flask import Flask, request

app = Flask(__name__)

def get_env(k):
    return (os.environ.get(k,"") or "").strip().strip('"').strip("'")

# Tere 6 Variables
BOT_TOKEN = get_env("BOT_TOKEN")
PAGE_ACCESS_TOKEN = get_env("PAGE_ACCESS_TOKEN") # Naye wala IGQ... token yahi hai
IG_BUSINESS_ID = get_env("IG_BUSINESS_ID")
IG_USER_ID = get_env("IG_USER_ID")
VERIFY_TOKEN = get_env("VERIFY_TOKEN") or "auto123"
ADMIN_TELEGRAM_ID = get_env("ADMIN_TELEGRAM_ID")

# IG ID - Dono me se jo milega use karega
EFFECTIVE_IG_ID = IG_USER_ID or IG_BUSINESS_ID

DATA_FILE = "data.json"
user_states = {}

def load_data():
    if not os.path.exists(DATA_FILE): return {}
    try:
        with open(DATA_FILE,'r') as f: return json.load(f)
    except: return {}

def save_data(d):
    with open(DATA_FILE,'w') as f: json.dump(d,f,indent=2)

# --- INSTAGRAM API CALLS (Naye + Purane API dono ke liye) ---
def send_comment_reply(comment_id):
    # Naye API ke liye graph.instagram.com, purane ke liye graph.facebook.com
    urls = [
        f"https://graph.instagram.com/v22.0/{comment_id}/replies",
        f"https://graph.facebook.com/v19.0/{comment_id}/replies"
    ]
    for url in urls:
        try:
            payload = {"message": "DM check karo, link bhej diya 🚀", "access_token": PAGE_ACCESS_TOKEN}
            r = requests.post(url, json=payload, timeout=15)
            print(f"REPLY [{url}]: {r.status_code} {r.text}")
            if r.ok:
                return True
        except Exception as e:
            print(f"Reply Error {url}: {e}")
    return False

def send_dm(commenter_id, dm_link, btn_name):
    urls = [
        f"https://graph.instagram.com/v22.0/{EFFECTIVE_IG_ID}/messages",
        f"https://graph.facebook.com/v19.0/{EFFECTIVE_IG_ID}/messages"
    ]
    if btn_name and btn_name.lower()!= 'skip':
        msg_payload = {"attachment":{"type":"template","payload":{"template_type":"button","text":f"Ye raha tera link 👇\n{dm_link}","buttons":[{"type":"web_url","url":dm_link,"title":btn_name[:20]}]}}}
    else:
        msg_payload = {"text": f"Ye raha tera link 👇\n{dm_link}"}

    for url in urls:
        try:
            payload = {"recipient":{"id":commenter_id},"message":msg_payload,"access_token":PAGE_ACCESS_TOKEN}
            r = requests.post(url, json=payload, timeout=15)
            print(f"DM [{url}]: {r.status_code} {r.text}")
            if r.ok:
                return True
        except Exception as e:
            print(f"DM Error {url}: {e}")
    return False

@app.route('/')
def home(): return "Bot is Live - All Features OK"

@app.route('/webhook', methods=['GET','POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == VERIFY_TOKEN:
            return request.args.get('hub.challenge')
        return "Verify Fail", 403

    body = request.json
    print(f"WEBHOOK AAYA: {body}")
    data = load_data()

    for entry in body.get('entry',[]):
        for change in entry.get('changes',[]):
            if change.get('field')!= 'comments': continue
            val = change.get('value',{})
            comment_id = val.get('id')
            text = (val.get('text','') or '').lower()
            commenter_id = val.get('from',{}).get('id')
            media_id = val.get('media',{}).get('id')

            if not comment_id or not commenter_id: continue
            # Khud ke comment pe reply nahi karega
            if str(commenter_id) == str(EFFECTIVE_IG_ID): continue

            for cfg in data.values():
                # Media Match - Agar media_id hai to check karo
                if cfg.get('media_id') and media_id and str(cfg.get('media_id'))!= str(media_id):
                    continue

                # Yahan Follow Check ka logic aayega (Instagram API follow check allow nahi karta, isliye abhi sabko bhejega)
                print(f"MATCHED Reel {cfg.get('shortcode')} -> Comment: {text}")
                send_comment_reply(comment_id)
                send_dm(commenter_id, cfg.get('dm_link'), cfg.get('button_name'))

    return "OK", 200

# --- TELEGRAM BOT ---
def run_telegram():
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        print("BOT_TOKEN galat hai")
        return
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
        from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

        def is_admin(uid):
            if not ADMIN_TELEGRAM_ID: return True
            return str(uid) == str(ADMIN_TELEGRAM_ID)

        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not is_admin(update.effective_user.id):
                await update.message.reply_text("Ye bot sirf admin ke liye hai.")
                return
            user_states[update.effective_user.id] = {"step":"reel_link"}
            await update.message.reply_text("Reel ka link bhejo (Instagram Reel ka URL):")

        async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
            uid = update.effective_user.id
            if not is_admin(uid): return
            txt = update.message.text.strip()
            step = user_states.get(uid,{}).get("step")
            data = load_data()
            cfg = data.get(str(uid), {})

            if step == "reel_link":
                m = re.search(r'/reel/([^/]+)/|/p/([^/]+)/', txt)
                sc = (m.group(1) or m.group(2)) if m else None
                cfg['shortcode'] = sc
                cfg['reel_url'] = txt
                # Media ID auto-nikalo
                try:
                    # Naye API ke liye
                    url = f"https://graph.instagram.com/v22.0/{EFFECTIVE_IG_ID}/media?fields=id,shortcode&limit=100&access_token={PAGE_ACCESS_TOKEN}"
                    res = requests.get(url, timeout=10).json()
                    for it in res.get('data',[]):
                        if it.get('shortcode') == sc:
                            cfg['media_id'] = it.get('id')
                            break
                    if not cfg.get('media_id'):
                        # Purana API try
                        url2 = f"https://graph.facebook.com/v19.0/{EFFECTIVE_IG_ID}/media?fields=id,shortcode&limit=100&access_token={PAGE_ACCESS_TOKEN}"
                        res2 = requests.get(url2, timeout=10).json()
                        for it in res2.get('data',[]):
                            if it.get('shortcode') == sc:
                                cfg['media_id'] = it.get('id')
                                break
                except Exception as e:
                    print(f"Media ID Error: {e}")
                save_data({**data,str(uid):cfg})
                kb = [[InlineKeyboardButton("🌍 All Comments pe DM", callback_data="kw_all")]]
                await update.message.reply_text(f"✅ Reel: {sc}\nMedia ID: {cfg.get('media_id','Auto detect hoga')}\n\nAb keyword select karo:", reply_markup=InlineKeyboardMarkup(kb))

            elif step == "dm_link":
                cfg['dm_link'] = txt
                save_data({**data,str(uid):cfg})
                await update.message.reply_text("Button ka naam bhejo (ex: JOIN NOW) - Skip karna hai to 'skip' likho:")
                user_states[uid] = {"step":"btn_name"}

            elif step == "btn_name":
                cfg['button_name'] = None if txt.lower() == 'skip' else txt
                save_data({**data,str(uid):cfg})
                kb = [[InlineKeyboardButton("✅ Follow Only ON (Beta)", callback_data="follow_on")],[InlineKeyboardButton("❌ Sabko DM bhejo", callback_data="follow_off")]]
                await update.message.reply_text("Follow check chahiye kya?", reply_markup=InlineKeyboardMarkup(kb))

        async def handle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
            q = update.callback_query
            await q.answer()
            uid = q.from_user.id
            if not is_admin(uid): return
            data = load_data()
            cfg = data.get(str(uid),{})
            if q.data == "kw_all":
                cfg['keyword_type'] = 'all'
                save_data({**data,str(uid):cfg})
                await q.message.reply_text("Ab DM LINK bhejo (Yahi button ka URL banega):")
                user_states[uid] = {"step":"dm_link"}
            else:
                cfg['follow_only'] = (q.data == 'follow_on')
                save_data({**data,str(uid):cfg})
                await q.message.reply_text(f"✅ BOT ACTIVE HO GAYA!\n\nReel: {cfg.get('shortcode')}\nMedia ID: {cfg.get('media_id')}\nLink: {cfg.get('dm_link')}\nButton: {cfg.get('button_name')}\nFollow Only: {cfg.get('follow_only')}\n\nAb {EFFECTIVE_IG_ID} wali ID ki reel pe dusre account se comment karke test karo.")

        async def main():
            app_tg = Application.builder().token(BOT_TOKEN).build()
            app_tg.add_handler(CommandHandler("start", start))
            app_tg.add_handler(CallbackQueryHandler(handle_cb))
            app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
            await app_tg.initialize()
            await app_tg.start()
            await app_tg.updater.start_polling()
            print("Telegram Bot Live")
            await asyncio.Event().wait()

        asyncio.run(main())
    except Exception as e:
        print(f"Telegram Crash: {e}")

threading.Thread(target=run_telegram, daemon=True).start()

if __name__ == '__main__':
    if not EFFECTIVE_IG_ID:
        print("ERROR: IG_BUSINESS_ID / IG_USER_ID me se ek to daalo")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
