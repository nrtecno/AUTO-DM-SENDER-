import os
import re
import io
import json
import time
import logging
import threading
import asyncio

import requests
from flask import Flask, request, jsonify

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ----------------------------------------------------------------------------
# LOGGING
# ----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("igbot")

# ----------------------------------------------------------------------------
# ENV VARS (exact names as used on Render)
# ----------------------------------------------------------------------------
ADMIN_TELEGRAM_ID = os.environ.get("ADMIN_TELEGRAM_ID", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
IG_USER_ID = os.environ.get("IG_USER_ID", "")
IG_BUSINESS_ID = os.environ.get("IG_BUSINESS_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "auto123")

# Optional, only needed for long-lived token exchange/refresh.
IG_APP_SECRET = os.environ.get("IG_APP_SECRET", "")

# If true, comments only match a config when media_id matches exactly.
# With multiple reels tracked at once, this should normally stay true.
# Configs saved without a resolvable media_id act as a catch-all fallback
# regardless of this setting (so testing still works even if lookup failed).
STRICT_MEDIA_MATCH = os.environ.get("STRICT_MEDIA_MATCH", "true").lower() == "true"

EFFECTIVE_IG_ID = IG_USER_ID or IG_BUSINESS_ID

# In-memory token, may get replaced by a fresh long-lived token at startup.
ACCESS_TOKEN = PAGE_ACCESS_TOKEN

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")

# ----------------------------------------------------------------------------
# DATA STORE
# ----------------------------------------------------------------------------
# Structure (supports multiple reels per admin):
# {
#   "<admin_telegram_id>": {
#     "<shortcode>": {
#         "shortcode", "reel_url", "media_id", "keyword_type",
#         "dm_link", "button_name", "follow_only"
#     },
#     ...
#   }
# }
_data_lock = threading.Lock()


def load_data():
    with _data_lock:
        if not os.path.exists(DATA_FILE):
            return {}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}


def save_data(data):
    with _data_lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------------
# TELEGRAM NOTIFY HELPER (plain HTTP, works from any thread, no asyncio needed)
# ----------------------------------------------------------------------------
def notify_admin(text: str):
    if not BOT_TOKEN or not ADMIN_TELEGRAM_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_TELEGRAM_ID, "text": text},
            timeout=10,
        )
    except requests.RequestException as e:
        log.warning("notify_admin failed: %s", e)


# ----------------------------------------------------------------------------
# INSTAGRAM GRAPH API HELPERS
# ----------------------------------------------------------------------------
def graph_post_with_fallback(path_suffix, payload):
    urls = [
        f"https://graph.instagram.com/v22.0/{path_suffix}",
        f"https://graph.facebook.com/v19.0/{path_suffix}",
    ]
    last_result = None
    for url in urls:
        try:
            resp = requests.post(
                url,
                json=payload,
                params={"access_token": ACCESS_TOKEN},
                timeout=15,
            )
            ok = resp.status_code == 200
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            last_result = (ok, body, url)
            if ok:
                return last_result
            log.warning("Graph POST failed on %s -> %s %s", url, resp.status_code, body)
        except requests.RequestException as e:
            log.warning("Graph POST exception on %s -> %s", url, e)
            last_result = (False, str(e), url)
    return last_result


def reply_to_comment(comment_id, message_text):
    ok, body, url = graph_post_with_fallback(f"{comment_id}/replies", {"message": message_text})
    log.info("REPLY STATUS - comment_id=%s ok=%s url=%s body=%s", comment_id, ok, url, body)
    if not ok:
        notify_admin(f"⚠️ Comment reply FAILED for comment_id={comment_id}\n{body}")
    return ok


