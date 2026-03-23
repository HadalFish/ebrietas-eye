from dotenv import load_dotenv
load_dotenv()

import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
import smtplib
from email.message import EmailMessage
import time
import traceback
import os
from typing import List, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Startup validation -- fail loud and early if .env is incomplete
# ---------------------------------------------------------------------------

_REQUIRED_ENV_VARS = [
    "LOG_PATH",
    "LOGIN_URL",
    "TYLER_EMAIL",
    "TYLER_PASSWORD",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "SMTP_USER",
    "SMTP_PASS",
    "NOTIFICATION_EMAIL",
]

def _validate_env():
    missing = [v for v in _REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        raise EnvironmentError(
            f"missing required environment variables: {', '.join(missing)}\n"
            "check your .env file and make sure all values are set."
        )

_validate_env()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class Config:
    VERSION = "v3.1.2"

    LOG_PATH = os.getenv("LOG_PATH")
    _LOG_DIR = os.path.dirname(LOG_PATH)

    SENT_JOBS_PATH         = os.path.join(_LOG_DIR, "sent_jobs.txt")
    ALL_JOBS_LOG_PATH      = os.path.join(_LOG_DIR, "all_jobs_log.txt")
    AUTO_ACCEPT_STATE_PATH = os.path.join(_LOG_DIR, "auto_accept_state.txt")
    GREENLIST_PATH         = os.path.join(_LOG_DIR, "greenlist.txt")
    BLACKLIST_PATH         = os.path.join(_LOG_DIR, "blacklist.txt")
    DONT_HUNT_DATES_PATH   = os.path.join(_LOG_DIR, "dont_hunt_dates.txt")
    TELEGRAM_UPDATE_PATH   = os.path.join(_LOG_DIR, "last_telegram_update.txt")

    LOGIN_URL = os.getenv("LOGIN_URL")
    EMAIL     = os.getenv("TYLER_EMAIL")
    PASSWORD  = os.getenv("TYLER_PASSWORD")

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

    SMTP_SERVER        = "smtp.gmail.com"
    SMTP_PORT          = 587
    SMTP_USER          = os.getenv("SMTP_USER")
    SMTP_PASS          = os.getenv("SMTP_PASS")
    NOTIFICATION_EMAIL = os.getenv("NOTIFICATION_EMAIL")

    # Timing
    POLL_INTERVAL = 4
    RESTART_DELAY = 60

    # Auto-accept default (overridden by file on load)
    AUTO_ACCEPT_ENABLED = True

    # Hardcoded filter lists -- schools/keywords that are permanently blocked.
    # These are separate from blacklist.txt because they are structural rules,
    # not user-managed preferences.
    #
    # Add the exact lowercase school names and position keywords you want
    # permanently blocked (i.e. rules that never change regardless of user config).
    # Examples:
    #   BLOCKED_SCHOOLS = ["example elementary", "another middle school"]
    #   BLOCKED_POSITION_KEYWORDS = ["ell", "dual language", "phys ed"]
    BLOCKED_SCHOOLS: List[str] = []
    BLOCKED_POSITION_KEYWORDS: List[str] = []

    # In-memory lists loaded from files
    TEACHER_BLACKLIST: List[str] = []
    TEACHER_GREENLIST: List[str] = []
    DONT_HUNT_DATES:   List      = []

    @classmethod
    def load_lists(cls):
        """Load blacklist, greenlist, and dont_hunt_dates from files."""
        cls._load_name_list(cls.BLACKLIST_PATH, "TEACHER_BLACKLIST", "blacklist")
        cls._load_name_list(cls.GREENLIST_PATH, "TEACHER_GREENLIST", "greenlist")
        cls._load_dates()

    @classmethod
    def _load_name_list(cls, path: str, attr: str, label: str):
        """Generic loader for line-separated name files."""
        setattr(cls, attr, [])
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    entries = [
                        line.strip().lower()
                        for line in f
                        if line.strip() and not line.strip().startswith("#")
                    ]
                setattr(cls, attr, entries)
                Logger.log(f"loaded {len(entries)} {label} entries")
            except Exception as e:
                Logger.log(f"error loading {label}: {e}", "ERROR")
        else:
            Logger.log(f"no {label}.txt found, creating empty file", "WARNING")
            with open(path, "w", encoding="utf-8"):
                pass

    @classmethod
    def _load_dates(cls):
        """Load dont-hunt dates from file."""
        cls.DONT_HUNT_DATES = []
        if os.path.exists(cls.DONT_HUNT_DATES_PATH):
            try:
                with open(cls.DONT_HUNT_DATES_PATH, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            try:
                                cls.DONT_HUNT_DATES.append(
                                    datetime.strptime(line, "%m/%d/%Y").date()
                                )
                            except ValueError:
                                Logger.log(f"invalid date in dont_hunt_dates.txt: {line}", "WARNING")
                Logger.log(f"loaded {len(cls.DONT_HUNT_DATES)} dont-hunt dates")
            except Exception as e:
                Logger.log(f"error loading dont-hunt dates: {e}", "ERROR")
        else:
            Logger.log("no dont_hunt_dates.txt found, creating template", "WARNING")
            with open(cls.DONT_HUNT_DATES_PATH, "w", encoding="utf-8") as f:
                f.write("# dates you're unavailable, one per line\n")
                f.write("# format: MM/DD/YYYY\n")
                f.write("# example: 12/25/2025\n")

    @classmethod
    def load_auto_accept_state(cls):
        """Load auto-accept state from file."""
        try:
            if os.path.exists(cls.AUTO_ACCEPT_STATE_PATH):
                with open(cls.AUTO_ACCEPT_STATE_PATH, "r", encoding="utf-8") as f:
                    cls.AUTO_ACCEPT_ENABLED = f.read().strip().lower() == "true"
                Logger.log(f"auto-accept: {'enabled' if cls.AUTO_ACCEPT_ENABLED else 'disabled'}")
            else:
                cls.save_auto_accept_state()
        except Exception as e:
            Logger.log(f"error loading auto-accept state: {e}", "ERROR")

    @classmethod
    def save_auto_accept_state(cls):
        """Persist auto-accept state to file."""
        try:
            with open(cls.AUTO_ACCEPT_STATE_PATH, "w", encoding="utf-8") as f:
                f.write("true" if cls.AUTO_ACCEPT_ENABLED else "false")
        except Exception as e:
            Logger.log(f"error saving auto-accept state: {e}", "ERROR")

    @classmethod
    def set_auto_accept(cls, enabled: bool):
        """Set auto-accept state and persist."""
        cls.AUTO_ACCEPT_ENABLED = enabled
        cls.save_auto_accept_state()


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class Logger:
    @staticmethod
    def get_pdt_time() -> datetime:
        """Return current time in US/Pacific timezone."""
        return datetime.now(pytz.timezone("US/Pacific"))

    @staticmethod
    def log(message: str, level: str = "INFO"):
        timestamp = Logger.get_pdt_time().strftime("%Y-%m-%d %I:%M:%S %p")
        entry = f"[{timestamp}] [{level}] {message}"
        print(entry)
        try:
            with open(Config.LOG_PATH, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception as e:
            print(f"failed to write to log file: {e}")


# ---------------------------------------------------------------------------
# JobEvaluator
# ---------------------------------------------------------------------------

class JobEvaluator:
    """Evaluates jobs against filter and auto-accept criteria."""

    @staticmethod
    def parse_job_date(date_str: str) -> Optional[datetime]:
        """Parse a job date string into a datetime object."""
        if not date_str or date_str == "unknown date":
            return None
        try:
            # ISO format
            if "t" in date_str.lower():
                try:
                    return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    pass

            # ReadySub format: "Mon, 9/22"
            if ", " in date_str and "/" in date_str:
                date_part = date_str.split(", ")[1]
                parts = date_part.split("/")
                if len(parts) == 2:
                    try:
                        year = datetime.now().year
                        month, day = int(parts[0]), int(parts[1])
                        parsed = datetime(year, month, day)
                        if parsed.date() < datetime.now().date():
                            parsed = datetime(year + 1, month, day)
                        return parsed
                    except (ValueError, IndexError):
                        pass

            # Other common formats
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue

            Logger.log(f"could not parse date: {date_str}", "WARNING")
            return None
        except Exception as e:
            Logger.log(f"error parsing date {date_str}: {e}", "ERROR")
            return None

    @staticmethod
    def _parse_time(time_str: str) -> Optional[datetime]:
        """Parse a single time string into a datetime object."""
        time_str = time_str.strip().replace("am", " AM").replace("pm", " PM")
        for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def parse_schedule_times(schedule: str) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        Parse start and end times from a schedule string like '7:30 AM - 2:30 PM'.
        Returns (start, end), either of which may be None if parsing fails.
        """
        if not schedule or " - " not in schedule:
            return None, None
        try:
            parts = schedule.split(" - ", 1)
            start = JobEvaluator._parse_time(parts[0])
            end   = JobEvaluator._parse_time(parts[1])
            return start, end
        except Exception as e:
            Logger.log(f"error parsing schedule '{schedule}': {e}", "DEBUG")
            return None, None

    @staticmethod
    def categorize_schedule(schedule: str) -> str:
        """
        Categorize a schedule string as 'half_day_am', 'half_day_pm', or 'full_day'.

        Logic:
          1. Parse start and end times and compute duration.
          2. If duration is under HALF_DAY_THRESHOLD hours, it is a half-day.
             Use start time to split AM (starts before 10:00 AM) vs PM (starts 10:00 AM or later).
          3. Anything at or above the threshold is a full day.
          4. If the schedule cannot be parsed, default to 'full_day'.

        Known ranges for this district:
          AM half-day: ~7:00 AM - 12:30 PM (~5.5 hrs)
          PM half-day: ~11:30 AM - 3:45 PM (~4.25 hrs)
          Full day:    ~7:30 AM - 2:30 PM  (~7 hrs)

        The threshold of 6.0 hours sits cleanly between the longest half-day
        (5.5 hrs) and the shortest expected full-day shift.
        """
        HALF_DAY_THRESHOLD_HOURS = 6.0
        PM_START_HOUR = 10  # jobs starting at or after 10:00 AM are PM half-days

        start, end = JobEvaluator.parse_schedule_times(schedule)

        if start is None or end is None:
            Logger.log(f"could not parse schedule for categorization: '{schedule}'", "DEBUG")
            return "full_day"

        duration_hours = (end - start).seconds / 3600

        if duration_hours < HALF_DAY_THRESHOLD_HOURS:
            if start.hour < PM_START_HOUR:
                return "half_day_am"
            return "half_day_pm"

        return "full_day"

    @staticmethod
    def passes_first_filter(job: Dict) -> Tuple[bool, str]:
        """
        First filter: blocks jobs that should never be considered.
        Returns (passes, reason).
        """
        fields = JobBot.parse_job_fields(job)
        position   = fields["position"].lower()
        school     = fields["school"].lower()
        staff_name = fields["staff"].lower().strip()
        date_str   = fields["date"]

        # Permanently blocked schools
        for blocked in Config.BLOCKED_SCHOOLS:
            if blocked in school:
                return False, f"blocked school: {blocked}"

        # Permanently blocked position keywords
        for keyword in Config.BLOCKED_POSITION_KEYWORDS:
            if keyword in position:
                return False, f"blocked keyword: {keyword}"

        # User-managed teacher blacklist
        for blocked_teacher in Config.TEACHER_BLACKLIST:
            if blocked_teacher in staff_name:
                return False, f"blacklisted teacher: {staff_name}"

        # Dont-hunt dates
        job_date = JobEvaluator.parse_job_date(date_str)
        if job_date and job_date.date() in Config.DONT_HUNT_DATES:
            return False, f"dont-hunt date: {job_date.strftime('%m/%d/%Y')}"

        return True, "passed first filter"

    @staticmethod
    def should_auto_accept(job: Dict) -> Tuple[bool, str]:
        """
        Determine if a job should be auto-accepted.
        Returns (should_accept, reason).
        """
        try:
            fields   = JobBot.parse_job_fields(job)
            position = fields["position"].lower()
            school   = fields["school"].lower()
            staff    = fields["staff"].lower().strip()

            is_sped           = "sped" in position or "special education" in position
            is_self_contained = "self contained" in position or "self-contained" in position

            # Greenlist (highest priority)
            for greenlisted in Config.TEACHER_GREENLIST:
                if greenlisted in staff:
                    return True, f"teacher on greenlist: {staff}"

            # Add your auto-accept rules below.
            # Each rule should check the school name (or position type) and return
            # (True, "reason string") if the job should be accepted automatically.
            #
            # Examples:
            #
            # Always accept any job at a specific school:
            #   if "example elementary" in school:
            #       return True, "example elementary school"
            #
            # Accept sped jobs at a specific school:
            #   if is_sped and "example high" in school:
            #       return True, "sped at example high school"
            #
            # Accept self-contained jobs at a specific school:
            #   if is_self_contained and "example elementary" in school:
            #       return True, "example elementary self-contained"

            return False, "no auto-accept criteria met"

        except Exception as e:
            Logger.log(f"error evaluating auto-accept: {e}", "ERROR")
            return False, f"evaluation error: {e}"


# ---------------------------------------------------------------------------
# JobTracker
# ---------------------------------------------------------------------------

class JobTracker:
    def __init__(self):
        self.seen_jobs:     set = set()
        self.accepted_jobs: set = set()
        self._load_seen_jobs()

    def _load_seen_jobs(self):
        if os.path.exists(Config.SENT_JOBS_PATH):
            try:
                with open(Config.SENT_JOBS_PATH, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("[ACCEPTED] "):
                            key = line[11:]
                            self.accepted_jobs.add(key)
                            self.seen_jobs.add(key)
                        else:
                            self.seen_jobs.add(line)
                Logger.log(f"loaded {len(self.seen_jobs)} previously seen jobs")
            except Exception as e:
                Logger.log(f"error loading seen jobs: {e}", "ERROR")

    def _save_seen_jobs(self):
        try:
            with open(Config.SENT_JOBS_PATH, "w", encoding="utf-8") as f:
                for key in self.accepted_jobs:
                    f.write(f"[ACCEPTED] {key}\n")
                for key in self.seen_jobs:
                    if key not in self.accepted_jobs:
                        f.write(f"{key}\n")
        except Exception as e:
            Logger.log(f"error saving seen jobs: {e}", "ERROR")

    def has_been_sent(self, job_key: str) -> bool:
        return job_key in self.seen_jobs

    def has_been_accepted(self, job_key: str) -> bool:
        return job_key in self.accepted_jobs

    def is_new_job(self, job_key: str) -> bool:
        return job_key not in self.seen_jobs

    def mark_as_sent(self, job_key: str):
        if job_key not in self.seen_jobs:
            self.seen_jobs.add(job_key)
            self._save_seen_jobs()

    def mark_as_accepted(self, job_key: str):
        if job_key not in self.accepted_jobs:
            self.accepted_jobs.add(job_key)
            self.seen_jobs.add(job_key)
            self._save_seen_jobs()


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------

class Notifier:
    @staticmethod
    def send_telegram(message: str) -> bool:
        try:
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
            response = requests.post(
                url,
                json={
                    "chat_id": Config.TELEGRAM_CHAT_ID,
                    "text": message,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("ok"):
                Logger.log("telegram message sent")
                return True
            Logger.log(f"telegram api error: {result.get('description', 'unknown')}", "ERROR")
            return False
        except Exception as e:
            Logger.log(f"failed to send telegram message: {e}", "ERROR")
            return False

    @staticmethod
    def send_email(message: str, subject: str = "ReadySub Notification") -> bool:
        try:
            msg = EmailMessage()
            msg.set_content(message)
            msg["Subject"] = subject
            msg["From"]    = Config.SMTP_USER
            msg["To"]      = Config.NOTIFICATION_EMAIL

            with smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT) as server:
                server.starttls()
                server.login(Config.SMTP_USER, Config.SMTP_PASS)
                server.send_message(msg)

            Logger.log("email sent")
            return True
        except Exception as e:
            Logger.log(f"failed to send email: {e}", "ERROR")
            return False


# ---------------------------------------------------------------------------
# TelegramHandler
# ---------------------------------------------------------------------------

class TelegramHandler:
    def __init__(self):
        self.update_file   = Config.TELEGRAM_UPDATE_PATH
        self.last_update_id = self._load_last_update_id()

    def _load_last_update_id(self) -> int:
        if os.path.exists(self.update_file):
            try:
                with open(self.update_file, "r", encoding="utf-8") as f:
                    return int(f.read().strip())
            except ValueError:
                Logger.log("last_telegram_update.txt contained invalid data, resetting to 0", "WARNING")
            except Exception as e:
                Logger.log(f"could not load last update id: {e}", "WARNING")
        return 0

    def _save_last_update_id(self, update_id: int):
        try:
            with open(self.update_file, "w", encoding="utf-8") as f:
                f.write(str(update_id))
            self.last_update_id = update_id
        except Exception as e:
            Logger.log(f"could not save last update id: {e}", "WARNING")

    def check_and_handle_messages(self):
        try:
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/getUpdates"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            result = response.json()
            if not result.get("ok"):
                return

            updates = result.get("result", [])
            new_updates = [u for u in updates if u.get("update_id", 0) > self.last_update_id]

            if not new_updates:
                return

            self._handle_commands(new_updates)
            self._save_last_update_id(max(u.get("update_id", 0) for u in new_updates))

        except Exception as e:
            Logger.log(f"error checking telegram messages: {e}", "ERROR")

    def _handle_commands(self, updates: List[Dict]):
        for update in updates:
            message  = update.get("message", {})
            text     = message.get("text", "").strip().lower()
            chat_id  = str(message.get("chat", {}).get("id", ""))

            if chat_id != Config.TELEGRAM_CHAT_ID:
                continue

            if text in ("/status", "status"):
                auto_status = "enabled" if Config.AUTO_ACCEPT_ENABLED else "disabled"
                Notifier.send_telegram(
                    f"bot status: online\n"
                    f"time: {Logger.get_pdt_time().strftime('%Y-%m-%d %I:%M:%S %p')}\n"
                    f"auto-accept: {auto_status}\n"
                    f"blacklist: {len(Config.TEACHER_BLACKLIST)} teachers\n"
                    f"greenlist: {len(Config.TEACHER_GREENLIST)} teachers\n"
                    f"dont hunt: {len(Config.DONT_HUNT_DATES)} dates\n"
                    f"version: {Config.VERSION}"
                )

            elif text in ("/on", "on"):
                Config.set_auto_accept(True)
                Notifier.send_telegram("auto-accept enabled")

            elif text in ("/off", "off"):
                Config.set_auto_accept(False)
                Notifier.send_telegram("auto-accept disabled")

            elif text in ("/reload", "reload"):
                Config.load_lists()
                Notifier.send_telegram(
                    f"lists reloaded\n"
                    f"blacklist: {len(Config.TEACHER_BLACKLIST)}\n"
                    f"greenlist: {len(Config.TEACHER_GREENLIST)}\n"
                    f"dont hunt: {len(Config.DONT_HUNT_DATES)}"
                )

            elif text in ("/help", "help"):
                Notifier.send_telegram(
                    "=== commands ===\n"
                    "/status - bot status\n"
                    "on / off - toggle auto-accept\n"
                    "reload - reload lists from files\n"
                    "/help - this message\n\n"
                    "manage lists by editing:\n"
                    "  greenlist.txt\n"
                    "  blacklist.txt\n"
                    "  dont_hunt_dates.txt"
                )


# ---------------------------------------------------------------------------
# ReadySubScraper
# ---------------------------------------------------------------------------

class ReadySubScraper:
    _USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self._USER_AGENT})
        self.is_logged_in = False

    def login(self) -> bool:
        try:
            Logger.log("attempting login to readysub...")
            login_page = self.session.get(Config.LOGIN_URL, timeout=30)
            login_page.raise_for_status()

            soup  = BeautifulSoup(login_page.text, "html.parser")
            token = soup.find("input", {"name": "__RequestVerificationToken"})

            payload = {
                "Email":      Config.EMAIL,
                "Password":   Config.PASSWORD,
                "RememberMe": "false",
            }
            if token and token.get("value"):
                payload["__RequestVerificationToken"] = token["value"]

            response = self.session.post(Config.LOGIN_URL, data=payload, timeout=30)
            response.raise_for_status()

            if "Log In" in response.text:
                Logger.log("login failed -- credentials may be incorrect", "ERROR")
                self.is_logged_in = False
                return False

            Logger.log("login successful")
            self.is_logged_in = True
            return True

        except Exception as e:
            Logger.log(f"error during login: {e}", "ERROR")
            self.is_logged_in = False
            return False

    def ensure_logged_in(self) -> bool:
        if not self.is_logged_in:
            return self.login()
        return True

    def fetch_jobs(self) -> List[Dict]:
        try:
            today    = datetime.today().strftime("%m/%d/%Y")
            jobs_url = (
                f"https://app.readysub.com/substitute/jobs/available?"
                f"isDeclined=false&isRequested=false&sortDirection=asc&sortField=date&startDate={today}"
            )
            response = self.session.get(
                jobs_url,
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                timeout=30,
            )
            response.raise_for_status()

            if response.url == Config.LOGIN_URL or "Log In" in response.text:
                Logger.log("session expired while fetching jobs", "WARNING")
                self.is_logged_in = False
                return []

            jobs = response.json().get("availableJobs", [])
            Logger.log(f"fetched {len(jobs)} job(s)")
            return jobs

        except Exception as e:
            Logger.log(f"error fetching jobs: {e}", "ERROR")
            return []

    def accept_job(self, job: Dict) -> bool:
        try:
            job_id = (job.get("acceptJobModalModel") or {}).get("jobId", "")
            if not job_id:
                Logger.log("no job id found for acceptance", "ERROR")
                return False

            accept_url = f"https://app.readysub.com/substitute/jobs/{job_id}/accept"
            response = self.session.post(
                accept_url,
                data={"jobId": job_id},
                headers={
                    "Content-Type":       "application/x-www-form-urlencoded",
                    "X-Requested-With":   "XMLHttpRequest",
                    "Referer":            f"https://app.readysub.com/substitute/jobs/{job_id}",
                },
                timeout=30,
            )

            if response.status_code == 200:
                Logger.log(f"accepted job {job_id}")
                return True

            Logger.log(f"failed to accept job {job_id}: status {response.status_code}", "ERROR")
            return False

        except Exception as e:
            Logger.log(f"error accepting job: {e}", "ERROR")
            return False


# ---------------------------------------------------------------------------
# JobBot
# ---------------------------------------------------------------------------

class JobBot:
    def __init__(self):
        self.scraper          = ReadySubScraper()
        self.tracker          = JobTracker()
        self.telegram_handler = TelegramHandler()

    # ------------------------------------------------------------------
    # Shared field parser -- single source of truth for job data access
    # ------------------------------------------------------------------

    @staticmethod
    def parse_job_fields(job: Dict) -> Dict[str, str]:
        """Extract common display fields from a raw job dict."""
        return {
            "position": job.get("position", ""),
            "school":   (job.get("siteLink") or {}).get("text", "") or "unknown school",
            "staff":    (job.get("employeePicLink") or {}).get("text", "") or "unknown staff",
            "date":     job.get("date", "unknown date"),
            "schedule": job.get("schedule", ""),
        }

    # ------------------------------------------------------------------
    # Job key
    # ------------------------------------------------------------------

    def create_job_key(self, job: Dict) -> str:
        f      = self.parse_job_fields(job)
        job_id = (job.get("acceptJobModalModel") or {}).get("jobId", "") or "noid"
        return f"{f['position'].lower()}|{f['school'].lower()}|{f['date'].lower()}|{f['staff'].lower()}|{job_id}"

    # ------------------------------------------------------------------
    # Formatters
    # ------------------------------------------------------------------

    def format_job_for_email(self, job: Dict) -> str:
        f = self.parse_job_fields(job)
        lines = [
            f"position: {f['position']}",
            f"school: {f['school']}",
            f"staff: {f['staff']}",
            f"date: {f['date']}",
        ]
        if f["schedule"]:
            lines.append(f"schedule: {f['schedule']}")
        return "\n".join(lines) + "\n"

    def format_job_for_telegram(self, job: Dict) -> str:
        f        = self.parse_job_fields(job)
        position = f["position"]

        # Flip "Type - School Level" to "School Level - Type" for readability
        if " - " in position:
            left, right = position.split(" - ", 1)
            position = f"{right.strip()} - {left.strip()}"

        lines = [position, f["school"], f["staff"], f["date"]]
        if f["schedule"]:
            lines.append(f["schedule"])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log_all_jobs(self, jobs: List[Dict]):
        try:
            timestamp = Logger.get_pdt_time().strftime("%Y-%m-%d %I:%M:%S %p")
            with open(Config.ALL_JOBS_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n=== job scan: {timestamp} ===\n")
                f.write(f"total jobs found: {len(jobs)}\n\n")
                for i, job in enumerate(jobs, 1):
                    f.write(f"--- job {i} ---\n")
                    f.write(self.format_job_for_email(job))
                    f.write("\n")
        except Exception as e:
            Logger.log(f"error logging all jobs: {e}", "ERROR")

    # ------------------------------------------------------------------
    # Categorization
    # ------------------------------------------------------------------

    def categorize_jobs(self, jobs: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Categorize jobs into half-day AM, half-day PM, or full-day.
        Note: dont-hunt-date jobs are already excluded by passes_first_filter
        and will not appear here.
        """
        categories: Dict[str, List[Dict]] = {
            "half_day_am": [],
            "half_day_pm": [],
            "full_day":    [],
        }
        for job in jobs:
            schedule = job.get("schedule", "")
            bucket   = JobEvaluator.categorize_schedule(schedule)
            if bucket == "half_day_am":
                categories["half_day_am"].append(job)
            elif bucket == "half_day_pm":
                categories["half_day_pm"].append(job)
            else:
                categories["full_day"].append(job)
        return categories

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def process_jobs(self, jobs: List[Dict]) -> Tuple[List[Dict], List[Tuple[Dict, str]]]:
        """Run jobs through filters and return (new_jobs, auto_accept_jobs)."""
        new_jobs:         List[Dict]            = []
        auto_accept_jobs: List[Tuple[Dict, str]] = []

        for job in jobs:
            job_key          = self.create_job_key(job)
            f                = self.parse_job_fields(job)
            position, school = f["position"].lower(), f["school"].lower()

            passes, reason = JobEvaluator.passes_first_filter(job)
            if not passes:
                Logger.log(f"filtered: {position} at {school} -- {reason}")
                continue

            Logger.log(f"passed filter: {position} at {school}")

            if self.tracker.is_new_job(job_key):
                new_jobs.append(job)
                self.tracker.mark_as_sent(job_key)

            if Config.AUTO_ACCEPT_ENABLED and not self.tracker.has_been_accepted(job_key):
                should_accept, accept_reason = JobEvaluator.should_auto_accept(job)
                if should_accept:
                    auto_accept_jobs.append((job, accept_reason))

        return new_jobs, auto_accept_jobs

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def send_notifications(self, jobs: List[Dict]):
        if not jobs:
            return

        categories    = self.categorize_jobs(jobs)
        telegram_parts = []

        if categories["half_day_am"]:
            telegram_parts.append("🌅 half day am (7:15-9:00)")
            for job in categories["half_day_am"]:
                telegram_parts.append(self.format_job_for_telegram(job))
                telegram_parts.append("")

        if categories["half_day_pm"]:
            telegram_parts.append("🌞 half day pm (10:00-1:00)")
            for job in categories["half_day_pm"]:
                telegram_parts.append(self.format_job_for_telegram(job))
                telegram_parts.append("")

        if categories["full_day"]:
            telegram_parts.append("📅 full day jobs")
            for job in categories["full_day"]:
                telegram_parts.append(self.format_job_for_telegram(job))
                telegram_parts.append("")

        Notifier.send_telegram("\n".join(telegram_parts))

        timestamp   = Logger.get_pdt_time().strftime("%Y-%m-%d %I:%M:%S %p")
        email_parts = [f"new jobs -- {timestamp}\n"]
        for job in jobs:
            email_parts.append(self.format_job_for_email(job))
            email_parts.append("")
        Notifier.send_email("\n".join(email_parts), f"new jobs -- {len(jobs)} found")

    # ------------------------------------------------------------------
    # Auto-accept
    # ------------------------------------------------------------------

    def auto_accept_jobs(self, auto_accept_list: List[Tuple[Dict, str]]):
        for job, reason in auto_accept_list:
            job_key  = self.create_job_key(job)
            f        = self.parse_job_fields(job)
            position = f["position"].lower()
            school   = f["school"].lower()

            Logger.log(f"attempting auto-accept: {position} at {school} -- {reason}")

            if self.scraper.accept_job(job):
                self.tracker.mark_as_accepted(job_key)
                Notifier.send_telegram(
                    f"✅ job auto-accepted!\n\n"
                    f"{self.format_job_for_telegram(job)}\n\n"
                    f"reason: {reason}"
                )
                Notifier.send_email(
                    f"auto-accepted: {position} at {school}\nreason: {reason}",
                    f"job accepted -- {position}",
                )
                Logger.log(f"auto-accepted: {position} at {school}")
            else:
                Notifier.send_telegram(
                    f"❌ auto-accept failed\n\n"
                    f"{self.format_job_for_telegram(job)}\n\n"
                    f"reason: {reason}\n"
                    f"please check manually"
                )
                Logger.log(f"failed to auto-accept: {position} at {school}", "ERROR")

    # ------------------------------------------------------------------
    # Main cycle
    # ------------------------------------------------------------------

    def run_check_cycle(self):
        Logger.log("starting check cycle...")

        try:
            self.telegram_handler.check_and_handle_messages()
        except Exception as e:
            Logger.log(f"error checking telegram: {e}", "ERROR")

        if not self.scraper.ensure_logged_in():
            Logger.log("failed to ensure login", "ERROR")
            return

        jobs = self.scraper.fetch_jobs()
        if not jobs:
            Logger.log("no jobs found or fetch failed")
            return

        self.log_all_jobs(jobs)

        new_jobs, auto_accept_list = self.process_jobs(jobs)

        if auto_accept_list:
            Logger.log(f"auto-accepting {len(auto_accept_list)} job(s)")
            self.auto_accept_jobs(auto_accept_list)

        if new_jobs:
            Logger.log(f"sending notifications for {len(new_jobs)} new job(s)")
            self.send_notifications(new_jobs)
        else:
            Logger.log("no new jobs to notify")

    def run(self):
        Logger.log(f"readysub bot {Config.VERSION} starting up...")

        Config.load_lists()
        Config.load_auto_accept_state()

        if not self.scraper.login():
            Logger.log("initial login failed", "ERROR")

        auto_status = "enabled" if Config.AUTO_ACCEPT_ENABLED else "disabled"
        startup_msg = (
            f"bot started -- {Logger.get_pdt_time().strftime('%Y-%m-%d %I:%M:%S %p')}\n"
            f"version: {Config.VERSION}\n"
            f"auto-accept: {auto_status}"
        )
        Notifier.send_telegram(startup_msg)
        Notifier.send_email(startup_msg, "bot startup")

        while True:
            try:
                self.run_check_cycle()
                time.sleep(Config.POLL_INTERVAL)
            except KeyboardInterrupt:
                Logger.log("bot stopped by user")
                break
            except Exception as e:
                Logger.log(f"error in main loop: {e}", "ERROR")
                Logger.log(traceback.format_exc(), "ERROR")
                time.sleep(Config.POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Entry point with auto-restart
# ---------------------------------------------------------------------------

def run_bot_with_restart():
    while True:
        try:
            bot = JobBot()
            bot.run()
            break
        except Exception as e:
            crash_time   = Logger.get_pdt_time().strftime("%Y-%m-%d %I:%M:%S %p")
            error_detail = traceback.format_exc()

            Logger.log(f"bot crashed at {crash_time}: {e}", "CRITICAL")
            Logger.log(error_detail, "CRITICAL")

            crash_msg = f"bot crashed at {crash_time}\n\nerror: {e}\n\n{error_detail}"
            try:
                Notifier.send_email(crash_msg, "bot crash alert")
                Notifier.send_telegram(f"bot crashed -- restarting in {Config.RESTART_DELAY}s")
            except Exception as notify_err:
                print(f"crash notification also failed: {notify_err}")

            Logger.log(f"waiting {Config.RESTART_DELAY}s before restart...")
            time.sleep(Config.RESTART_DELAY)


if __name__ == "__main__":
    run_bot_with_restart()
