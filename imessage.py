import requests
import json
import uuid
import random
import os
import asyncio
from datetime import datetime
from collections import deque
from dotenv import load_dotenv

load_dotenv()
BB_URL = "http://localhost:1234"
BB_PASSWORD = os.getenv("BB_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MY_IDENTITIES = os.getenv("MY_IDENTITIES", "").split(",")
STOP_FILE = 'bot_disabled'
BLACKLIST_FILE = 'blacklist.txt'

PRIORITY_MODELS = [
    "gemini-3-flash-preview", 
    "gemini-2.5-flash",  
    "gemini-2.5-flash-lite",  
]
FALLBACK_MODEL = "gemma-3-27b-it"

SYSTEM_PROMPT_GEMMA = os.getenv("SYSTEM_PROMPT_GEMMA")
SYSTEM_PROMPT_GEMINI = os.getenv("SYSTEM_PROMPT_GEMINI")

active_conversations = {}

def is_blacklisted(sender):
    if not os.path.exists(BLACKLIST_FILE):
        return False
    try:
        with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
            blocked_list = {line.strip() for line in f if line.strip()}
        return sender in blocked_list
    except Exception as e:
        print(f"Error reading blacklist: {e}\r")
        return False

def try_model_request(model_name, model_type, message):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    
    if model_type == "gemma":
        payload = {
            "contents": [{
                "role": "user", 
                "parts": [{"text": f"{SYSTEM_PROMPT_GEMMA}MESSAGE: {message}"}]
            }]
        }
    else:
        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPT_GEMINI}]
            },
            "contents": [{
                "parts": [{"text": f"MESSAGE: {message}"}]
            }]
        }

    try:
        response = requests.post(url, json=payload, timeout=10)
        res_json = response.json()

        if 'error' in res_json:
            print(f"[{model_name}] Failed.\r")
            return None
            
        return res_json['candidates'][0]['content']['parts'][0]['text']

    except Exception as e:
        print(f"[{model_name}] Connection Error: {e}\r")
        return None

def get_ai_reply(message):
    for model_name in PRIORITY_MODELS:
        print(f"[{model_name}] Trying.\r")
        reply = try_model_request(model_name, "gemini", message)
        if reply:
            return reply

    print("Switching to Fallback...\r")
    return try_model_request(FALLBACK_MODEL, "gemma", message)

def get_chat_guid_from_message(message_guid, sender):
    url = f"{BB_URL}/api/v1/message/{message_guid}"
    params = {
        "password": BB_PASSWORD,
        "with": "chats" 
    }
    fallback_guid = f"iMessage;-;{sender}"
    
    try:
        res = requests.get(url, params=params, timeout=10)
        
        if res.status_code != 200:
            print(f"API Error ({res.status_code}): {res.text}\r")
            return fallback_guid
            
        data = res.json()
        
        if 'data' in data and 'chats' in data['data'] and len(data['data']['chats']) > 0:
            return data['data']['chats'][0]['guid']
            
        print(f"Message found, but no linked chat info: {data}\r")
        return fallback_guid

    except Exception as e:
        print(f"Message Lookup Error: {e}\r")
        return fallback_guid

def send_bb_message(chat_guid, text):
    print(f"Sending to {chat_guid}...\r")
    url = f"{BB_URL}/api/v1/message/text"
    payload = {
        "chatGuid": chat_guid,
        "message": text,
        "method": "apple-script",
        "tempGuid": f"PT-{uuid.uuid4()}"
    }
    try:
        requests.post(url, params={"password": BB_PASSWORD}, json=payload, timeout=5)
    except Exception as e:
        print(f"BB Send Error: #ignore.\r")

async def process_conversation_after_delay(sender):
    try:
        wait_time = random.randint(10, 18)
        print(f"[{sender}] Waiting {wait_time}s (Accumulating messages)...\r")
        await asyncio.sleep(wait_time)

        if sender not in active_conversations: return
        
        msgs = active_conversations[sender]['messages']
        resolved_guid = active_conversations[sender]['guid'] 
        received_ts = active_conversations[sender]['timestamp']
        
        full_text = "\n".join(msgs)
        print(f"[{sender}] Processing accumulated texts.\r")

        del active_conversations[sender]

        history_context = get_chat_history(sender, lines_to_read=20)
        prompt_payload = f"{history_context}\nNEW INCOMING MESSAGE: {full_text}"

        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(None, get_ai_reply, prompt_payload)

        if reply:
            reply_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                print("Saving history.\r")
                with open(f"{sender}.txt", "a", encoding="utf-8") as f:
                    f.write(f"[Sender - {received_ts}]: {full_text}\n[Me - {reply_timestamp}]: {reply}\n")
            except Exception as e:
                print(f"Error saving history: {e}\r")

            await loop.run_in_executor(None, send_bb_message, resolved_guid, reply)
        else:
            print(f"[{sender}] Error: No reply generated.\r")

    except asyncio.CancelledError:
        print(f"[{sender}] Timer reset (New message arrived).\r")
        raise

