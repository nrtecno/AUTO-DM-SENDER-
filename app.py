import os, re, json, requests, asyncio, time
from datetime import datetime, date
from flask import Flask, request
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import threading

load_dotenv()

IG_USER_ID = os.getenv("IG_USER_ID")
IG_TOKEN = os.getenv("IG_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "auto123").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0")) if os.getenv("ADMIN_TELEGRAM_ID") else 0

CONFIG_FILE = "reels_config.json"
STATS_FILE = "dm_stats.json"

def load_config():
    if not os.path.exists(CONFIG_FILE): return {}
    try: 
        with open(CONFIG_FILE,'r') as f: return json.load(f)
    except: return {}

def save_config(d): 
    with open(CONFIG_FILE,'w') as f: json.dump(d,f,indent=2)

def load_stats():
    if not os.path.exists(STATS_FILE): return {"total":0, "logs":[]}
    try:
        with open(STATS_FILE,'r') as f: return json.load(f)
    except: return {"total":0, "logs":[]}

def save_stats(d):
    with open(STATS_FILE,'w') as f: json.dump(d,f,indent=2)

def add_dm_log(media_id, shortcode, comment_id, comment_text, ig_username="unknown"):
    stats = load_stats()
    stats["total"] = stats.get("total",0)+1
    log_entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": str(date.today()),
        "media_id": media_id,
        "shortcode": shortcode,
        "comment_id": comment_id,
        "comment_text": comment_text,
        "username": ig_username
    }
    stats["logs"].append(log_entry)
    # Keep last 1000 only
    if len(stats["logs"]) > 1000:
        stats["logs"] = stats["logs"][-1000:]
    save_stats(stats)
    return stats["total"]

def extract_shortcode(url):
    m = re.search(r'/(reel|p|reels)/([A-Za-z0-9_-]+)/?', url)
    return m.group(2) if m else None

def get_media_id_from_shortcode(shortcode):
    url = f"https://graph.facebook.com/v20.0/{IG_USER_ID}/media"
    params = {"fields":"id,shortcode","access_token":IG_TOKEN,"limit":100}
    try:
        r = requests.get(url, params=params, timeout=20).json()
        for media in r.get("data",[]):
            if media.get("shortcode")==shortcode: return media.get("id")
    except Exception as e:
        print("Media fetch error:", e)
    return None

def reply_to_comment(comment_id, text):
    # Fixed: Use correct endpoint
    url = f"https://graph.facebook.com/v20.0/{comment_id}/replies"
    payload = {"message": text, "access_token": IG_TOKEN}
    r = requests.post(url, data=payload, timeout=15)
    print(f"PUBLIC REPLY [{comment_id}]: {r.status_code} {r.text}")
    return r

def send_private_reply(comment_id, dm_text):
    url = f"https://graph.facebook.com/v20.0/{comment_id}/private_replies"
    payload = {"message": dm_text, "access_token": IG_TOKEN}
    r = requests.post(url, data=payload, timeout=15)
    print(f"PRIVATE REPLY [{comment_id}]: {r.status_code} {r.text}")
    return r

app = Flask(__name__)

@app.route('/')
def home(): return "Bot is Running! v2 PRO with DM Counter"

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    print(f"VERIFY ATTEMPT token={token} expected={VERIFY_TOKEN}")
    if mode=='subscribe' and token and token.strip()==VERIFY_TOKEN:
        print("WEBHOOK VERIFIED!")
        return challenge, 200
    return "Verification failed", 403

