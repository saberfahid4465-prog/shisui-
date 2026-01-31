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
        parts = repo_url.rstrip("/").split("/")
        owner_repo = f"{parts[-2]}/{parts[-1]}"
        api_url = f"https://api.github.com/repos/{owner_repo}/actions/runs?per_page=1"
        headers = {"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"}
        
        try:
            response = requests.get(api_url, headers=headers)
            if response.status_code == 200:
                runs = response.json().get("workflow_runs", [])
                return runs[0] if runs else None
        except Exception as e:
            print(f"Error fetching workflow for {owner_repo}: {e}")
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
                    except Exception: pass
        except Exception: pass

    def send_telegram_message(self, text: str):
        if not self.telegram_token or not self.telegram_chat_id:
            return
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        requests.post(url, json={"chat_id": self.telegram_chat_id, "text": text})

    def run_monitoring(self):
        today = datetime.datetime.now().strftime("%d %b %Y %H:%M UTC")
        self.report_lines.append(f"📊 DETAILED SUPERVISOR REPORT")
        self.report_lines.append(f"📅 Date: {today}")
        self.report_lines.append("-" * 30)
        
        all_healthy = True
        for bot in self.config.get("bots", []):
            name = bot.get("name")
            repo = bot.get("repo_url")
            acc = bot.get("account")
            channel = bot.get("channel")
            
            bot_report = [f"🤖 Bot: {name}", f"📢 Channel: {channel}", f"🔗 Repo: {repo}"]
            
            pat = self.get_github_pat(acc)
            if not pat:
                bot_report.append("❌ Status: Missing Access Token (PAT)")
                all_healthy = False
            else:
                run = self.fetch_latest_workflow_run(repo, pat)
                if not run:
                    bot_report.append("⚪ Status: No workflow runs found")
                else:
                    status = run.get("conclusion")
                    run_time = run.get("updated_at", "Unknown")
                    workflow_name = run.get("name", "Unknown Workflow")
                    
                    if status == "success":
                        bot_report.append(f"✅ Status: OK (Workflow: {workflow_name})")
                        bot_report.append(f"🕒 Last Run: {run_time}")
                    else:
                        all_healthy = False
                        error_msg = run.get("display_title", "Unknown Error")
                        bot_report.append(f"🔴 Status: FAILED ({error_msg})")
                        bot_report.append(f"🕒 Failed at: {run_time}")
                        
                        fix = self.analyze_with_gemini(name, error_msg)
                        if fix:
                            bot_report.append(f"🧠 AI Analysis: Suggesting '{fix}'")
                            if self.apply_fix(repo, run.get("id"), pat, fix):
                                bot_report.append(f"🛠 Auto-fix: Applied successfully ✅")
                            else:
                                bot_report.append(f"🛠 Auto-fix: Failed to apply ❌")
                        else:
                            bot_report.append("⚠️ AI Analysis: No safe auto-fix available")
            
            self.report_lines.append("\n".join(bot_report))
            self.report_lines.append("-" * 30)

        summary = "✅ SYSTEM STATUS: HEALTHY" if all_healthy else "⚠️ SYSTEM STATUS: ATTENTION REQUIRED"
        self.report_lines.append(f"\n{summary}")
        
        self.send_telegram_message("\n".join(self.report_lines))

if __name__ == "__main__":
    supervisor = SupervisorBot()
    supervisor.check_telegram_updates()
    supervisor.run_monitoring()
