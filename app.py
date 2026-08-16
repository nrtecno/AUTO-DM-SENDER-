
import os, re, json, requests, threading
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv
import telebot
from telebot import types

load_dotenv()
IG_USER_ID = os.getenv("IG_USER_ID")
IG_TOKEN = os.getenv("IG_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "auto123")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_TELEGRAM_ID")
CONFIG_FILE = "reels_config.json"
USER_STATE_FILE = "user_state.json"
app = Flask(__name__)
bot = telebot.TeleBot(TG_TOKEN, threaded=True) if TG_TOKEN else None

def load_config():
    if not os.path.exists(CONFIG_FILE): return {}
    try:
        with open(CONFIG_FILE, 'r') as f: return json.load(f)
    except: return {}
def save_config(data):
    with open(CONFIG_FILE, 'w') as f: json.dump(data, f, indent=2)
def load_states():
    if not os.path.exists(USER_STATE_FILE): return {}
    try:
        with open(USER_STATE_FILE, 'r') as f: return json.load(f)
    except: return {}
def save_states(data):
    with open(USER_STATE_FILE, 'w') as f: json.dump(data, f, indent=2)
def extract_shortcode(url):
    import re
    m = re.search(r'/(reel|p|reels)/([A-Za-z0-9_-]+)', url)
    return m.group(2) if m else None
def get_media_id_from_shortcode(shortcode):
    if not IG_USER_ID or not IG_TOKEN: return None
    url = f"https://graph.facebook.com/v22.0/{IG_USER_ID}/media"
    params = {"fields": "id,shortcode", "access_token": IG_TOKEN, "limit": 100}
    try:
        r = requests.get(url, params=params, timeout=20)
        data = r.json()
        for media in data.get("data", []):
            if media.get("shortcode") == shortcode:
                return media.get("id")
        return None
    except Exception as e:
        print("Media fetch error:", e)
        return None
def reply_to_comment(comment_id, text):
    url = f"https://graph.facebook.com/v22.0/{comment_id}/replies"
    r = requests.post(url, data={"message": text, "access_token": IG_TOKEN})
    print(f"[COMMENT REPLY] {r.text}")
    return r
def send_private_reply(comment_id, dm_text):
    url = f"https://graph.facebook.com/v22.0/{comment_id}/private_replies"
    r = requests.post(url, data={"message": dm_text, "access_token": IG_TOKEN})
    print(f"[PRIVATE REPLY] {r.text}")
    return r

DASHBOARD = """
<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>NR AutoDM WebApp</title>
<style>
body{font-family:system-ui;background:#0f0f10;color:#fff;margin:0;padding:20px}
.card{background:#1c1c1f;border:1px solid #2a2a2e;border-radius:16px;padding:16px;margin:12px 0}
.btn{padding:10px 16px;border-radius:10px;border:0;font-weight:700;cursor:pointer}
.btn-d{background:#ff3b30;color:#fff}
.badge{background:#2a2a2e;padding:4px 10px;border-radius:20px;font-size:12px}
a{color:#8ab4f8;text-decoration:none}
</style></head><body>
<h2>🚀 NR AutoDM - Flask + pyTelegramBotAPI (Python 3.12)</h2>
<p>IG: {{ig_id}} | Active: {{count}}</p>
{% for mid,cfg in config.items() %}
<div class="card">
<b>Reel:</b> <a href="{{cfg.reel_url}}" target="_blank">{{cfg.shortcode}}</a> <span class="badge">{{cfg.type}}</span><br>
<b>ID:</b> {{mid}}<br>
<b>Keywords:</b> {{cfg.keywords}}<br>
<b>DM:</b> {{cfg.dm_link}}<br>
<b>Button:</b> {{cfg.button_name}} -> {{cfg.button_url}}<br>
<b>Comment:</b> {{cfg.comment_text}}<br><br>
<button class="btn btn-d" onclick="delReel('{{mid}}')">Cancel DM</button>
</div>
{% else %}
<div class="card">No active reels. Use Telegram bot /start</div>
{% endfor %}
<script>
function delReel(id){
 if(!confirm('Band karna hai?')) return;
 fetch('/api/delete/'+id,{method:'DELETE'}).then(()=>location.reload())
}
</script>
</body></html>
"""

@app.route('/')
def dash():
    cfg = load_config()
    return render_template_string(DASHBOARD, config=cfg, count=len(cfg), ig_id=IG_USER_ID)