@app.route('/webhook', methods=['POST'])
def webhook_handler():
    data = request.get_json()
    print("WEBHOOK IN:", json.dumps(data,indent=2))
    if not data: return "ok",200
    if "entry" in data:
        for entry in data["entry"]:
            for change in entry.get("changes",[]):
                if change.get("field")=="comments":
                    value = change.get("value",{})
                    media_id = value.get("media",{}).get("id")
                    comment_id = value.get("id")
                    comment_text_raw = value.get("text","")
                    comment_text = comment_text_raw.lower()
                    from_user = value.get("from",{})
                    from_id = str(from_user.get("id",""))
                    username = from_user.get("username","unknown")
                    if from_id == str(IG_USER_ID): continue
                    print(f"NEW COMMENT: media={media_id} comment={comment_text_raw} from={username}")
                    config = load_config()
                    reel_cfg = config.get(media_id)
                    # Fallback via shortcode mapping
                    if not reel_cfg:
                        try:
                            info_url = f"https://graph.facebook.com/v20.0/{media_id}"
                            params = {"fields":"shortcode","access_token":IG_TOKEN}
                            sc = requests.get(info_url, params=params, timeout=10).json().get("shortcode")
                            if sc:
                                for mid,cfg in config.items():
                                    if cfg.get("shortcode")==sc:
                                        reel_cfg=cfg
                                        media_id=mid
                                        break
                        except Exception as e:
                            print("Shortcode map fail", e)
                    if not reel_cfg:
                        print(f"No config for {media_id}")
                        continue
                    should=False
                    if reel_cfg.get("type")=="all": should=True
                    else:
                        kws=[k.lower() for k in reel_cfg.get("keywords",[])]
                        if any(kw in comment_text for kw in kws): should=True
                    if should:
                        # 1. Public reply
                        pub_text = reel_cfg.get("comment_text","Check your DM! 🔥")
                        reply_to_comment(comment_id, pub_text)
                        # 2. Private DM
                        dm_link = reel_cfg.get("dm_link","")
                        btn_name = reel_cfg.get("button_name","")
                        btn_url = reel_cfg.get("button_url","")
                        dm_msg = f"Hey! Here's your link 👇\n{dm_link}"
                        if btn_name and btn_url:
                            dm_msg += f"\n\n{btn_name}: {btn_url}"
                        resp = send_private_reply(comment_id, dm_msg)
                        if resp.status_code==200:
                            total = add_dm_log(media_id, reel_cfg.get("shortcode",""), comment_id, comment_text_raw, username)
                            print(f"DM COUNT NOW: {total}")
                        else:
                            print("Private reply failed, not counting")
    return "ok",200

