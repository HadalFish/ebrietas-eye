# Ebrietas' Eye

A Python automation bot that monitors [ReadySub](https://app.readysub.com) for available substitute teaching jobs, sends real-time notifications via Telegram and Gmail, and optionally auto-accepts jobs that meet configurable criteria.

## Features

- Polls ReadySub every 4 seconds for new available jobs
- Filters jobs by school, position keywords, teacher blacklist, and user-defined unavailable dates
- Auto-accepts jobs matching a configurable greenlist of schools, teachers, and position types
- Sends Telegram and email notifications organized by job type (half-day AM, half-day PM, full day)
- Telegram bot interface for live status checks and remote control
- Persists seen/accepted job history across restarts
- Auto-restarts on crash with email and Telegram alerts

## Requirements

- Python 3.10+
- A ReadySub substitute account
- A Telegram bot token and chat ID
- A Gmail account with an [App Password](https://myaccount.google.com/apppasswords) configured

## Setup

1. Clone the repository and create a virtual environment:
   ```bash
   git clone https://github.com/HadalFish/ebrietas-eye.git
   cd ebrietas-eye
   python -m venv venv
   source venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Copy the example environment file and fill in your values:
   ```bash
   cp .env.example .env
   ```

4. Create the data directory and required list files:
   ```bash
   mkdir -p data
   touch data/blacklist.txt data/greenlist.txt
   ```
   Set `LOG_PATH` in `.env` to a path inside your data directory, e.g. `data/bot.log`.
   The bot will auto-create `dont_hunt_dates.txt` with a usage template on first run.

5. Run the bot:
   ```bash
   python ebrietas_eye.py
   ```

## Configuration Files

All list files live in the same directory as your log file (`LOG_PATH`).

### `blacklist.txt`
Teachers whose jobs should never be notified or accepted. One name per line (case-insensitive). Lines starting with `#` are comments.

```
# Example
john smith
jane doe
```

### `greenlist.txt`
Teachers whose jobs should always be auto-accepted regardless of other criteria. Same format as blacklist.txt.

### `dont_hunt_dates.txt`
Dates you are unavailable. Jobs on these dates are silently filtered. Format: `MM/DD/YYYY`.

```
# Example
12/25/2025
01/01/2026
```

## Telegram Commands

Send these to your bot in the configured chat:

| Command | Action |
|---|---|
| `/status` | Show bot status, version, and list counts |
| `on` / `off` | Enable or disable auto-accept |
| `reload` | Reload blacklist, greenlist, and dont-hunt dates from files |
| `/help` | Show command list |

## Auto-Accept Logic

Jobs are auto-accepted when:
- The teacher is on the greenlist (highest priority)
- The school is on the auto-accept school list (e.g. Washington Elementary)
- The position matches school-specific criteria (e.g. sped at MLK High, self-contained at Maddison)

Auto-accept can be toggled at runtime via Telegram (`on` / `off`) and persists across restarts.

## Project Structure

```
ebrietas_eye.py      Main bot script
.env.example         Environment variable template
requirements.txt     Python dependencies
data/                Runtime data (gitignored)
  bot.log
  sent_jobs.txt
  all_jobs_log.txt
  blacklist.txt
  greenlist.txt
  dont_hunt_dates.txt
```

## Dependencies

```
requests
beautifulsoup4
python-dotenv
pytz
```

Generate `requirements.txt` with:
```bash
pip freeze > requirements.txt
```