@app.route('/webhook', methods=['GET'])
def verify():
    if request.args.get('hub.mode') == 'subscribe' and request.args.get('hub.verify_token') == VERIFY_TOKEN:
        return request.args.get('hub.challenge'), 200
    return "Failed", 403

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    print("[WEBHOOK]", json.dumps(data, indent=2))
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") == "comments":
                    v = change.get("value", {})
                    media_id = v.get("media", {}).get("id")
                    comment_id = v.get("id")
                    comment_text = v.get("text","").lower()
                    cfg_all = load_config()
                    matched = media_id if media_id in cfg_all else None
                    if matched:
                        rc = cfg_all[matched]
                        should = rc.get("type") == "all" or any(k.lower() in comment_text for k in rc.get("keywords",[]))
                        if should:
                            reply_to_comment(comment_id, rc.get("comment_text","Check your DM!"))
                            msg = f"Hey! Here's your link 👇\n{rc.get('dm_link','')}"
                            if rc.get("button_name") and rc.get("button_url"):
                                msg += f"\n\n{rc.get('button_name')}: {rc.get('button_url')}"
                            send_private_reply(comment_id, msg)
    except Exception as e:
        print("Webhook err:", e)
    return "EVENT_RECEIVED", 200

@app.route('/api/delete/<mid>', methods=['DELETE'])
def api_del(mid):
    cfg = load_config()
    if mid in cfg:
        del cfg[mid]
        save_config(cfg)
        return jsonify({"ok":True})
    for k in list(cfg.keys()):
        if cfg[k].get("shortcode")==mid:
            del cfg[k]
            save_config(cfg)
            return jsonify({"ok":True})
    return jsonify({"error":"not found"}),404

def is_admin(uid):
    if not ADMIN_ID: return True
    return str(uid)==str(ADMIN_ID)
def set_state(chat_id, st):
    all_st = load_states()
    all_st[str(chat_id)] = st
    save_states(all_st)
def get_state(chat_id):
    return load_states().get(str(chat_id), {})

