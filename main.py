import requests
import time
import os
import json

TOKEN        = os.environ.get("SNIPER_TOKEN", "8640215686:AAHvQDoFMxX8KyKLuGTcAJ5D4xf0DBWFnDA")
MY_WALLET    = os.environ.get("MY_WALLET", "TXj3JCr6ZbM8Tnq8WqLubL81g4mwAw5pUr")
CHANNEL_ID   = int(os.environ.get("CHANNEL_ID", "-1003976387571"))
REQUIRED_USD = float(os.environ.get("REQUIRED_USD", "25"))

USED_TXS_FILE = "used_txs.json"
URL = f"https://api.telegram.org/bot{TOKEN}/"


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


def check_tron_scan(tx_id):
    try:
        api_url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={tx_id}"
        response = requests.get(api_url, timeout=15).json()

        token_info = response.get("tokenTransferInfo")
        if not token_info:
            return False

        to_address = token_info.get("to_address", "")
        amount_str = token_info.get("amount_str", "0")
        amount = float(amount_str) / 1_000_000

        if to_address == MY_WALLET and amount >= REQUIRED_USD:
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


def main():
    print("Bot is Live...", flush=True)
    offset = 0
    while True:
        updates = tg_call("getUpdates", {"offset": offset, "timeout": 20})
        if updates and "result" in updates:
            for update in updates["result"]:
                offset = update["update_id"] + 1
                if "message" not in update:
                    continue

                msg  = update["message"]
                uid  = msg["chat"]["id"]
                text = msg.get("text", "").strip()

                if text == "/start":
                    tg_call("sendMessage", {
                        "chat_id": uid,
                        "text": (
                            f"\u0623\u0647\u0644\u0627\u064b \u0628\u0643! \U0001f44b\n\n"
                            f"\u0644\u0644\u0627\u0634\u062a\u0631\u0627\u0643 \u0641\u064a \u0642\u0646\u0627\u0629 \u0627\u0644\u0625\u0634\u0627\u0631\u0627\u062a\u060c \u0623\u0631\u0633\u0644 *{int(REQUIRED_USD)}$ USDT* (\u0634\u0628\u0643\u0629 TRC20) \u0644\u0644\u0645\u062d\u0641\u0638\u0629:\n"
                            f"`{MY_WALLET}`\n\n"
                            f"\u0628\u0639\u062f \u0627\u0644\u0625\u0631\u0633\u0627\u0644\u060c \u0623\u0631\u0633\u0644 *TXID* \u0627\u0644\u0639\u0645\u0644\u064a\u0629 \u0647\u0646\u0627 \u0648\u0633\u064a\u062a\u0645 \u0627\u0644\u062a\u062d\u0642\u0642 \u062a\u0644\u0642\u0627\u0626\u064a\u0627\u064b \u2705"
                        ),
                        "parse_mode": "Markdown"
                    })

                elif len(text) == 64:
                    if text in USED_TXS:
                        tg_call("sendMessage", {"chat_id": uid, "text": "\u274c \u0647\u0630\u0627 \u0627\u0644\u0631\u0645\u0632 \u062a\u0645 \u0627\u0633\u062a\u062e\u062f\u0627\u0645\u0647 \u0645\u0633\u0628\u0642\u0627\u064b!"})
                    else:
                        tg_call("sendMessage", {"chat_id": uid, "text": "\U0001f50d \u062c\u0627\u0631\u064a \u0641\u062d\u0635 \u0627\u0644\u0639\u0645\u0644\u064a\u0629 \u0639\u0644\u0649 \u0627\u0644\u0628\u0644\u0648\u0643\u0634\u064a\u0646... \u0627\u0646\u062a\u0638\u0631 \u0644\u062d\u0638\u0629."})
                        if check_tron_scan(text):
                            link_res = tg_call("createChatInviteLink", {"chat_id": CHANNEL_ID, "member_limit": 1})
                            invite_link = link_res.get("result", {}).get("invite_link") if link_res else None

                            if invite_link:
                                save_used_tx(text)
                                USED_TXS.add(text)
                                tg_call("sendMessage", {
                                    "chat_id": uid,
                                    "text": f"\u2705 \u062a\u0645 \u0627\u0644\u062a\u0623\u0643\u062f \u0645\u0646 \u0627\u0644\u062f\u0641\u0639!\n\n\u0631\u0627\u0628\u0637 \u0627\u0644\u0642\u0646\u0627\u0629 (\u0635\u0627\u0644\u062d \u0644\u0634\u062e\u0635 \u0648\u0627\u062d\u062f \u0641\u0642\u0637):\n{invite_link}"
                                })
                            else:
                                tg_call("sendMessage", {"chat_id": uid, "text": "\u26a0\ufe0f \u062a\u0645 \u0627\u0644\u062a\u0623\u0643\u062f \u0645\u0646 \u0627\u0644\u062f\u0641\u0639 \u0644\u0643\u0646 \u062d\u0635\u0644 \u062e\u0637\u0623 \u0641\u064a \u0625\u0646\u0634\u0627\u0621 \u0627\u0644\u0631\u0627\u0628\u0637. \u062a\u0648\u0627\u0635\u0644 \u0645\u0639 \u0627\u0644\u062f\u0639\u0645."})
                        else:
                            tg_call("sendMessage", {"chat_id": uid, "text": "\u274c \u0644\u0645 \u0646\u062c\u062f \u0639\u0645\u0644\u064a\u0629 \u062f\u0641\u0639 \u0645\u0637\u0627\u0628\u0642\u0629.\n\n\u062a\u0623\u0643\u062f \u0645\u0646:\n- \u0625\u0631\u0633\u0627\u0644 \u0627\u0644\u0645\u0628\u0644\u063a \u0627\u0644\u0635\u062d\u064a\u062d\n- \u0644\u0644\u0645\u062d\u0641\u0638\u0629 \u0627\u0644\u0635\u062d\u064a\u062d\u0629\n- \u0639\u0644\u0649 \u0634\u0628\u0643\u0629 TRC20"})

        time.sleep(1)


if __name__ == "__main__":
    main()
