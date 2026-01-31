import os
import yaml
import requests
import json
import datetime
import time
import random
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
        self.auto_fixes_count = 0
        self.manual_actions_count = 0
        
        # Get current time for system prompt
        now = datetime.datetime.now()
        self.system_prompt = f"""
        You are 'Shisui', a smart Supervisor Bot and AI Assistant. 
        Your job is to help the user monitor their GitHub bots and answer any questions.
        Today's date is {now.strftime('%d %b %Y')}.
        Current time is {now.strftime('%H:%M:%S')} UTC.
        
        Current bots being monitored:
        {json.dumps(self.config.get('bots', []), indent=2)}
        """

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
            repo_path = repo_url.replace("https://github.com/", "").replace(".git", "").strip("/")
            api_url = f"https://api.github.com/repos/{repo_path}/actions/runs?per_page=1"
            headers = {
                "Authorization": f"token {pat}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Supervisor-Bot"
            }
            # Add a random query parameter to bypass any potential caching
            response = requests.get(f"{api_url}&t={time.time()}", headers=headers, timeout=15)
            if response.status_code == 200:
                runs = response.json().get("workflow_runs", [])
                return runs[0] if runs else None
        except Exception:
            pass
        return None

    def analyze_with_gemini(self, bot_name: str, error_context: str) -> Dict[str, Any]:
        prompt = f"Analyze failure for {bot_name}: {error_context}. Return JSON with 'fix' (retry_workflow, none), 'confidence' (0-100), 'reason'."
        try:
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception:
            return {"fix": "none", "confidence": 0}

    def apply_fix(self, repo_url: str, run_id: int, pat: str, fix: str) -> bool:
        try:
            repo_path = repo_url.replace("https://github.com/", "").replace(".git", "").strip("/")
            headers = {"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"}
            if fix == "retry_workflow":
                api_url = f"https://api.github.com/repos/{repo_path}/actions/runs/{run_id}/rerun"
                resp = requests.post(api_url, headers=headers)
                return resp.status_code == 201
        except Exception:
            pass
        return False

    def send_telegram_message(self, text: str, chat_id: str = None):
        target_id = chat_id or self.telegram_chat_id
        if not self.telegram_token or not target_id:
            return
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        requests.post(url, json={"chat_id": target_id, "text": text}, timeout=15)

    def run_monitoring(self, chat_id: str = None):
        # FORCE REFRESH DATE
        now = datetime.datetime.now()
        current_date = now.strftime("%d %b %Y")
        current_time = now.strftime("%H:%M:%S")
        
        report = [f"📊 Daily Supervisor Report – {current_date}"]
        
        self.auto_fixes_count = 0
        self.manual_actions_count = 0
        total_confidence = []
        
        for bot in self.config.get("bots", []):
            name = bot.get("name")
            repo = bot.get("repo_url")
            acc = bot.get("account")
            channel = bot.get("channel")
            
            pat = self.get_github_pat(acc)
            if not pat:
                report.append(f"🔴 {name} ({channel})\n   ❌ Status: Failed\n   ⚠ Error: Missing PAT")
                self.manual_actions_count += 1
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
                ai_result = self.analyze_with_gemini(name, error_msg)
                fix = ai_result.get("fix", "none")
                total_confidence.append(ai_result.get("confidence", 0))
                
                report.append(f"🔴 {name} ({channel})\n   ❌ Status: Failed\n   ⚠ Error: {error_msg}")
                if fix != "none" and self.apply_fix(repo, run.get("id"), pat, fix):
                    report.append(f"   🛠 Auto-fix applied: {fix} ✅")
                    self.auto_fixes_count += 1
                else:
                    self.manual_actions_count += 1

        report.append(f"\n🚨 Manual Action Required: {self.manual_actions_count}")
        report.append(f"⚙ Auto-Fixes Applied Today: {self.auto_fixes_count}")
        report.append(f"System Status: {'HEALTHY ✅' if self.manual_actions_count == 0 else 'ATTENTION ⚠️'}")
        
        if total_confidence:
            report.append(f"Confidence (Gemini AI): {int(sum(total_confidence)/len(total_confidence))}%")
        
        report.append(f"\n🕒 Last Updated: {current_time} UTC")
        
        self.send_telegram_message("\n".join(report), chat_id)

    def ai_chat(self, user_message: str) -> str:
        try:
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "system", "content": self.system_prompt}, {"role": "user", "content": user_message}]
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
            if not resp.get("ok"): return
            for update in resp.get("result", []):
                self.last_update_id = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "")
                cid = msg.get("chat", {}).get("id")
                if text.lower() == "/status":
                    self.run_monitoring(cid)
                elif text:
                    self.send_telegram_message(self.ai_chat(text), cid)
        except Exception: pass

if __name__ == "__main__":
    bot = SupervisorBot()
    if os.getenv("GITHUB_EVENT_NAME") in ["schedule", "workflow_dispatch"]:
        bot.run_monitoring()
    else:
        start = time.time()
        while time.time() - start < 600:
            bot.process_updates()
            time.sleep(5)
