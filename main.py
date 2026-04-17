import requests
import time
import os
import json
import threading

TOKEN      = os.environ.get("SNIPER_TOKEN", "8640215686:AAHvQDoFMxX8KyKLuGTcAJ5D4xf0DBWFnDA")
MY_WALLET  = os.environ.get("MY_WALLET", "TXj3JCr6ZbM8Tnq8WqLubL81g4mwAw5pUr")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1003976387571"))

PACKAGES = {
    "weekly":  {"name": "Weekly",   "price": 15, "days": 7,  "label": "\U0001f4c5 Weekly \u2014 $15"},
    "monthly": {"name": "Monthly",  "price": 30, "days": 30, "label": "\U0001f4c6 Monthly \u2014 $30"},
    "3months": {"name": "3 Months", "price": 80, "days": 90, "label": "\U0001f5d3 3 Months \u2014 $80"},
}

USED_TXS_FILE      = "used_txs.json"
USED_TRIAL_FILE    = "used_trials.json"
SUBSCRIBERS_FILE   = "subscribers.json"
URL = f"https://api.telegram.org/bot{TOKEN}/"

pending_package = {}
pending_trial   = set()


def load_json_set(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_json_set(filepath, data_set):
    with open(filepath, "w") as f:
        json.dump(list(data_set), f)


def load_subscribers():
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_subscribers(subs):
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(subs, f)


USED_TXS   = load_json_set(USED_TXS_FILE)
USED_TRIAL = load_json_set(USED_TRIAL_FILE)


def check_tron_scan(tx_id, required_usd):
    try:
        api_url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={tx_id}"
        response = requests.get(api_url, timeout=15).json()
        token_info = response.get("tokenTransferInfo")
        if not token_info:
            return False
        to_address = token_info.get("to_address", "")
        amount = float(token_info.get("amount_str", "0")) / 1_000_000
        return to_address == MY_WALLET and amount >= required_usd
    except Exception as e:
        print(f"TronScan error: {e}", flush=True)
        return False


def tg_call(method, data):
    try:
        return requests.post(URL + method, json=data, timeout=15).json()
    except Exception:
        return None


def add_subscriber(user_id, days):
    subs = load_subscribers()
    expire_ts = int(time.time()) + days * 86400
    subs[str(user_id)] = expire_ts
    save_subscribers(subs)


def kick_expired_subscribers():
    while True:
        try:
            subs = load_subscribers()
            now = int(time.time())
            changed = False
            for user_id, expire_ts in list(subs.items()):
                if now >= expire_ts:
                    tg_call("banChatMember", {"chat_id": CHANNEL_ID, "user_id": int(user_id)})
                    tg_call("unbanChatMember", {"chat_id": CHANNEL_ID, "user_id": int(user_id)})
                    tg_call("sendMessage", {
                        "chat_id": int(user_id),
                        "text": (
                            "\u23f0 Your subscription has expired.\n\n"
                            "Renew anytime by sending /start"
                        )
                    })
                    del subs[user_id]
                    changed = True
                    print(f"Kicked expired user: {user_id}", flush=True)
            if changed:
                save_subscribers(subs)
        except Exception as e:
            print(f"Kick loop error: {e}", flush=True)
        time.sleep(3600)


def send_package_menu(uid):
    keyboard = {
        "inline_keyboard": [
            [{"text": "\U0001f381 Free 24h Trial", "callback_data": "trial"}],
            [{"text": PACKAGES["weekly"]["label"],  "callback_data": "pkg_weekly"}],
            [{"text": PACKAGES["monthly"]["label"], "callback_data": "pkg_monthly"}],
            [{"text": PACKAGES["3months"]["label"], "callback_data": "pkg_3months"}],
        ]
    }
    tg_call("sendMessage", {
        "chat_id": uid,
        "text": (
            "\U0001f4ca <b>Sniper Signals</b>\n\n"
            "Real-time crypto signals.\n\n"
            "Choose your subscription plan:"
        ),
        "parse_mode": "HTML",
        "reply_markup": keyboard,
    })


def send_payment_instructions(uid, pkg_key):
    pkg = PACKAGES[pkg_key]
    pending_package[uid] = pkg_key
    tg_call("sendMessage", {
        "chat_id": uid,
        "text": (
            f"\u2705 You selected: <b>{pkg['name']} \u2014 ${pkg['price']}</b>\n\n"
            f"Send exactly <b>${pkg['price']} USDT</b> (TRC20 network) to:\n\n"
            f"<code>{MY_WALLET}</code>\n\n"
            f"\u261d\ufe0f Tap the address above to copy it.\n\n"
            f"After sending, paste the <b>TXID</b> here to get instant access."
        ),
        "parse_mode": "HTML",
    })


def request_phone(uid):
    pending_trial.add(uid)
    keyboard = {
        "keyboard": [[{
            "text": "\U0001f4f2 Share my phone number",
            "request_contact": True
        }]],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }
    tg_call("sendMessage", {
        "chat_id": uid,
        "text": (
            "\U0001f381 <b>Free 24h Trial</b>\n\n"
            "To activate your trial, please share your phone number.\n"
            "This is to prevent abuse (one trial per person)."
        ),
        "parse_mode": "HTML",
        "reply_markup": keyboard,
    })


def give_trial(uid, phone):
    if phone in USED_TRIAL:
        tg_call("sendMessage", {
            "chat_id": uid,
            "text": "\u274c This phone number has already used the free trial.",
            "reply_markup": {"remove_keyboard": True}
        })
        return

    link_res = tg_call("createChatInviteLink", {
        "chat_id": CHANNEL_ID,
        "member_limit": 1,
        "expire_date": int(time.time()) + 86400
    })
    invite_link = link_res.get("result", {}).get("invite_link") if link_res else None

    if invite_link:
        USED_TRIAL.add(phone)
        save_json_set(USED_TRIAL_FILE, USED_TRIAL)
        add_subscriber(uid, 1)
        tg_call("sendMessage", {
            "chat_id": uid,
            "text": (
                "\U0001f381 <b>Your free 24h trial is ready!</b>\n\n"
                f"{invite_link}\n\n"
                "\u23f0 Expires in 24 hours."
            ),
            "parse_mode": "HTML",
            "reply_markup": {"remove_keyboard": True}
        })
    else:
        tg_call("sendMessage", {
            "chat_id": uid,
            "text": "\u26a0\ufe0f Could not generate trial link. Please try again later.",
            "reply_markup": {"remove_keyboard": True}
        })


def main():
    print("Bot is Live...", flush=True)
    threading.Thread(target=kick_expired_subscribers, daemon=True).start()

    offset = 0
    while True:
        updates = tg_call("getUpdates", {"offset": offset, "timeout": 20})
        if not updates or "result" not in updates:
            time.sleep(1)
            continue

        for update in updates["result"]:
            offset = update["update_id"] + 1

            if "callback_query" in update:
                cq   = update["callback_query"]
                uid  = cq["message"]["chat"]["id"]
                data = cq.get("data", "")
                tg_call("answerCallbackQuery", {"callback_query_id": cq["id"]})
                if data == "trial":
                    request_phone(uid)
                elif data.startswith("pkg_"):
                    pkg_key = data[4:]
                    if pkg_key in PACKAGES:
                        send_payment_instructions(uid, pkg_key)
                continue

            if "message" not in update:
                continue

            msg  = update["message"]
            uid  = msg["chat"]["id"]

            if "contact" in msg and uid in pending_trial:
                phone = msg["contact"].get("phone_number", "")
                pending_trial.discard(uid)
                give_trial(uid, phone)
                continue

            text = msg.get("text", "").strip()

            if text == "/start":
                send_package_menu(uid)

            elif len(text) == 64:
                pkg_key = pending_package.get(uid)
                if not pkg_key:
                    tg_call("sendMessage", {"chat_id": uid, "text": "Please choose a plan first by sending /start"})
                    continue

                if text in USED_TXS:
                    tg_call("sendMessage", {"chat_id": uid, "text": "\u274c This TXID has already been used."})
                else:
                    tg_call("sendMessage", {"chat_id": uid, "text": "\U0001f50d Checking transaction on blockchain..."})
                    if check_tron_scan(text, PACKAGES[pkg_key]["price"]):
                        link_res = tg_call("createChatInviteLink", {"chat_id": CHANNEL_ID, "member_limit": 1})
                        invite_link = link_res.get("result", {}).get("invite_link") if link_res else None
                        if invite_link:
                            USED_TXS.add(text)
                            save_json_set(USED_TXS_FILE, USED_TXS)
                            add_subscriber(uid, PACKAGES[pkg_key]["days"])
                            pending_package.pop(uid, None)
                            tg_call("sendMessage", {
                                "chat_id": uid,
                                "text": f"\u2705 Payment confirmed!\n\nHere is your channel link (single use):\n{invite_link}"
                            })
                        else:
                            tg_call("sendMessage", {"chat_id": uid, "text": "\u26a0\ufe0f Payment confirmed but failed to generate link. Please contact support."})
                    else:
                        tg_call("sendMessage", {
                            "chat_id": uid,
                            "text": (
                                "\u274c Payment not found.\n\n"
                                "Make sure you:\n"
                                "\u2022 Sent the correct amount\n"
                                "\u2022 To the correct wallet\n"
                                "\u2022 On TRC20 network"
                            )
                        })

        time.sleep(1)


if __name__ == "__main__":
    main()