@bot.message_handler(commands=['start'])
def start_cmd(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Unauthorized")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ New AutoDM Setup", callback_data="new_setup"),
        types.InlineKeyboardButton("📋 My Active Reels", callback_data="list_reels"),
        types.InlineKeyboardButton("❌ Cancel DM", callback_data="cancel_dm"),
        types.InlineKeyboardButton("💬 Set Comment Text", callback_data="set_comment")
    )
    bot.send_message(message.chat.id, "🤖 *NR AutoDM - pyTelegramBotAPI + Flask WebApp*\n\nReel ka link bhejo jisme AutoDM lagana hai.", parse_mode="Markdown", reply_markup=markup)
    set_state(message.chat.id, {"step":"idle"})

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    chat_id = call.message.chat.id
    if not is_admin(call.from_user.id): return
    data = call.data
    if data=="new_setup":
        set_state(chat_id, {"step":"awaiting_reel_link"})
        bot.send_message(chat_id, "🔗 Reel ka link bhejo:")
    elif data=="all_keywords":
        st=get_state(chat_id); st["type"]="all"; st["step"]="awaiting_dm_link"; set_state(chat_id,st)
        bot.send_message(chat_id, "✅ All selected.\nAb DM LINK bhejo:")
    elif data=="custom_keywords":
        st=get_state(chat_id); st["type"]="custom"; st["step"]="awaiting_keywords"; set_state(chat_id,st)
        bot.send_message(chat_id, "✍️ Keywords likho comma se (ex: link,dm,price)")
    elif data=="list_reels":
        cfg=load_config()
        if not cfg: bot.send_message(chat_id, "Koi active nahi.")
        else:
            txt="📋 Active:\n\n"
            for mid,c in cfg.items(): txt+=f"{c.get('shortcode')} | {c.get('type')} | {c.get('dm_link')}\n"
            bot.send_message(chat_id, txt)
    elif data=="cancel_dm":
        set_state(chat_id, {"step":"awaiting_cancel_link"})
        bot.send_message(chat_id, "❌ Kaunsi reel ka band karna hai? Link bhejo:")
    elif data=="set_comment":
        set_state(chat_id, {"step":"awaiting_comment_reel_link"})
        bot.send_message(chat_id, "💬 Kaunsi reel ke liye comment set karna hai? Link bhejo:")
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: True)
def all_msg(message):
    chat_id=message.chat.id
    if not is_admin(message.from_user.id): return
    text=message.text.strip()
    st=get_state(chat_id)
    step=st.get("step","idle")
    if step=="idle" and "instagram.com" in text:
        sc=extract_shortcode(text)
        if not sc:
            bot.send_message(chat_id, "❌ Sahi link bhejo"); return
        bot.send_message(chat_id, f"🔍 Found: {sc}")
        mid=get_media_id_from_shortcode(sc) or sc
        set_state(chat_id, {"step":"awaiting_keyword_type","media_id":mid,"shortcode":sc,"reel_url":text})
        mk=types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("🌍 All Keywords", callback_data="all_keywords"))
        mk.add(types.InlineKeyboardButton("🎯 Custom Keywords", callback_data="custom_keywords"))
        bot.send_message(chat_id, "Reel lock! Choose karo:", reply_markup=mk)
        return
    if step=="awaiting_reel_link":
        if "instagram.com" not in text:
            bot.send_message(chat_id, "Sahi reel link bhejo"); return
        sc=extract_shortcode(text); mid=get_media_id_from_shortcode(sc) or sc
        st.update({"media_id":mid,"shortcode":sc,"reel_url":text,"step":"awaiting_keyword_type"}); set_state(chat_id,st)
        mk=types.InlineKeyboardMarkup(); mk.add(types.InlineKeyboardButton("🌍 All",callback_data="all_keywords")); mk.add(types.InlineKeyboardButton("🎯 Custom",callback_data="custom_keywords"))
        bot.send_message(chat_id, "Reel lock!", reply_markup=mk)
    elif step=="awaiting_keywords":
        kws=[k.strip() for k in text.split(",") if k.strip()]
        st["keywords"]=kws; st["step"]="awaiting_dm_link"; set_state(chat_id,st)
        bot.send_message(chat_id, f"Keywords: {', '.join(kws)}\n\nAb DM LINK bhejo:")
    elif step=="awaiting_dm_link":
        st["dm_link"]=text; st["step"]="awaiting_button_name"; set_state(chat_id,st)
        bot.send_message(chat_id, "🔗 DM Link save.\n\nButton Name bhejo (skip likho agar nahi chahiye):")
    elif step=="awaiting_button_name":
        if text.lower()=="skip":
            st["button_name"]=""; st["button_url"]=""; st["step"]="awaiting_comment_text"; set_state(chat_id,st)
            bot.send_message(chat_id, "💬 Comment reply text bhejo (Ex: Check DM!):")
        else:
            st["button_name"]=text; st["step"]="awaiting_button_url"; set_state(chat_id,st)
            bot.send_message(chat_id, f"Button: {text}\nAb Button ka URL bhejo:")
    elif step=="awaiting_button_url":
        st["button_url"]=text; st["step"]="awaiting_comment_text"; set_state(chat_id,st)
        bot.send_message(chat_id, "💬 Comment reply text bhejo:")
    elif step=="awaiting_comment_text":
        cfg=load_config(); mid=st["media_id"]
        cfg[mid]={"reel_url":st.get("reel_url"),"shortcode":st.get("shortcode"),"type":st.get("type","all"),"keywords":st.get("keywords",[]),"dm_link":st.get("dm_link"),"button_name":st.get("button_name",""),"button_url":st.get("button_url",""),"comment_text":text}
        save_config(cfg); set_state(chat_id,{"step":"idle"})
        bot.send_message(chat_id, f"✅ *Active Ho Gaya!*\nReel: {mid}\nType: {cfg[mid]['type']}\nDM: {cfg[mid]['dm_link']}\nComment: {text}", parse_mode="Markdown")
    elif step=="awaiting_cancel_link":
        sc=extract_shortcode(text); cfg=load_config(); deleted=False
        for k in list(cfg.keys()):
            if k==sc or cfg[k].get("shortcode")==sc:
                del cfg[k]; deleted=True
        if deleted: save_config(cfg); bot.send_message(chat_id, "✅ Band kar diya!")
        else: bot.send_message(chat_id, "❌ Nahi mila")
        set_state(chat_id,{"step":"idle"})
    elif step=="awaiting_comment_reel_link":
        sc=extract_shortcode(text); st["media_id"]=sc; st["step"]="awaiting_new_comment_text"; set_state(chat_id,st)
        bot.send_message(chat_id, "Naya comment text bhejo:")
    elif step=="awaiting_new_comment_text":
        cfg=load_config(); found=False
        for k in cfg:
            if k==st.get("media_id") or cfg[k].get("shortcode")==st.get("media_id"):
                cfg[k]["comment_text"]=text; found=True
        if found: save_config(cfg); bot.send_message(chat_id, f"✅ Update: {text}")
        else: bot.send_message(chat_id, "Active me nahi hai")
        set_state(chat_id,{"step":"idle"})

def run_bot():
    if not bot: print("No TG Token"); return
    print("Bot polling...")
    bot.infinity_polling(skip_pending=True)

if __name__=="__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port=int(os.environ.get("PORT",10000))
    app.run(host='0.0.0.0', port=port)
                       
