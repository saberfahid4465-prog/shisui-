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
        self.auto_fixes_count = 0
        self.manual_actions_count = 0
        
        # System prompt for the AI Assistant
        self.system_prompt = f"""
        You are 'Shisui', a smart Supervisor Bot and AI Assistant. 
        Your job is to help the user monitor their GitHub bots and answer any questions.
        Today's date is {datetime.datetime.now().strftime('%d %b %Y')}.
        
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
            # Clean URL and get owner/repo
            repo_path = repo_url.replace("https://github.com/", "").replace(".git", "").strip("/")
            api_url = f"https://api.github.com/repos/{repo_path}/actions/runs?per_page=1"
            headers = {
                "Authorization": f"token {pat}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Supervisor-Bot"
            }
            response = requests.get(api_url, headers=headers, timeout=15)
            if response.status_code == 200:
                runs = response.json().get("workflow_runs", [])
                return runs[0] if runs else None
            else:
                print(f"GitHub API Error for {repo_path}: {response.status_code}")
        except Exception as e:
            print(f"Error fetching workflow: {e}")
        return None

    def analyze_with_gemini(self, bot_name: str, error_context: str) -> Dict[str, Any]:
        prompt = f"""
        Analyze failure for {bot_name}: {error_context}. 
        Return a JSON object with:
        - "fix": one of [retry_workflow, reinstall_dependencies, clear_cache, delay_quota_reset, restart_workflow, none]
        - "confidence": integer 0-100
        - "reason": short explanation
        """
        try:
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception:
            return {"fix": "none", "confidence": 0, "reason": "AI analysis failed"}

    def apply_fix(self, repo_url: str, run_id: int, pat: str, fix: str) -> bool:
        try:
            repo_path = repo_url.replace("https://github.com/", "").replace(".git", "").strip("/")
            headers = {"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"}
            if fix in ["retry_workflow", "restart_workflow"]:
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
        # Use real-time date
        current_date = datetime.datetime.now().strftime("%d %b %Y")
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
                report.append(f"🟢 {name} ({channel})\n   ✔ Status: Success\n   ℹ Notes: No recent runs found")
                continue
            
            status = run.get("conclusion")
            if status == "success":
                report.append(f"🟢 {name} ({channel})\n   ✔ Status: Success\n   ℹ Notes: Ran normally")
            else:
                error_msg = run.get("display_title", "Unknown Error")
                ai_result = self.analyze_with_gemini(name, error_msg)
                fix = ai_result.get("fix", "none")
                confidence = ai_result.get("confidence", 0)
                total_confidence.append(confidence)
                
                report.append(f"🔴 {name} ({channel})\n   ❌ Status: Failed\n   ⚠ Error: {error_msg}")
                
                if fix != "none":
                    if self.apply_fix(repo, run.get("id"), pat, fix):
                        report.append(f"   🛠 Auto-fix applied: {fix.replace('_', ' ')} ✅")
                        self.auto_fixes_count += 1
                    else:
                        report.append(f"   🛠 Auto-fix failed ❌")
                        self.manual_actions_count += 1
                else:
                    report.append(f"   ⚠️ Manual action required")
                    self.manual_actions_count += 1

        report.append(f"\n🚨 Manual Action Required: {self.manual_actions_count}")
        report.append(f"⚙ Auto-Fixes Applied Today: {self.auto_fixes_count}")
        
        system_status = "HEALTHY ✅" if self.manual_actions_count == 0 else "ATTENTION REQUIRED ⚠️"
        report.append(f"System Status: {system_status}")
        
        if total_confidence:
            avg_conf = sum(total_confidence) / len(total_confidence)
            report.append(f"Confidence (Gemini AI): {int(avg_conf)}%")
        
        self.send_telegram_message("\n".join(report), chat_id)

    def ai_chat(self, user_message: str) -> str:
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
                message = update.get("message", {})
                text = message.get("text", "")
                chat_id = message.get("chat", {}).get("id")
                if not text: continue
                if text.lower() == "/status":
                    self.run_monitoring(chat_id)
                else:
                    self.send_telegram_message(self.ai_chat(text), chat_id)
        except Exception: pass

if __name__ == "__main__":
    bot = SupervisorBot()
    # Check if run by schedule or manual trigger
    event = os.getenv("GITHUB_EVENT_NAME")
    if event in ["schedule", "workflow_dispatch"]:
        bot.run_monitoring()
    else:
        # Polling mode for 10 minutes in GH Actions
        start = time.time()
        while time.time() - start < 600:
            bot.process_updates()
            time.sleep(5)
