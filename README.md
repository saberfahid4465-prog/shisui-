# Supervisor Bot

This bot monitors multiple GitHub-hosted Python bots, applies AI-powered safe auto-fixes using Gemini, and sends daily Telegram reports.

## 🚀 Deployment to GitHub Actions

1.  **Create a new repository** on GitHub.
2.  **Push these files** to your repository.
3.  **Set up GitHub Secrets**:
    Go to `Settings > Secrets and variables > Actions` and add the following:
    - `TELEGRAM_TOKEN`
    - `TELEGRAM_CHAT_ID`
    - `GEMINI_API_KEY`
    - `GITHUB_PAT_ACC1`
    - `GITHUB_PAT_ACC2`

## 🤖 Features
- **Daily Monitoring**: Runs automatically at 09:00 UTC.
- **AI Analysis**: Uses Gemini to suggest fixes for failed workflows.
- **Auto-Fix**: Automatically retries workflows if a safe fix is identified.
- **Telegram Reports**: Sends a summary to your Telegram chat.
- **Add Bots via Telegram**: Send `Add new Telegram bot: URL, ACC, CHANNEL` to the bot to add more.

## 🛠 Local Setup
1. Install dependencies: `pip install -r requirements.txt`
2. Create a `.env` file based on the provided template.
3. Run: `python supervisor.py`
