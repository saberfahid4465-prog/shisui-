import os
import yaml
import requests
import json
import datetime
import time
import uuid
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
        print(f"DEBUG: Initialized. Token set: {bool(self.telegram_token)}, Chat ID: {self.telegram_chat_id}")
        
    def load_config(self) -> Dict:
        if not os.path.exists(self.config_path):
            print(f"DEBUG: Config file {self.config_path} not found.")
            return {"bots": []}
        with open(self.config_path, "r") as f:
            config = yaml.safe_load(f) or {"bots": []}
            print(f"DEBUG: Loaded {len(config.get('bots', []))} bots.")
            return config

    def save_config(self):
        with open(self.config_path, "w") as f:
            yaml.dump(self.config, f)

    def get_github_pat(self, account_key: str) -> Optional[str]:
        env_var = f"PAT_{account_key.upper()}"
        pat = os.getenv(env_var)
        print(f"DEBUG: PAT for {account_key} ({env_var}): {'Found' if pat else 'Not Found'}")
        return pat

    def fetch_latest_workflow_run(self, repo_url: str, pat: str) -> Optional[Dict]:
        try:
            repo_path = repo_url.replace("https://github.com/", "").replace(".git", "").strip("/")
            api_url = f"https://api.github.com/repos/{repo_path}/actions/runs?per_page=1"
            headers = {
                "Authorization": f"token {pat}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Supervisor-Bot"
            }
            print(f"DEBUG: Fetching workflow for {repo_path}...")
            response = requests.get(f"{api_url}&nocache={time.time()}", headers=headers, timeout=15)
            if response.status_code == 200:
                runs = response.json().get("workflow_runs", [])
                print(f"DEBUG: Found {len(runs)} runs for {repo_path}.")
                return runs[0] if runs else None
            else:
                print(f"DEBUG: GitHub API Error for {repo_path}: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"DEBUG: Exception fetching workflow for {repo_url}: {e}")
        return None

    def send_telegram_message(self, text: str, chat_id: str = None):
        target_id = chat_id or self.telegram_chat_id
        if not self.telegram_token or not target_id:
            print(f"DEBUG: Telegram not configured. Token: {bool(self.telegram_token)}, Target ID: {target_id}")
            return
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        print(f"DEBUG: Sending Telegram message to {target_id}...")
        try:
            resp = requests.post(url, json={"chat_id": target_id, "text": text}, timeout=15)
            print(f"DEBUG: Telegram response: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"DEBUG: Exception sending Telegram message: {e}")

    def run_monitoring(self, chat_id: str = None):
        print("DEBUG: Starting monitoring run...")
        now = datetime.datetime.now()
        current_date_str = now.strftime("%d %b %Y")
        current_time_str = now.strftime("%H:%M:%S")
        unique_run_id = str(uuid.uuid4())[:8]
        
        report = [f"📊 Daily Supervisor Report – {current_date_str}"]
        
        manual_actions = 0
        auto_fixes = 0
        
        for bot in self.config.get("bots", []):
            name = bot.get("name")
            repo = bot.get("repo_url")
            acc = bot.get("account")
            channel = bot.get("channel")
            
            pat = self.get_github_pat(acc)
            if not pat:
                report.append(f"🔴 {name} ({channel})\n   ❌ Status: Failed\n   ⚠ Error: Missing PAT")
                manual_actions += 1
                continue
            
            run = self.fetch_latest_workflow_run(repo, pat)
            if not run:
                report.append(f"🟢 {name} ({channel})\n   ✔ Status: Success\n   ℹ Notes: No recent runs")
                continue
            
            status = run.get("conclusion")
            if status == "success":
                report.append(f"🟢 {name} ({channel})\n   ✔ Status: Success\n   ℹ Notes: Ran normally")
            else:
                error_msg = run.get("display_title", "Error")
                report.append(f"🔴 {name} ({channel})\n   ❌ Status: Failed\n   ⚠ Error: {error_msg}")
                manual_actions += 1

        report.append(f"\n🚨 Manual Action Required: {manual_actions}")
        report.append(f"⚙ Auto-Fixes Applied Today: {auto_fixes}")
        report.append(f"System Status: {'HEALTHY ✅' if manual_actions == 0 else 'ATTENTION ⚠️'}")
        report.append(f"\n🕒 Last Updated: {current_time_str} UTC")
        report.append(f"🆔 Run ID: {unique_run_id}")
        
        self.send_telegram_message("\n".join(report), chat_id)

    def ai_chat(self, user_message: str) -> str:
        try:
            now = datetime.datetime.now()
            sys_prompt = f"You are Shisui, a smart assistant. Today is {now.strftime('%d %b %Y %H:%M:%S')}. Help the user with their bots: {json.dumps(self.config.get('bots', []))}"
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_message}]
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error: {e}"

    def process_updates(self):
        if not self.telegram_token: return
        url = f"https://api.telegram.org/bot{self.telegram_token}/getUpdates"
        params = {"offset": self.last_update_id + 1, "timeout": 20}
        try:
            resp = requests.get(url, params=params, timeout=25).json()
            if not resp.get("ok"): 
                print(f"DEBUG: Telegram getUpdates error: {resp}")
                return
            for update in resp.get("result", []):
                self.last_update_id = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "")
                cid = msg.get("chat", {}).get("id")
                print(f"DEBUG: Received message: '{text}' from {cid}")
                if text.lower() == "/status":
                    self.run_monitoring(cid)
                elif text:
                    self.send_telegram_message(self.ai_chat(text), cid)
        except Exception as e: 
            print(f"DEBUG: Exception in process_updates: {e}")

if __name__ == "__main__":
    bot = SupervisorBot()
    event = os.getenv("GITHUB_EVENT_NAME")
    print(f"DEBUG: Event name: {event}")
    if event in ["schedule", "workflow_dispatch"]:
        bot.run_monitoring()
    else:
        # Polling mode for 10 minutes
        print("DEBUG: Entering polling mode...")
        start = time.time()
        while time.time() - start < 600:
            bot.process_updates()
            time.sleep(5)
