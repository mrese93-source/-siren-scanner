import requests
import time
import os
import json

TOKEN      = os.environ.get("SNIPER_TOKEN", "8640215686:AAHvQDoFMxX8KyKLuGTcAJ5D4xf0DBWFnDA")
MY_WALLET  = os.environ.get("MY_WALLET", "TXj3JCr6ZbM8Tnq8WqLubL81g4mwAw5pUr")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1003976387571"))

PACKAGES = {
    "weekly":  {"name": "Weekly",   "price": 15,  "label": "\U0001f4c5 Weekly \u2014 $15"},
    "monthly": {"name": "Monthly",  "price": 40,  "label": "\U0001f4c6 Monthly \u2014 $40"},
    "3months": {"name": "3 Months", "price": 99,  "label": "\U0001f5d3 3 Months \u2014 $99"},
}

USED_TXS_FILE = "used_txs.json"
URL = f"https://api.telegram.org/bot{TOKEN}/"

pending_package = {}


def load_used_txs():
    if os.path.exists(USED_TXS_FILE):
        try:
            with open(USED_TXS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_used_tx(tx_id):
    txs = load_used_txs()
    txs.add(tx_id)
    with open(USED_TXS_FILE, "w") as f:
        json.dump(list(txs), f)


USED_TXS = load_used_txs()


def check_tron_scan(tx_id, required_usd):
    try:
        api_url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={tx_id}"
        response = requests.get(api_url, timeout=15).json()
        token_info = response.get("tokenTransferInfo")
        if not token_info:
            return False
        to_address = token_info.get("to_address", "")
        amount = float(token_info.get("amount_str", "0")) / 1_000_000
        if to_address == MY_WALLET and amount >= required_usd:
            return True
        return False
    except Exception as e:
        print(f"TronScan error: {e}", flush=True)
        return False


def tg_call(method, data):
    try:
        return requests.post(URL + method, json=data, timeout=15).json()
    except Exception:
        return None


def send_package_menu(uid):
    keyboard = {
        "inline_keyboard": [
            [{"text": PACKAGES["weekly"]["label"],  "callback_data": "pkg_weekly"}],
            [{"text": PACKAGES["monthly"]["label"], "callback_data": "pkg_monthly"}],
            [{"text": PACKAGES["3months"]["label"], "callback_data": "pkg_3months"}],
        ]
    }
    tg_call("sendMessage", {
        "chat_id": uid,
        "text": (
            "\U0001f4ca <b>Sniper Signals</b>\n\n"
            "Real-time crypto signals for Bybit futures.\n\n"
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


def main():
    print("Bot is Live...", flush=True)
    offset = 0
    while True:
        updates = tg_call("getUpdates", {"offset": offset, "timeout": 20})
        if not updates or "result" not in updates:
            time.sleep(1)
            continue

        for update in updates["result"]:
            offset = update["update_id"] + 1

            # --- Callback (button press) ---
            if "callback_query" in update:
                cq   = update["callback_query"]
                uid  = cq["message"]["chat"]["id"]
                data = cq.get("data", "")
                tg_call("answerCallbackQuery", {"callback_query_id": cq["id"]})

                if data.startswith("pkg_"):
                    pkg_key = data[4:]
                    if pkg_key in PACKAGES:
                        send_payment_instructions(uid, pkg_key)
                continue

            # --- Message ---
            if "message" not in update:
                continue

            msg  = update["message"]
            uid  = msg["chat"]["id"]
            text = msg.get("text", "").strip()

            if text == "/start":
                send_package_menu(uid)

            elif len(text) == 64:
                pkg_key = pending_package.get(uid)
                if not pkg_key:
                    tg_call("sendMessage", {
                        "chat_id": uid,
                        "text": "Please choose a plan first by sending /start"
                    })
                    continue

                if text in USED_TXS:
                    tg_call("sendMessage", {"chat_id": uid, "text": "\u274c This TXID has already been used."})
                else:
                    tg_call("sendMessage", {"chat_id": uid, "text": "\U0001f50d Checking transaction on blockchain..."})
                    required = PACKAGES[pkg_key]["price"]
                    if check_tron_scan(text, required):
                        link_res = tg_call("createChatInviteLink", {"chat_id": CHANNEL_ID, "member_limit": 1})
                        invite_link = link_res.get("result", {}).get("invite_link") if link_res else None
                        if invite_link:
                            save_used_tx(text)
                            USED_TXS.add(text)
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
