import os
import yaml
import requests
import json
import datetime
from typing import List, Dict, Any, Optional
from openai import OpenAI

# Initialize OpenAI client for Gemini (using the pre-configured environment)
client = OpenAI()

class SupervisorBot:
    def __init__(self, config_path: str = "apps.yaml"):
        self.config_path = config_path
        self.config = self.load_config()
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.report_lines = []
        print(f"DEBUG: Initialized with Token: {'Set' if self.telegram_token else 'Not Set'}, Chat ID: {self.telegram_chat_id}")

    def load_config(self) -> Dict:
        if not os.path.exists(self.config_path):
            print(f"DEBUG: Config file {self.config_path} not found.")
            return {"bots": []}
        with open(self.config_path, "r") as f:
            config = yaml.safe_load(f) or {"bots": []}
            print(f"DEBUG: Loaded {len(config.get('bots', []))} bots from config.")
            return config

    def save_config(self):
        with open(self.config_path, "w") as f:
            yaml.dump(self.config, f)

    def get_github_pat(self, account_key: str) -> Optional[str]:
        env_var = f"PAT_{account_key.upper()}"
        pat = os.getenv(env_var)
        print(f"DEBUG: Fetching PAT for {account_key} from {env_var}: {'Found' if pat else 'Not Found'}")
        return pat

    def fetch_latest_workflow_run(self, repo_url: str, pat: str) -> Optional[Dict]:
        parts = repo_url.rstrip("/").split("/")
        owner_repo = f"{parts[-2]}/{parts[-1]}"
        api_url = f"https://api.github.com/repos/{owner_repo}/actions/runs?per_page=1"
        headers = {"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"}
        
        try:
            response = requests.get(api_url, headers=headers)
            if response.status_code == 200:
                runs = response.json().get("workflow_runs", [])
                return runs[0] if runs else None
            else:
                print(f"DEBUG: GitHub API error for {owner_repo}: {response.status_code}")
        except Exception as e:
            print(f"DEBUG: Error fetching workflow for {owner_repo}: {e}")
        return None

    def analyze_with_gemini(self, bot_name: str, error_context: str) -> Optional[str]:
        prompt = f"Analyze failure for {bot_name}: {error_context}. Return one of: retry_workflow, reinstall_dependencies, clear_cache, delay_quota_reset, restart_workflow, or none."
        try:
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}]
            )
            fix = response.choices[0].message.content.strip().lower()
            return fix if fix in ["retry_workflow", "reinstall_dependencies", "clear_cache", "delay_quota_reset", "restart_workflow"] else None
        except Exception as e:
            print(f"DEBUG: Gemini analysis error: {e}")
            return None

    def apply_fix(self, repo_url: str, run_id: int, pat: str, fix: str) -> bool:
        parts = repo_url.rstrip("/").split("/")
        owner_repo = f"{parts[-2]}/{parts[-1]}"
        headers = {"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"}
        if fix in ["retry_workflow", "restart_workflow"]:
            api_url = f"https://api.github.com/repos/{owner_repo}/actions/runs/{run_id}/rerun"
            resp = requests.post(api_url, headers=headers)
            return resp.status_code == 201
        return False

    def check_telegram_updates(self):
        if not self.telegram_token: return
        url = f"https://api.telegram.org/bot{self.telegram_token}/getUpdates"
        try:
            resp = requests.get(url).json()
            if not resp.get("ok"): return
            for update in resp.get("result", []):
                message = update.get("message", {})
                text = message.get("text", "")
                if text.startswith("Add new Telegram bot:"):
                    try:
                        data = text.split(":")[1].strip().split(",")
                        repo_url = data[0].strip()
                        acc = data[1].strip()
                        channel = data[2].strip()
                        new_bot = {"name": repo_url.split("/")[-1], "repo_url": repo_url, "account": acc, "channel": channel, "type": "telegram"}
                        if not any(b['repo_url'] == repo_url for b in self.config['bots']):
                            self.config['bots'].append(new_bot)
                            self.save_config()
                            self.send_telegram_message(f"✅ New bot added successfully!")
                    except Exception as e:
                        self.send_telegram_message(f"❌ Failed to parse bot info: {e}")
        except Exception: pass

    def send_telegram_message(self, text: str):
        if not self.telegram_token or not self.telegram_chat_id:
            print(f"DEBUG: Telegram not configured. Message: {text}")
            return
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        print(f"DEBUG: Sending message to {self.telegram_chat_id}...")
        # Removed parse_mode to avoid "Bad Request: can't parse entities" errors
        resp = requests.post(url, json={"chat_id": self.telegram_chat_id, "text": text})
        print(f"DEBUG: Telegram response: {resp.status_code} - {resp.text}")

    def run_monitoring(self):
        today = datetime.datetime.now().strftime("%d %b %Y")
        self.report_lines.append(f"Daily Supervisor Report - {today}")
        all_healthy = True
        for bot in self.config.get("bots", []):
            name, repo, acc, channel = bot.get("name"), bot.get("repo_url"), bot.get("account"), bot.get("channel")
            pat = self.get_github_pat(acc)
            if not pat:
                self.report_lines.append(f"Bot: {name} ({channel}) - Status: Missing PAT")
                all_healthy = False
                continue
            run = self.fetch_latest_workflow_run(repo, pat)
            if not run:
                self.report_lines.append(f"Bot: {name} ({channel}) - Status: No runs found")
                continue
            if run.get("conclusion") == "success":
                self.report_lines.append(f"Bot: {name} ({channel}) - Status: OK")
            else:
                all_healthy = False
                error_msg = run.get("display_title", "Error")
                self.report_lines.append(f"Bot: {name} ({channel}) - Status: FAILED ({error_msg})")
                fix = self.analyze_with_gemini(name, error_msg)
                if fix and self.apply_fix(repo, run.get("id"), pat, fix):
                    self.report_lines.append(f"Fix applied: {fix}")
        self.report_lines.append(f"\nSystem status: {'HEALTHY' if all_healthy else 'ATTENTION REQUIRED'}")
        self.send_telegram_message("\n".join(self.report_lines))

if __name__ == "__main__":
    supervisor = SupervisorBot()
    supervisor.check_telegram_updates()
    supervisor.run_monitoring()