def send_dm(recipient_id, config):
    dm_link = config.get("dm_link", "")
    button_name = config.get("button_name", "skip")

    if button_name and button_name.lower() != "skip":
        message = {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": f"Ye raha link 👇 {dm_link}",
                    "buttons": [
                        {"type": "web_url", "url": dm_link, "title": button_name[:20]}
                    ],
                },
            }
        }
    else:
        message = {"text": f"Ye raha link 👇\n{dm_link}"}

    payload = {"recipient": {"id": recipient_id}, "message": message}
    ok, body, url = graph_post_with_fallback(f"{EFFECTIVE_IG_ID}/messages", payload)
    log.info("DM STATUS - recipient=%s ok=%s url=%s body=%s", recipient_id, ok, url, body)

    if ok:
        notify_admin(f"✅ DM sent to {recipient_id} (reel: {config.get('shortcode')})")
    else:
        notify_admin(f"❌ DM FAILED to {recipient_id} (reel: {config.get('shortcode')})\n{body}")
    return ok


def fetch_media_id_by_shortcode(shortcode, max_pages=10):
    """Look up media id matching a shortcode, paging through all recent media."""
    url = f"https://graph.instagram.com/v22.0/{IG_USER_ID}/media"
    params = {"fields": "id,shortcode", "limit": 100, "access_token": ACCESS_TOKEN}

    for _ in range(max_pages):
        try:
            resp = requests.get(url, params=params, timeout=15)
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("fetch_media_id_by_shortcode failed: %s", e)
            return None

        for item in data.get("data", []):
            if item.get("shortcode") == shortcode:
                return item.get("id")

        next_url = data.get("paging", {}).get("next")
        if not next_url:
            break
        url = next_url
        params = None  # next_url already contains all query params

    return None


def subscribe_webhook():
    if not ACCESS_TOKEN:
        log.warning("Skipping webhook subscribe: missing access token")
        return

    candidate_ids = ["me"]
    if EFFECTIVE_IG_ID:
        candidate_ids.append(EFFECTIVE_IG_ID)

    for ig_id in candidate_ids:
        url = f"https://graph.instagram.com/v22.0/{ig_id}/subscribed_apps"
        try:
            resp = requests.post(
                url,
                params={"subscribed_fields": "comments", "access_token": ACCESS_TOKEN},
                timeout=15,
            )
            log.info("SUBSCRIBE WEBHOOK (id=%s) -> %s %s", ig_id, resp.status_code, resp.text)
            if resp.status_code == 200:
                return
        except requests.RequestException as e:
            log.warning("SUBSCRIBE WEBHOOK (id=%s) failed: %s", ig_id, e)

    log.warning(
        "SUBSCRIBE WEBHOOK failed on all candidate IDs. Check that PAGE_ACCESS_TOKEN "
        "has instagram_business_manage_messages/comments scopes and that IG_USER_ID "
        "matches the token's actual account (compare via GET /me?access_token=...)."
    )
    notify_admin("⚠️ Webhook subscribe failed on startup. DMs/replies will NOT work until this is fixed. Check Render logs.")


