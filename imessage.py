import json
import uuid
import random
import os
import asyncio
import aiohttp
from datetime import datetime
from collections import deque
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()
BB_URL = "http://localhost:1234"
BB_PASSWORD = os.getenv("BB_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MY_IDENTITIES = set(filter(None, os.getenv("MY_IDENTITIES", "").split(",")))
STOP_FILE = Path('bot_disabled')
BLACKLIST_FILE = Path('blacklist.txt')
HISTORY_DIR = Path("history")
HISTORY_DIR.mkdir(exist_ok=True)

SYSTEM_PROMPT_GEMMA = os.getenv("SYSTEM_PROMPT_GEMMA")
SYSTEM_PROMPT_GEMINI = os.getenv("SYSTEM_PROMPT_GEMINI")

PRIORITY_MODELS = [
    "gemini-3-flash-preview", 
    "gemini-2.5-flash",  
    "gemini-2.5-flash-lite",  
]
FALLBACK_MODEL = "gemma-3-27b-it"

class BlueBubblesBot:
    def __init__(self):
        self.active_conversations = {}
        self.blacklist = self._load_blacklist()
        self.session = None

    async def start(self):
        print("Bot starting up...\r")
        self.session = aiohttp.ClientSession()
        try:
            await self.main_loop()
        finally:
            await self.session.close()
            print("Bot shutdown.\r")

    def _load_blacklist(self):
        if not BLACKLIST_FILE.exists():
            return set()
        try:
            content = BLACKLIST_FILE.read_text(encoding='utf-8')
            return {line.strip() for line in content.splitlines() if line.strip()}
        except Exception as e:
            print(f"Error loading blacklist: {e}\r")
            return set()

    def _save_blacklist(self):
        try:
            sorted_list = sorted(list(self.blacklist))
            BLACKLIST_FILE.write_text('\n'.join(sorted_list) + '\n', encoding='utf-8')
        except Exception as e:
            print(f"Error saving blacklist: {e}\r")

    async def call_gemini(self, model_name, system_prompt, user_message):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        
        if model_name == FALLBACK_MODEL:
            payload = { 
                "contents": [{ "role": "user", "parts": [{"text": f"{SYSTEM_PROMPT_GEMMA}{user_message}"}] }]
            }
        else:
            payload = {
                "system_instruction": { "parts": [{"text": SYSTEM_PROMPT_GEMINI}] }, "contents": [{ "parts": [{"text": user_message}] }]
            }

        try:
            async with self.session.post(url, json=payload, timeout=15) as response:
                if response.status != 200:
                    print(f"[{model_name}] Failed.\r")
                    return None
                
                res_json = await response.json()
                return res_json['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            print(f"[{model_name}] Request Failed: {e}\r")
            return None

    async def get_ai_reply(self, message):
        for model in PRIORITY_MODELS:
            print(f"[{model}] Generating...\r")
            reply = await self.call_gemini(model, SYSTEM_PROMPT_GEMINI, message)
            if reply: return reply
        
        print(f"[{FALLBACK_MODEL}] Switching to fallback...\r")
        return await self.call_gemini(FALLBACK_MODEL, SYSTEM_PROMPT_GEMMA, message)

    async def get_chat_guid(self, message_guid, sender):
        url = f"{BB_URL}/api/v1/message/{message_guid}"
        params = {"password": BB_PASSWORD, "with": "chats"}
        fallback = f"any;-;{sender}"

        try:
            async with self.session.get(url, params=params, timeout=10) as res:
                if res.status != 200: return fallback
                data = await res.json()
                
                if data.get('data') and data['data'].get('chats'):
                    return data['data']['chats'][0]['guid']
                return fallback
        except Exception:
            print(f"get_chat_guid: {e}\r")
            return fallback

    async def send_message(self, chat_guid, text):
        if not text: return
        print(f"Sending reply to {chat_guid}...\r")
        url = f"{BB_URL}/api/v1/message/text"
        payload = {
            "chatGuid": chat_guid,
            "message": text,
            "method": "apple-script", 
            "tempGuid": f"PT-{uuid.uuid4()}"
        }
        try:
            async with self.session.post(url, params={"password": BB_PASSWORD}, json=payload, timeout=5) as res:
                pass
        except Exception as e:
            print(f"Send Error: {e}\r")

    def get_history(self, sender, lines=15):
        path = HISTORY_DIR / f"{sender}.txt"
        if not path.exists(): return ""
        try:
            with path.open('r', encoding='utf-8') as f:
                last_lines = deque(f, maxlen=lines)
            return "\n--- HISTORY ---\n" + "".join(last_lines) + "\n--- END HISTORY ---\n"
        except Exception:
            return ""

    def append_history(self, sender, incoming, outgoing, timestamp):
        path = HISTORY_DIR / f"{sender}.txt"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with path.open('a', encoding='utf-8') as f:
                f.write(f"[Sender - {timestamp}]: {incoming}\n[Bot - {now}]: {outgoing}\n")
        except Exception as e:
            print(f"History write error: {e}\r")

    async def handle_admin(self, text, chat_guid):
        if text == '/help' or text == '?':
            help_message = (
                "Commands:\n\n"
                "/help: this message\n"
                "/block (+#): blocks, unblocks, list\n"
                "/bot (on/off): enables, disables, status\n\n"
                "* text: ai reply"
                )
            await self.send_message(chat_guid, help_message)
            return True
        
        parts = text.split(' ', 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == '/bot':
            if arg == 'on':
                if STOP_FILE.exists(): STOP_FILE.unlink()
                await self.send_message(chat_guid, "Bot Enabled")
            elif arg == 'off':
                STOP_FILE.touch()
                await self.send_message(chat_guid, "Bot Disabled")
            else:
                status = "Disabled" if STOP_FILE.exists() else "Enabled"
                await self.send_message(chat_guid, f"Status: {status}")
            return True

        if cmd == '/block':
            if not arg:
                await self.send_message(chat_guid, f"Blocked: {list(self.blacklist)}")
                return True
            
            if arg in self.blacklist:
                self.blacklist.remove(arg)
                await self.send_message(chat_guid, f"Unblocked {arg}")
            else:
                self.blacklist.add(arg)
                await self.send_message(chat_guid, f"Blocked {arg}")
            self._save_blacklist()
            return True
            
        return False

    async def process_queue(self, sender):
        try:
            wait_time = random.randint(10, 18)
            print(f"[{sender}] Waiting {wait_time}s (Accumulating messages)...\r")
            await asyncio.sleep(wait_time)

            if sender not in self.active_conversations: return

            data = self.active_conversations.pop(sender)
            full_text = "\n".join(data['messages'])
            chat_guid = data['guid']
            
            print(f"[{sender}] Processing accumulated message...\r")

            history = await asyncio.to_thread(self.get_history, sender)
            prompt = f"{history}\nNEW INCOMING MESSAGE: {full_text}"

            reply = await self.get_ai_reply(prompt)

            if reply:
                await self.send_message(chat_guid, reply)
                await asyncio.to_thread(self.append_history, sender, full_text, reply, data['timestamp'])
            else:
                print(f"[{sender}] No reply generated.\r")

        except asyncio.CancelledError:
            print(f"[{sender}] Timer reset (New message arrived).\r")
            raise 

    async def main_loop(self):
        print("Bot is listening...\r")
        
        process = await asyncio.create_subprocess_exec(
            'script', '-q', '/dev/null', 'imsg', 'watch', '--json',
            stdout=asyncio.subprocess.PIPE
        )

        async for line in process.stdout:
            if not line: continue
            try:
                raw_line = line.decode('utf-8').strip()
                data = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            if data.get('is_from_me'): continue

            sender = data.get('sender')
            text = data.get('text', '').strip()
            msg_guid = data.get('guid')

            if STOP_FILE.exists() and sender not in MY_IDENTITIES: continue

            resolved_guid = await self.get_chat_guid(msg_guid, sender)

            if sender in MY_IDENTITIES:
                if await self.handle_admin(text, resolved_guid):
                    continue

            if sender in self.blacklist:
                print(f"Ignored blacklisted: {sender}\r")
                continue

            if ";+;" in resolved_guid:
                print(f"Ignored group chat: {resolved_guid}\r")
                continue

            is_trigger = sender in MY_IDENTITIES and text.startswith('*')
            if sender not in MY_IDENTITIES or is_trigger:
                clean_text = text[1:] if is_trigger else text
                
                if sender in self.active_conversations:
                    self.active_conversations[sender]['task'].cancel()
                    self.active_conversations[sender]['messages'].append(clean_text)
                    self.active_conversations[sender]['guid'] = resolved_guid 
                else:
                    self.active_conversations[sender] = {
                        'messages': [clean_text],
                        'guid': resolved_guid,
                        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'task': None
                    }
                
                task = asyncio.create_task(self.process_queue(sender))
                self.active_conversations[sender]['task'] = task

if __name__ == "__main__":
    bot = BlueBubblesBot()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        pass