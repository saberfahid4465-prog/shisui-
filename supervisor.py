import os
import yaml
import requests
import json
import datetime
import time
from typing import List, Dict, Any, Optional
from openai import OpenAI

# Initialize OpenAI client for Gemini
client = OpenAI()

class SupervisorBot:
    def __init__(self, config_path: str = "apps.yaml"):
        self.config_path = config_path
        self.config = self.load_config()
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.last_update_id = 0
        
        # System prompt for the AI Assistant
        self.system_prompt = """
        You are 'Shisui', a smart Supervisor Bot and AI Assistant. 
        Your job is to help the user monitor their GitHub bots and answer any questions.
        You can:
        1. Monitor bots: Check status of GitHub workflows.
        2. Add bots: If a user provides a GitHub URL, account, and channel, help them add it.
        3. Chat: Be friendly, helpful, and smart.
        
        Current bots being monitored:
        """ + json.dumps(self.config.get('bots', []), indent=2)

    def load_config(self) -> Dict:
        if not os.path.exists(self.config_path):
            return {"bots": []}
        with open(self.config_path, "r") as f:
            return yaml.safe_load(f) or {"bots": []}

    def save_config(self):
        with open(self.config_path, "w") as f:
            yaml.dump(self.config, f)

    def get_github_pat(self, account_key: str) -> Optional[str]:
        env_var = f"PAT_{account_key.upper()}"
        return os.getenv(env_var)

    def fetch_latest_workflow_run(self, repo_url: str, pat: str) -> Optional[Dict]:
        try:
            parts = repo_url.rstrip("/").split("/")
            owner_repo = f"{parts[-2]}/{parts[-1]}"
            api_url = f"https://api.github.com/repos/{owner_repo}/actions/runs?per_page=1"
            headers = {"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"}
            response = requests.get(api_url, headers=headers, timeout=10)
            if response.status_code == 200:
                runs = response.json().get("workflow_runs", [])
                return runs[0] if runs else None
        except Exception as e:
            print(f"Error fetching workflow: {e}")
        return None

    def ai_chat(self, user_message: str) -> str:
        """
        Handles general chat and intent recognition using Gemini.
        """
        try:
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_message}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"I'm having a bit of trouble thinking right now. Error: {e}"

    def send_telegram_message(self, text: str, chat_id: str = None):
        target_id = chat_id or self.telegram_chat_id
        if not self.telegram_token or not target_id:
            return
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        requests.post(url, json={"chat_id": target_id, "text": text}, timeout=10)

    def process_updates(self):
        """
        Polls for new messages and responds.
        """
        if not self.telegram_token: return
        
        url = f"https://api.telegram.org/bot{self.telegram_token}/getUpdates"
        params = {"offset": self.last_update_id + 1, "timeout": 30}
        
        try:
            resp = requests.get(url, params=params, timeout=35).json()
            if not resp.get("ok"): return
            
            for update in resp.get("result", []):
                self.last_update_id = update["update_id"]
                message = update.get("message", {})
                text = message.get("text", "")
                chat_id = message.get("chat", {}).get("id")
                
                if not text: continue
                
                print(f"Received message: {text} from {chat_id}")
                
                # Handle specific commands or use AI for general chat
                if text.lower() == "/status":
                    self.run_monitoring(chat_id)
                elif text.startswith("Add new Telegram bot:"):
                    self.handle_add_bot(text, chat_id)
                else:
                    # Use AI for smart response
                    ai_response = self.ai_chat(text)
                    self.send_telegram_message(ai_response, chat_id)
                    
        except Exception as e:
            print(f"Polling error: {e}")

    def handle_add_bot(self, text: str, chat_id: str):
        try:
            data = text.split(":")[1].strip().split(",")
            repo_url = data[0].strip()
            acc = data[1].strip()
            channel = data[2].strip()
            new_bot = {"name": repo_url.split("/")[-1], "repo_url": repo_url, "account": acc, "channel": channel, "type": "telegram"}
            if not any(b['repo_url'] == repo_url for b in self.config['bots']):
                self.config['bots'].append(new_bot)
                self.save_config()
                self.send_telegram_message("✅ I've added the new bot to my monitoring list!", chat_id)
            else:
                self.send_telegram_message("ℹ️ That bot is already in my list.", chat_id)
        except Exception as e:
            self.send_telegram_message(f"❌ I couldn't parse that. Please use: Add new Telegram bot: URL, Account, Channel", chat_id)

    def run_monitoring(self, chat_id: str = None):
        today = datetime.datetime.now().strftime("%d %b %Y")
        report = [f"📊 Daily Supervisor Report – {today}"]
        
        all_healthy = True
        for bot in self.config.get("bots", []):
            name, repo, acc, channel = bot.get("name"), bot.get("repo_url"), bot.get("account"), bot.get("channel")
            pat = self.get_github_pat(acc)
            
            if not pat:
                report.append(f"🔴 {name} ({channel})\n   ❌ Status: Missing PAT")
                all_healthy = False
                continue
            
            run = self.fetch_latest_workflow_run(repo, pat)
            if not run or run.get("conclusion") == "success":
                report.append(f"🟢 {name} ({channel})\n   ✔ Status: Success")
            else:
                report.append(f"🔴 {name} ({channel})\n   ❌ Status: Failed ({run.get('display_title', 'Error')})")
                all_healthy = False

        report.append(f"\nSystem Status: {'HEALTHY ✅' if all_healthy else 'ATTENTION REQUIRED ⚠️'}")
        self.send_telegram_message("\n".join(report), chat_id)

if __name__ == "__main__":
    bot = SupervisorBot()
    print("Shisui Smart Assistant is starting...")
    
    # If run via schedule, just do monitoring
    if os.getenv("GITHUB_EVENT_NAME") == "schedule" or os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        bot.run_monitoring()
    else:
        # Otherwise, run in polling mode for a limited time (to fit in GH Action)
        # In a real server, this would be a while True loop.
        # For GH Actions, we'll poll for 5 minutes then exit.
        start_time = time.time()
        while time.time() - start_time < 300: # 5 minutes
            bot.process_updates()
            time.sleep(2)