# Telegram
user_state={}

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    chat_id=update.effective_chat.id
    if ADMIN_ID!=0 and chat_id!=ADMIN_ID:
        await update.message.reply_text("Unauthorized"); return
    keyboard=[
        [InlineKeyboardButton("➕ New AutoDM Setup", callback_data="new_setup")],
        [InlineKeyboardButton("📋 My Active Reels", callback_data="list_reels")],
        [InlineKeyboardButton("📊 DMs - Total Sent", callback_data="dm_stats")],
        [InlineKeyboardButton("❌ Cancel AutoDM", callback_data="cancel_dm")],
        [InlineKeyboardButton("💬 Set Comment Text", callback_data="set_comment")]
    ]
    await update.message.reply_text("🔥 **PRO AutoDM Bot v2**\n\nReel ka link bhejo jisme AutoDM lagana hai.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    user_state[chat_id]={"step":"awaiting_reel_link"}

async def button_handler(update:Update, context:ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    chat_id=query.message.chat_id
    data=query.data

    if data=="new_setup":
        user_state[chat_id]={"step":"awaiting_reel_link"}
        await query.message.reply_text("🔗 Reel ka link bhejo:")
    elif data=="list_reels":
        config=load_config()
        if not config:
            await query.message.reply_text("Koi active reel nahi hai.")
        else:
            txt="📋 **Active Reels:**\n\n"
            stats=load_stats()
            for mid,cfg in config.items():
                count=len([l for l in stats.get("logs",[]) if l.get("media_id")==mid or l.get("shortcode")==cfg.get("shortcode")])
                txt+=f"• `{cfg.get('shortcode')}` - {cfg.get('type')} - DMs: {count}\n🔗 {cfg.get('reel_url')}\n\n"
            await query.message.reply_text(txt, parse_mode="Markdown")
    elif data=="dm_stats":
        stats=load_stats()
        total=stats.get("total",0)
        today=str(date.today())
        today_count=len([l for l in stats.get("logs",[]) if l.get("date")==today])
        last_5=stats.get("logs",[])[-5:][::-1]
        txt=f"📊 **DM STATS**\n\n🚀 Total DMs Sent: **{total}**\n📅 Today: **{today_count}**\n\n"
        if last_5:
            txt+="🕒 Last 5 DMs:\n"
            for l in last_5:
                txt+=f"• {l.get('time')} - {l.get('shortcode')} - {l.get('comment_text')[:20]}\n"
        else:
            txt+="Abhi tak koi DM nahi gaya."
        kb=[
            [InlineKeyboardButton("🔄 Refresh", callback_data="dm_stats")],
            [InlineKeyboardButton("🗑️ Clear Stats", callback_data="clear_stats")]
        ]
        await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    elif data=="clear_stats":
        save_stats({"total":0,"logs":[]})
        await query.message.reply_text("✅ Stats clear ho gaye! Total ab 0 hai.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 Back to Stats", callback_data="dm_stats")]]))
    elif data=="all_keywords":
        user_state[chat_id]["type"]="all"
        user_state[chat_id]["step"]="awaiting_dm_link"
        await query.message.reply_text("✅ All selected.\nAb DM LINK bhejo:")
    elif data=="custom_keywords":
        user_state[chat_id]["type"]="custom"
        user_state[chat_id]["step"]="awaiting_keywords"
        await query.message.reply_text("Keywords bhejo comma se alag (jaise: link, price, dm):")
    elif data=="cancel_dm":
        user_state[chat_id]={"step":"awaiting_cancel_link"}
        await query.message.reply_text("❌ Kaunsi reel se hatana hai? Uska link bhejo:")
    elif data=="set_comment":
        user_state[chat_id]={"step":"awaiting_comment_reel_link"}
        await query.message.reply_text("💬 Kaunsi reel ka comment text change karna hai? Link bhejo:")

