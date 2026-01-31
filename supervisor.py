import os
import yaml
import requests
import json
import datetime
from typing import List, Dict, Any, Optional
from openai import OpenAI

# Initialize OpenAI client for Gemini (using the pre-configured environment)
# The system provides a gpt-4.1-mini/nano or gemini-2.5-flash via OpenAI-compatible API
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
        # Maps 'acc1' to 'GITHUB_PAT_ACC1'
        env_var = f"PAT_{account_key.upper()}"
        return os.getenv(env_var)

    def fetch_latest_workflow_run(self, repo_url: str, pat: str) -> Optional[Dict]:
        # Extract owner/repo from URL
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

    def get_workflow_logs(self, repo_url: str, run_id: int, pat: str) -> str:
        parts = repo_url.rstrip("/").split("/")
        owner_repo = f"{parts[-2]}/{parts[-1]}"
        # GitHub API for logs returns a redirect to a zip file
        api_url = f"https://api.github.com/repos/{owner_repo}/actions/runs/{run_id}/logs"
        headers = {"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"}
        
        try:
            # For simplicity in this script, we'll just report the failure status 
            # and use the run's 'display_title' or 'conclusion' for Gemini analysis
            # In a full implementation, you'd download and parse the zip logs.
            return f"Workflow run {run_id} failed with status: failure."
        except Exception:
            return "Could not retrieve detailed logs."

    def analyze_with_gemini(self, bot_name: str, error_context: str) -> Optional[str]:
        """
        Uses Gemini AI to analyze the error and suggest a safe fix.
        """
        prompt = f"""
        Analyze the following GitHub Actions failure for the bot '{bot_name}':
        Context: {error_context}
        
        Available safe fixes:
        - retry_workflow
        - reinstall_dependencies
        - clear_cache
        - delay_quota_reset
        - restart_workflow
        
        If one of these fixes is highly likely to solve the issue (e.g., transient network error, quota limit, or dependency glitch), return ONLY the fix name.
        If the error is code-related or unknown, return 'none'.
        """
        try:
            response = client.chat.completions.create(
                model="gemini-2.5-flash", # Using the provided Gemini model
                messages=[{"role": "user", "content": prompt}]
            )
            fix = response.choices[0].message.content.strip().lower()
            return fix if fix in ["retry_workflow", "reinstall_dependencies", "clear_cache", "delay_quota_reset", "restart_workflow"] else None
        except Exception as e:
            print(f"Gemini analysis error: {e}")
            return None

    def apply_fix(self, repo_url: str, run_id: int, pat: str, fix: str) -> bool:
        """
        Applies the predefined safe fix via GitHub API.
        """
        parts = repo_url.rstrip("/").split("/")
        owner_repo = f"{parts[-2]}/{parts[-1]}"
        headers = {"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"}
        
        if fix in ["retry_workflow", "restart_workflow"]:
            api_url = f"https://api.github.com/repos/{owner_repo}/actions/runs/{run_id}/rerun"
            resp = requests.post(api_url, headers=headers)
            return resp.status_code == 201
        
        # Other fixes like 'clear_cache' or 'reinstall_dependencies' would typically 
        # involve triggering a specific workflow with inputs or deleting cache via API.
        # For this supervisor, we'll treat them as 'retry' triggers for the demo.
        return False

    def check_telegram_updates(self):
        """
        Checks for new bot addition requests via Telegram.
        Example: Add new Telegram bot: https://github.com/user/repo, acc1, @Channel
        """
        if not self.telegram_token: return
        
        url = f"https://api.telegram.org/bot{self.telegram_token}/getUpdates"
        try:
            resp = requests.get(url).json()
            if not resp.get("ok"): return
            
            for update in resp.get("result", []):
                message = update.get("message", {})
                text = message.get("text", "")
                if text.startswith("Add new Telegram bot:"):
                    # Parsing: Add new Telegram bot: URL, ACC, CHANNEL
                    try:
                        data = text.split(":")[1].strip().split(",")
                        repo_url = data[0].strip()
                        acc = data[1].strip()
                        channel = data[2].strip()
                        
                        new_bot = {
                            "name": repo_url.split("/")[-1],
                            "repo_url": repo_url,
                            "account": acc,
                            "channel": channel,
                            "type": "telegram"
                        }
                        
                        # Check if already exists
                        if not any(b['repo_url'] == repo_url for b in self.config['bots']):
                            self.config['bots'].append(new_bot)
                            self.save_config()
                            self.send_telegram_message(f"✅ New bot added successfully!\nIt will be included in the next daily report.")
                    except Exception as e:
                        self.send_telegram_message(f"❌ Failed to parse bot info: {e}")
        except Exception as e:
            print(f"Error checking Telegram updates: {e}")

    def send_telegram_message(self, text: str):
        if not self.telegram_token or not self.telegram_chat_id:
            print(f"Telegram not configured. Message: {text}")
            return
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        requests.post(url, json={"chat_id": self.telegram_chat_id, "text": text, "parse_mode": "Markdown"})

    def run_monitoring(self):
        today = datetime.datetime.now().strftime("%d %b %Y")
        self.report_lines.append(f"📊 Daily Supervisor Report – {today}")
        
        all_healthy = True
        for bot in self.config.get("bots", []):
            name = bot.get("name")
            repo = bot.get("repo_url")
            acc = bot.get("account")
            channel = bot.get("channel")
            
            pat = self.get_github_pat(acc)
            if not pat:
                self.report_lines.append(f"🔴 {name} ({channel}) ❌ Missing PAT for {acc}")
                all_healthy = False
                continue
            
            run = self.fetch_latest_workflow_run(repo, pat)
            if not run:
                self.report_lines.append(f"⚪ {name} ({channel}) ❓ No workflow runs found")
                continue
            
            status = run.get("conclusion")
            if status == "success":
                self.report_lines.append(f"🟢 {name} ({channel}) ✔ OK")
            else:
                # Failure detected
                all_healthy = False
                error_msg = run.get("display_title", "Unknown error")
                self.report_lines.append(f"🔴 {name} ({channel}) ❌ {error_msg}")
                
                # AI Analysis & Auto-fix
                fix_suggestion = self.analyze_with_gemini(name, error_msg)
                if fix_suggestion:
                    success = self.apply_fix(repo, run.get("id"), pat, fix_suggestion)
                    if success:
                        self.report_lines.append(f"🛠 Auto-fix applied: {fix_suggestion.replace('_', ' ')} ✅")
                    else:
                        self.report_lines.append(f"🛠 Auto-fix failed: {fix_suggestion.replace('_', ' ')} ❌")
                else:
                    self.report_lines.append(f"⚠️ No safe auto-fix available.")

        status_summary = "System status: HEALTHY ✅" if all_healthy else "System status: ATTENTION REQUIRED ⚠️"
        self.report_lines.append(f"\n{status_summary}")
        
        # Send the final report
        self.send_telegram_message("\n".join(self.report_lines))

if __name__ == "__main__":
    supervisor = SupervisorBot()
    # 1. Check for new bots from Telegram first
    supervisor.check_telegram_updates()
    # 2. Run the daily monitoring and report
    supervisor.run_monitoring()