def exchange_for_long_lived_token():
    global ACCESS_TOKEN
    if not IG_APP_SECRET:
        log.warning("IG_APP_SECRET not set, skipping long-lived token exchange (using short-lived token as-is)")
        return
    url = "https://graph.instagram.com/access_token"
    params = {
        "grant_type": "ig_exchange_token",
        "client_secret": IG_APP_SECRET,
        "access_token": ACCESS_TOKEN,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if resp.status_code == 200 and "access_token" in data:
            ACCESS_TOKEN = data["access_token"]
            expires_in = data.get("expires_in", "?")
            log.info("TOKEN EXCHANGE OK - new long-lived token active in-memory, expires_in=%s sec", expires_in)
            notify_admin(
                f"🔑 Instagram token refreshed (expires_in={expires_in}s). "
                "Update PAGE_ACCESS_TOKEN on Render with this new value so it survives restarts "
                "(check Render logs for the exact token, it's not sent here for safety)."
            )
        else:
            log.warning("TOKEN EXCHANGE FAILED -> %s %s", resp.status_code, data)
            notify_admin(f"⚠️ Instagram token refresh FAILED: {data}")
    except (requests.RequestException, ValueError) as e:
        log.warning("TOKEN EXCHANGE exception: %s", e)


def token_refresh_loop():
    """Background thread: exchange once at startup, then refresh every ~50 days."""
    exchange_for_long_lived_token()
    while True:
        time.sleep(50 * 24 * 60 * 60)  # ~50 days
        exchange_for_long_lived_token()


# ----------------------------------------------------------------------------
# FLASK APP
# ----------------------------------------------------------------------------
flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET"])
def index():
    return "Bot Live"


@flask_app.route("/webhook", methods=["GET"])
def webhook_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


@flask_app.route("/webhook", methods=["POST"])
def webhook_receive():
    body = request.get_json(silent=True) or {}
    log.info("WEBHOOK AAYA - raw=%s", json.dumps(body)[:2000])

    all_data = load_data()

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue

            value = change.get("value", {})
            comment_id = value.get("id")
            text = value.get("text", "")
            commenter_id = value.get("from", {}).get("id")
            media_id = value.get("media", {}).get("id")

            log.info(
                "WEBHOOK AAYA - comment_id=%s text=%s commenter_id=%s media_id=%s",
                comment_id, text, commenter_id, media_id,
            )

            if not commenter_id or not comment_id:
                continue

            if commenter_id == EFFECTIVE_IG_ID:
                log.info("SKIP - self comment")
                continue

            matched_config = None

            # Pass 1: exact media_id match across every admin's saved automations.
            for admin_id, automations in all_data.items():
                for shortcode, config in automations.items():
                    if config.get("keyword_type") != "all":
                        continue
                    if config.get("media_id") and media_id and config["media_id"] == media_id:
                        matched_config = config
                        break
                if matched_config:
                    break

            # Pass 2: catch-all fallback for configs where media_id lookup failed,
            # only if nothing matched exactly and strict mode allows it.
            if not matched_config and not STRICT_MEDIA_MATCH:
                for admin_id, automations in all_data.items():
                    for shortcode, config in automations.items():
                        if config.get("keyword_type") == "all" and not config.get("media_id"):
                            matched_config = config
                            break
                    if matched_config:
                        break

            if not matched_config:
                log.info("NO MATCH - no active config for this comment (media_id=%s)", media_id)
                continue

            log.info("MATCHED - shortcode=%s dm_link=%s button_name=%s",
                      matched_config.get("shortcode"), matched_config.get("dm_link"), matched_config.get("button_name"))

            reply_to_comment(comment_id, "DM check karo, link bhej diya 🚀")
            send_dm(commenter_id, matched_config)

    return jsonify({"status": "ok"}), 200


# ----------------------------------------------------------------------------
# TELEGRAM BOT (admin-only setup flow)
# ----------------------------------------------------------------------------
WAITING_REEL_LINK, WAITING_KEYWORD, WAITING_DM_LINK, WAITING_BUTTON_NAME, WAITING_FOLLOW_ONLY = range(5)
WAITING_RESTORE_FILE = 100


def is_admin(user_id) -> bool:
    return ADMIN_TELEGRAM_ID != "" and str(user_id) == str(ADMIN_TELEGRAM_ID)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Ye bot sirf admin ke liye hai.")
        return ConversationHandler.END

    await update.message.reply_text(
        "Naye reel ka link bhejo (jaise instagram.com/reel/XXXXXXX/)\n\n"
        "Tip: /list se saari active automations dekh sakte ho, /remove se hata sakte ho."
    )
    return WAITING_REEL_LINK


async def receive_reel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    match = re.search(r"/(?:reel|p)/([A-Za-z0-9_-]+)", text)
    if not match:
        await update.message.reply_text("Link samajh nahi aaya, format check karo: .../reel/SHORTCODE/ ya .../p/SHORTCODE/")
        return WAITING_REEL_LINK

    shortcode = match.group(1)
    await update.message.reply_text("Media ID fetch kar raha hoon (saari reels check kar raha hoon, thoda time lag sakta hai)...")
    media_id = fetch_media_id_by_shortcode(shortcode)

    context.user_data["shortcode"] = shortcode
    context.user_data["reel_url"] = text
    context.user_data["media_id"] = media_id

    if media_id:
        await update.message.reply_text(f"Media ID mil gaya: {media_id}")
    else:
        await update.message.reply_text(
            "Media ID nahi mila (bahut purani reel ho sakti hai, ya token/permission issue). "
            "Agar STRICT_MEDIA_MATCH=false hai to ye reel catch-all ki tarah kaam karegi -- "
            "matlab har naya comment (kisi bhi reel pe) is config se match ho sakta hai. Aage badh sakte ho."
        )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("All Comments", callback_data="kw_all")]]
    )
    await update.message.reply_text("Keyword type chuno:", reply_markup=keyboard)
    return WAITING_KEYWORD