def get_chat_history(sender, lines_to_read=10):
    filename = f"{sender}.txt"
    if not os.path.exists(filename):
        return ""
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            last_lines = deque(f, maxlen=lines_to_read)
        
        history_block = "".join(last_lines)
        return f"\n--- RECENT CONVERSATION HISTORY---\n{history_block}\n--- END OF HISTORY ---\n"
    except Exception as e:
        print(f"Error reading history: {e}\r")
        return ""

def manage_black_list(text, resolved_chat_guid):
    try:        
        current_blacklist = []
        if os.path.exists(BLACKLIST_FILE):
            with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                current_blacklist = [line.strip() for line in f if line.strip()]

        parts = text.split(' ', 1)
        if len(parts) > 1:
            target_to_block = parts[1].strip()
            if target_to_block in current_blacklist:
                current_blacklist.remove(target_to_block)
                action = "Unblocked"
                print(f"Removed {target_to_block} from blacklist.\r")
            else:
                current_blacklist.append(target_to_block)
                action = "Blocked"
                print(f"Added {target_to_block} to blacklist.\r")

            unique_sorted_list = sorted(list(set(current_blacklist)))

            with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f:
                f.write('\n'.join(unique_sorted_list) + '\n')
            
            send_bb_message(resolved_chat_guid, f"{action}: {target_to_block}")
            print(f"{action} {target_to_block} in blacklist.\r")
        else:
            print("printing blocked list.\r")
            send_bb_message(resolved_chat_guid, f"blocklist:\n{current_blacklist}")
    except Exception as e:
        print(f"File Error: {e}\r")
        send_bb_message(resolved_chat_guid, "Error writing to blacklist file.")

def manage_bot(text, resolved_chat_guid, is_bot_disabled):
    parts = text.split(' ', 1)
    if len(parts) > 1:
        bot_action = parts[1].strip()
        if bot_action == 'on':
            if is_bot_disabled: os.remove(STOP_FILE)
            send_bb_message(resolved_chat_guid, 'Bot enabled')
            print("Bot enabled.\r")
            return
        if bot_action == 'off':
            open(STOP_FILE, 'w').close()
            send_bb_message(resolved_chat_guid, 'Bot disabled')
            print("Bot disabled.\r")
            return

    send_bb_message(resolved_chat_guid, f'Bot status: {'enabled' if is_bot_disabled else 'disabled'}')

def manage_admin(text, resolved_chat_guid, is_bot_disabled):    
    if text.lower() == '?' or text.lower() == '/help':
        help_message = (
            "Commands:\n\n"
            "/help: this message\n"
            "/block (+#): blocks, unblocks, list\n"
            "/bot (on/off): enables, disables, status\n\n"
            "* text: ai reply"
            )
        send_bb_message(resolved_chat_guid, help_message)
        return True
    if text.lower().startswith('/bot'): 
        manage_bot(text, resolved_chat_guid, is_bot_disabled)
        return True
    if text.lower().startswith('/block'):
        manage_black_list(text, resolved_chat_guid)
        return True

async def read_stream(stream):
    while True:
        line = await stream.readline()
        if not line:
            break
        yield line.decode('utf-8').strip()

async def main():
    print("Bot is listening (Async Mode - Multi-Sender Support)...\r")
    
    process = await asyncio.create_subprocess_exec(
        'script', '-q', '/dev/null', 'imsg', 'watch', '--json',
        stdout=asyncio.subprocess.PIPE
    )

    async for line in read_stream(process.stdout):
        if not line: continue
        
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if data.get('is_from_me'): continue

        sender = data.get('sender')
        text = data.get('text', '').strip()
        message_guid = data.get('guid', '') 

        resolved_chat_guid = await asyncio.to_thread(get_chat_guid_from_message, message_guid, sender)

        is_bot_disabled = os.path.exists(STOP_FILE)

        if sender in MY_IDENTITIES:
            if manage_admin(text, resolved_chat_guid, is_bot_disabled):
                continue

        if is_bot_disabled: continue

        if is_blacklisted(sender):
            print(f"[{sender}] Ignored (Blacklisted).\r")
            continue

        if resolved_chat_guid and ";+;" in resolved_chat_guid:
            print(f"Ignored group message from {sender} (Chat: {resolved_chat_guid}).\r")
            continue

        is_trigger = sender in MY_IDENTITIES and text.startswith('*')
        
        if is_trigger or (sender not in MY_IDENTITIES):
            clean_text = text[1:] if is_trigger else text
            
            arrival_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if sender in active_conversations:
                active_conversations[sender]['task'].cancel()
                active_conversations[sender]['messages'].append(clean_text)
                active_conversations[sender]['guid'] = resolved_chat_guid
            else:
                active_conversations[sender] = {
                    'messages': [clean_text],
                    'guid': resolved_chat_guid,
                    'task': None,
                    'timestamp': arrival_time
                }

            task = asyncio.create_task(process_conversation_after_delay(sender))
            active_conversations[sender]['task'] = task

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped.\r")