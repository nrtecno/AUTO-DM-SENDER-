import os, re, json, requests, threading, asyncio, logging
from flask import Flask, request
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

def get_env(k): return (os.environ.get(k,"") or "").strip().strip('"').strip("'")
BOT_TOKEN = get_env("BOT_TOKEN")
PAGE_ACCESS_TOKEN = get_env("PAGE_ACCESS_TOKEN")
IG_BUSINESS_ID = get_env("IG_BUSINESS_ID")
IG_USER_ID = get_env("IG_USER_ID")
VERIFY_TOKEN = get_env("VERIFY_TOKEN") or "auto123"
ADMIN_TELEGRAM_ID = get_env("ADMIN_TELEGRAM_ID")
EFFECTIVE_IG_ID = IG_USER_ID or IG_BUSINESS_ID

print(f"ENV CHECK: BOT={bool(BOT_TOKEN)} PAGE_TOKEN={bool(PAGE_ACCESS_TOKEN)} IG_ID={EFFECTIVE_IG_ID} ADMIN={ADMIN_TELEGRAM_ID}")

DATA_FILE = "data.json"
user_states = {}
def load_data():
    if not os.path.exists(DATA_FILE): return {}
    try:
        with open(DATA_FILE,'r') as f: return json.load(f)
    except: return {}
def save_data(d):
    with open(DATA_FILE,'w') as f: json.dump(d,f,indent=2)

def send_comment_reply(comment_id):
    for url in [f"https://graph.instagram.com/v22.0/{comment_id}/replies", f"https://graph.facebook.com/v19.0/{comment_id}/replies"]:
        try:
            r = requests.post(url, json={"message":"DM check karo, link bhej diya 🚀","access_token":PAGE_ACCESS_TOKEN}, timeout=15)
            print(f"REPLY: {r.status_code} {r.text[:200]}")
            if r.ok: return True
        except Exception as e: print(f"Reply err {e}")
    return False

def send_dm(commenter_id, dm_link, btn_name):
    for url in [f"https://graph.instagram.com/v22.0/{EFFECTIVE_IG_ID}/messages", f"https://graph.facebook.com/v19.0/{EFFECTIVE_IG_ID}/messages"]:
        try:
            msg = {"text":f"Ye raha link 👇\n{dm_link}"}
            if btn_name and btn_name.lower()!='skip':
                msg = {"attachment":{"type":"template","payload":{"template_type":"button","text":f"Link 👇 {dm_link}","buttons":[{"type":"web_url","url":dm_link,"title":btn_name[:20]}]}}}
            r = requests.post(url, json={"recipient":{"id":commenter_id},"message":msg,"access_token":PAGE_ACCESS_TOKEN}, timeout=15)
            print(f"DM: {r.status_code} {r.text[:300]}")
            if r.ok: return True
        except Exception as e: print(f"DM err {e}")
    return False

@app.route('/')
def home(): return f"Bot Live | IG_ID={EFFECTIVE_IG_ID} | ADMIN={ADMIN_TELEGRAM_ID}"

@app.route('/webhook', methods=['GET','POST'])
def webhook():
    if request.method=='GET':
        if request.args.get('hub.verify_token')==VERIFY_TOKEN:
            return request.args.get('hub.challenge')
        return "Fail",403
    body=request.json
    print(f"WEBHOOK AAYA: {body}")
    data=load_data()
    for entry in body.get('entry',[]):
        for change in entry.get('changes',[]):
            if change.get('field')!='comments': continue
            val=change.get('value',{})
            comment_id=val.get('id')
            commenter_id=val.get('from',{}).get('id')
            media_id=val.get('media',{}).get('id')
            if not comment_id or not commenter_id: continue
            for cfg in data.values():
                if cfg.get('media_id') and media_id and str(cfg.get('media_id'))!=str(media_id): continue
                print(f"MATCH Reel {cfg.get('shortcode')}")
                send_comment_reply(comment_id)
                send_dm(commenter_id, cfg.get('dm_link'), cfg.get('button_name'))
    return "OK",200

def run_telegram():
    print(f"Starting Telegram with token {BOT_TOKEN[:10]}...")
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        print("BOT_TOKEN GALAT HAI")
        return
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
        from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
        def is_admin(uid):
            if not ADMIN_TELEGRAM_ID: return True
            return str(uid)==str(ADMIN_TELEGRAM_ID)
        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
            print(f"/start from {update.effective_user.id}")
            if not is_admin(update.effective_user.id):
                await update.message.reply_text(f"Access denied. Your ID: {update.effective_user.id}")
                return
            user_states[update.effective_user.id]={"step":"reel_link"}
            await update.message.reply_text("Reel ka link bhejo:")
        async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
            uid=update.effective_user.id
            if not is_admin(uid): return
            txt=update.message.text.strip()
            step=user_states.get(uid,{}).get("step")
            data=load_data(); cfg=data.get(str(uid),{})
            if step=="reel_link":
                m=re.search(r'/reel/([^/]+)/|/p/([^/]+)/',txt); sc=(m.group(1) or m.group(2)) if m else None
                cfg['shortcode']=sc; cfg['reel_url']=txt
                try:
                    url=f"https://graph.instagram.com/v22.0/{EFFECTIVE_IG_ID}/media?fields=id,shortcode&limit=100&access_token={PAGE_ACCESS_TOKEN}"
                    res=requests.get(url,timeout=10).json()
                    for it in res.get('data',[]):
                        if it.get('shortcode')==sc: cfg['media_id']=it.get('id'); break
                except Exception as e: print(e)
                save_data({**data,str(uid):cfg})
                kb=[[InlineKeyboardButton("🌍 All Comments",callback_data="kw_all")]]
                await update.message.reply_text(f"Reel: {sc}\nMedia ID: {cfg.get('media_id','auto')}",reply_markup=InlineKeyboardMarkup(kb))
            elif step=="dm_link":
                cfg['dm_link']=txt; save_data({**data,str(uid):cfg}); await update.message.reply_text("Button Name (skip = no button):"); user_states[uid]={"step":"btn_name"}
            elif step=="btn_name":
                cfg['button_name']=None if txt.lower()=="skip" else txt; save_data({**data,str(uid):cfg}); await update.message.reply_text(f"✅ ACTIVE\nReel:{cfg.get('shortcode')}\nLink:{cfg.get('dm_link')}\nButton:{cfg.get('button_name')}\n\nAb test karo social_nr se.")
        async def handle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
            q=update.callback_query; await q.answer(); uid=q.from_user.id
            if not is_admin(uid): return
            data=load_data(); cfg=data.get(str(uid),{})
            if q.data=="kw_all":
                cfg['keyword_type']='all'; save_data({**data,str(uid):cfg}); await q.message.reply_text("DM LINK bhejo:"); user_states[uid]={"step":"dm_link"}
        async def main():
            app_tg=Application.builder().token(BOT_TOKEN).build()
            app_tg.add_handler(CommandHandler("start",start)); app_tg.add_handler(CallbackQueryHandler(handle_cb)); app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_msg))
            await app_tg.initialize(); await app_tg.start(); await app_tg.updater.start_polling()
            print("Telegram Bot Live ✅")
            await asyncio.Event().wait()
        asyncio.run(main())
    except Exception as e:
        print(f"TELEGRAM CRASH: {e}")
        import traceback; traceback.print_exc()

threading.Thread(target=run_telegram,daemon=True).start()
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.environ.get("PORT",10000)))