async def receive_keyword_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["keyword_type"] = "all"
    await query.edit_message_text("Keyword type: All Comments ✅")
    await query.message.reply_text("Ab DM link bhejo (jo commenter ko DM mein jayega):")
    return WAITING_DM_LINK


async def receive_dm_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dm_link"] = update.message.text.strip()
    await update.message.reply_text(
        "Button ka naam bhejo (jaise JOIN NOW), ya 'skip' likho text-only DM ke liye:"
    )
    return WAITING_BUTTON_NAME


async def receive_button_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["button_name"] = update.message.text.strip()

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("OFF (recommended)", callback_data="follow_off")],
            [InlineKeyboardButton("ON", callback_data="follow_on")],
        ]
    )
    await update.message.reply_text(
        "Follow Only ON/OFF? (Instagram API abhi follow-check support NAHI karta -- "
        "ye ek platform limitation hai, ON select karoge to bhi OFF hi use hoga)",
        reply_markup=keyboard,
    )
    return WAITING_FOLLOW_ONLY


async def receive_follow_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["follow_only"] = False  # forced off, API limitation
    if query.data == "follow_on":
        await query.edit_message_text("Follow Only: OFF (ON abhi Instagram API mein supported nahi hai) ✅")
    else:
        await query.edit_message_text("Follow Only: OFF ✅")

    admin_id = str(update.effective_user.id)
    shortcode = context.user_data.get("shortcode")
    config = {
        "shortcode": shortcode,
        "reel_url": context.user_data.get("reel_url"),
        "media_id": context.user_data.get("media_id"),
        "keyword_type": context.user_data.get("keyword_type", "all"),
        "dm_link": context.user_data.get("dm_link"),
        "button_name": context.user_data.get("button_name"),
        "follow_only": False,
    }

    data = load_data()
    data.setdefault(admin_id, {})[shortcode] = config
    save_data(data)

    total = len(data[admin_id])
    await query.message.reply_text(
        "Setup ho gaya! Is reel pe koi bhi comment karega to reply + DM chala jayega.\n\n"
        f"Reel: {config['reel_url']}\n"
        f"DM link: {config['dm_link']}\n"
        f"Button: {config['button_name']}\n\n"
        f"Total active automations: {total}. /list se dekh sakte ho."
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancel kar diya. /start se dobara shuru karo.")
    return ConversationHandler.END


async def list_automations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    admin_id = str(update.effective_user.id)
    data = load_data()
    automations = data.get(admin_id, {})

    if not automations:
        await update.message.reply_text("Koi active automation nahi hai. /start se naya banao.")
        return

    lines = ["📋 Active automations:\n"]
    for shortcode, config in automations.items():
        lines.append(
            f"• {shortcode} -- {config.get('reel_url')}\n"
            f"  DM: {config.get('dm_link')} | Button: {config.get('button_name')}\n"
            f"  media_id: {config.get('media_id') or 'NOT FOUND (catch-all)'}"
        )
    lines.append("\nHatane ke liye /remove use karo.")
    await update.message.reply_text("\n\n".join(lines))


async def remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    admin_id = str(update.effective_user.id)
    data = load_data()
    automations = data.get(admin_id, {})

    if not automations:
        await update.message.reply_text("Koi active automation nahi hai hatane ke liye.")
        return

    buttons = [
        [InlineKeyboardButton(f"❌ {shortcode}", callback_data=f"rm_{shortcode}")]
        for shortcode in automations.keys()
    ]
    await update.message.reply_text(
        "Kaunsi automation hatani hai?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    shortcode = query.data.replace("rm_", "", 1)
    admin_id = str(query.from_user.id)
    data = load_data()

    if admin_id in data and shortcode in data[admin_id]:
        del data[admin_id][shortcode]
        save_data(data)
        await query.edit_message_text(f"✅ Hata diya: {shortcode}")
    else:
        await query.edit_message_text("Ye automation pehle se nahi mili (shayad already hata di gayi).")


async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the current data.json to the admin, so it can be restored after a
    Render restart (data.json itself is NOT persistent storage on Render)."""
    if not is_admin(update.effective_user.id):
        return
    if not os.path.exists(DATA_FILE):
        await update.message.reply_text("Abhi tak koi data save nahi hua.")
        return
    with open(DATA_FILE, "rb") as f:
        await update.message.reply_document(
            document=InputFile(f, filename="data_backup.json"),
            caption="Ye backup safe rakho. Restart ke baad /restore se wapas load kar sakte ho.",
        )


async def restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    await update.message.reply_text("Backup ki gayi data_backup.json file yahan bhejo.")
    return WAITING_RESTORE_FILE


async def restore_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".json"):
        await update.message.reply_text("Ye .json file nahi lagi. Sahi backup file bhejo, ya /cancel.")
        return WAITING_RESTORE_FILE

    tg_file = await doc.get_file()
    file_bytes = await tg_file.download_as_bytearray()

    try:
        parsed = json.loads(file_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        await update.message.reply_text("File parse nahi ho payi, corrupt lag rahi hai.")
        return ConversationHandler.END

    save_data(parsed)
    await update.message.reply_text("✅ Restore ho gaya! /list se check kar lo.")
    return ConversationHandler.END


def build_telegram_app():
    application = Application.builder().token(BOT_TOKEN).build()

    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_REEL_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reel_link)],
            WAITING_KEYWORD: [CallbackQueryHandler(receive_keyword_type, pattern="^kw_all$")],
            WAITING_DM_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_dm_link)],
            WAITING_BUTTON_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_button_name)],
            WAITING_FOLLOW_ONLY: [CallbackQueryHandler(receive_follow_only, pattern="^follow_(on|off)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    restore_conv = ConversationHandler(
        entry_points=[CommandHandler("restore", restore_start)],
        states={
            WAITING_RESTORE_FILE: [MessageHandler(filters.Document.ALL, restore_receive)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(setup_conv)
    application.add_handler(restore_conv)
    application.add_handler(CommandHandler("list", list_automations))
    application.add_handler(CommandHandler("remove", remove_start))
    application.add_handler(CommandHandler("backup", backup))
    application.add_handler(CallbackQueryHandler(remove_confirm, pattern="^rm_"))

    return application


# ----------------------------------------------------------------------------
# STARTUP / RUN
# ----------------------------------------------------------------------------
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)


def run_telegram_bot():
    """Runs polling with basic retry so a Conflict error doesn't crash the process."""
    application = build_telegram_app()
    while True:
        # Python 3.12+/3.14 no longer auto-creates an event loop in the main
        # thread, so we must create and set one explicitly before run_polling().
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            application.run_polling(drop_pending_updates=True, close_loop=False)
            break  # run_polling returned normally (e.g. on shutdown)
        except Exception as e:
            log.warning("Telegram polling error: %s -- retrying in 10s", e)
            time.sleep(10)
        finally:
            try:
                loop.close()
            except Exception:
                pass


if __name__ == "__main__":
    missing = [
        name for name, val in [
            ("ADMIN_TELEGRAM_ID", ADMIN_TELEGRAM_ID),
            ("BOT_TOKEN", BOT_TOKEN),
            ("PAGE_ACCESS_TOKEN", PAGE_ACCESS_TOKEN),
            ("IG_USER_ID/IG_BUSINESS_ID", EFFECTIVE_IG_ID),
        ] if not val
    ]
    if missing:
        log.warning("Missing env vars: %s -- some features will not work until these are set.", missing)

    # Flask must bind to $PORT for Render to see the service as live.
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Token exchange + periodic refresh in the background.
    token_thread = threading.Thread(target=token_refresh_loop, daemon=True)
    token_thread.start()

    # Give the token thread a moment to run the first exchange before subscribing.
    time.sleep(2)
    subscribe_webhook()

    # Telegram bot runs in the main thread (blocking).
    run_telegram_bot()
