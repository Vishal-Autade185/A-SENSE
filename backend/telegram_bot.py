import os
import json
import logging
import requests
import threading
import time

logger = logging.getLogger("asense_telegram")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Callback function to handle incoming user commands from Telegram (injected by main.py)
agent_chat_fn = None
command_executor_fn = None

def register_handlers(chat_fn, cmd_fn):
    global agent_chat_fn, command_executor_fn
    agent_chat_fn = chat_fn
    command_executor_fn = cmd_fn

def send_telegram_message(text: str, inline_keyboard: list = None):
    """Send proactive text message or interactive buttons to Telegram safely."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram Bot Token or Chat ID not configured. Message skipped.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML" # HTML prevents strict Markdown parse failures
    }

    if inline_keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard})

    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            # Fallback to plain text if HTML had unescaped characters
            payload.pop("parse_mode", None)
            res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return False

def answer_callback_query(callback_query_id: str, text: str = "Action executed"):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=5)
    except Exception:
        pass

def send_chat_action(action: str = "typing"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendChatAction"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "action": action}, timeout=3)
    except Exception:
        pass

def _process_user_message_async(text: str):
    """Process user message with exact parity to the Web Dashboard."""
    try:
        send_chat_action("typing")
        if agent_chat_fn:
            agent_res = agent_chat_fn(text)
            reply = agent_res.get("reply", "Done.")
            
            # Append executed tools badge just like web dashboard!
            actions = agent_res.get("actions", [])
            badge = ""
            if actions:
                tool_names = ", ".join([a.get("tool") for a in actions])
                badge = f"\n\n🛠 <i>Executed: {tool_names}</i>"

            send_telegram_message(f"🤖 <b>A-SENSE:</b> {reply}{badge}")
    except Exception as e:
        logger.error(f"Error processing Telegram message: {e}")
        send_telegram_message(f"❌ Error: {e}")

# Snappy polling loop to listen for incoming Telegram messages and button clicks
def _telegram_polling_loop():
    if not TELEGRAM_BOT_TOKEN:
        logger.info("Telegram polling skipped (no token provided).")
        return

    logger.info("Telegram Bot listener started (optimized snappy polling).")
    last_update_id = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=5"
            res = requests.get(url, timeout=10)
            if res.status_code != 200:
                time.sleep(2)
                continue

            updates = res.json().get("result", [])
            for update in updates:
                last_update_id = update["update_id"]

                # Handle inline button click (instant physical execution)
                if "callback_query" in update:
                    cb = update["callback_query"]
                    cb_id = cb["id"]
                    data = cb.get("data", "")
                    if data.startswith("cmd:"):
                        parts = data.split(":")
                        dev = parts[1]
                        st = parts[2]
                        if command_executor_fn:
                            try:
                                command_executor_fn(dev, st)
                                answer_callback_query(cb_id, f"Switched {dev} to {st}!")
                                send_telegram_message(f"✅ Executed: Switched *{dev}* to *{st}*")
                            except Exception as e:
                                answer_callback_query(cb_id, f"Error: {e}")
                    elif data == "dismiss":
                        answer_callback_query(cb_id, "Dismissed")
                        send_telegram_message("Dismissed alert.")

                # Handle direct chat message from user
                elif "message" in update:
                    msg = update["message"]
                    sender_chat_id = str(msg["chat"]["id"])
                    text = msg.get("text", "").strip()

                    if TELEGRAM_CHAT_ID and sender_chat_id != TELEGRAM_CHAT_ID:
                        continue

                    if not text:
                        continue

                    if text == "/start":
                        send_telegram_message("👋 Hello! I am your *A-SENSE* Home Assistant Bot.\nYou can send me commands like:\n- *Turn on relay 1*\n- *What is the temperature?*\n- *Turn off all devices*")
                    else:
                        # Immediately spawn background worker so Telegram thread remains responsive
                        threading.Thread(target=_process_user_message_async, args=(text,), daemon=True).start()

        except Exception as e:
            logger.error(f"Telegram polling loop error: {e}")
            time.sleep(2)

def start_telegram_bot():
    if TELEGRAM_BOT_TOKEN:
        t = threading.Thread(target=_telegram_polling_loop, daemon=True)
        t.start()