async def message_handler(update:Update, context:ContextTypes.DEFAULT_TYPE):
    chat_id=update.effective_chat.id
    text=update.message.text.strip()
    if chat_id not in user_state:
        user_state[chat_id]={"step":"awaiting_reel_link"}
        await update.message.reply_text("Reel ka link bhejo:")
        return
    step=user_state[chat_id].get("step")

    if step=="awaiting_reel_link":
        if "instagram.com" not in text:
            await update.message.reply_text("Sahi Instagram reel link bhejo.")
            return
        sc=extract_shortcode(text)
        if not sc:
            await update.message.reply_text("Shortcode nahi mila, sahi link bhejo.")
            return
        await update.message.reply_text(f"Reel lock: {sc} ... Media ID nikal raha hu...")
        mid=get_media_id_from_shortcode(sc) or sc
        user_state[chat_id].update({"media_id":mid,"shortcode":sc,"reel_url":text,"step":"awaiting_keyword_type"})
        kb=[[InlineKeyboardButton("🌍 All Comments", callback_data="all_keywords")],[InlineKeyboardButton("🎯 Custom Keywords", callback_data="custom_keywords")]]
        await update.message.reply_text("Keyword type select karo:", reply_markup=InlineKeyboardMarkup(kb))

    elif step=="awaiting_keywords":
        kws=[k.strip() for k in text.split(",") if k.strip()]
        user_state[chat_id]["keywords"]=kws
        user_state[chat_id]["step"]="awaiting_dm_link"
        await update.message.reply_text(f"Keywords: {', '.join(kws)}\nAb DM LINK bhejo:")

    elif step=="awaiting_dm_link":
        user_state[chat_id]["dm_link"]=text
        user_state[chat_id]["step"]="awaiting_button_name"
        await update.message.reply_text("Button Name bhejo (jaise: JOIN NOW) - Skip karna hai to `skip` likho:")

    elif step=="awaiting_button_name":
        if text.lower()=="skip":
            user_state[chat_id]["button_name"]=""; user_state[chat_id]["button_url"]=""
            user_state[chat_id]["step"]="awaiting_comment_text"
            await update.message.reply_text("💬 Public comment me jo reply jayega wo text bhejo (jaise: Check your DM! 🔥):")
        else:
            user_state[chat_id]["button_name"]=text
            user_state[chat_id]["step"]="awaiting_button_url"
            await update.message.reply_text(f"Button Name: {text}\nAb Button ka URL bhejo:")

    elif step=="awaiting_button_url":
        if text.lower()=="skip":
            user_state[chat_id]["button_url"]=user_state[chat_id].get("dm_link","")
        else:
            user_state[chat_id]["button_url"]=text
        user_state[chat_id]["step"]="awaiting_comment_text"
        await update.message.reply_text("💬 Ab public comment ka text bhejo:")

    elif step=="awaiting_comment_text":
        config=load_config()
        mid=user_state[chat_id]["media_id"]
        config[mid]={
            "reel_url":user_state[chat_id].get("reel_url"),
            "shortcode":user_state[chat_id].get("shortcode"),
            "type":user_state[chat_id].get("type","all"),
            "keywords":user_state[chat_id].get("keywords",[]),
            "dm_link":user_state[chat_id].get("dm_link"),
            "button_name":user_state[chat_id].get("button_name",""),
            "button_url":user_state[chat_id].get("button_url",""),
            "comment_text":text
        }
        save_config(config)
        user_state.pop(chat_id,None)
        await update.message.reply_text(f"✅ **Active Ho Gaya!**\n\nReel: {mid}\nType: {config[mid]['type']}\nComment: {text}\nDM: {config[mid]['dm_link']}\n\nAb koi comment karega to DM count badhega. 📊 DMs button se dekh sakte ho.", parse_mode="Markdown")

    elif step=="awaiting_cancel_link":
        sc=extract_shortcode(text)
        mid=get_media_id_from_shortcode(sc) or sc
        config=load_config()
        deleted=False
        if mid in config: del config[mid]; deleted=True
        for m in list(config.keys()):
            if config[m].get("shortcode")==sc: del config[m]; deleted=True
        if deleted:
            save_config(config)
            await update.message.reply_text("✅ AutoDM hata diya!")
        else:
            await update.message.reply_text("❌ Reel active list me nahi mili.")
        user_state.pop(chat_id,None)

    elif step=="awaiting_comment_reel_link":
        sc=extract_shortcode(text)
        mid=get_media_id_from_shortcode(sc) or sc
        user_state[chat_id].update({"media_id":mid,"shortcode":sc,"step":"awaiting_new_comment_text"})
        await update.message.reply_text("Ab naya comment text bhejo:")

    elif step=="awaiting_new_comment_text":
        mid=user_state[chat_id]["media_id"]
        config=load_config()
        found=False
        for m in config:
            if m==mid or config[m].get("shortcode")==user_state[chat_id].get("shortcode"):
                config[m]["comment_text"]=text
                found=True
        if found:
            save_config(config)
            await update.message.reply_text(f"✅ Comment text update: {text}")
        else:
            await update.message.reply_text("❌ Reel nahi mili, pehle setup karo.")
        user_state.pop(chat_id,None)

async def run_telegram_async():
    if not TG_TOKEN: return
    application=Application.builder().token(TG_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    print("Telegram PRO Bot Started...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    while True: await asyncio.sleep(3600)

def run_flask():
    port=int(os.environ.get("PORT",10000))
    app.run(host='0.0.0.0', port=port)

def start_telegram_thread():
    loop=asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_telegram_async())

if __name__=="__main__":
    flask_thread=threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    time.sleep(2)
    start_telegram_thread()
    
