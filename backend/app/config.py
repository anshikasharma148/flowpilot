"""Central configuration, read from environment variables (optionally a .env file)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
ALERTS_FILE = Path(os.getenv("ALERTS_FILE", DATA_DIR / "alerts.json"))
PERFORMANCE_FILE = Path(os.getenv("PERFORMANCE_FILE", DATA_DIR / "performance_stats.json"))
DATABASE_URL = os.getenv("DATABASE_URL", "") or f"sqlite:///{BASE_DIR / 'workflow.db'}"
# Hosting providers hand out postgres:// or postgresql:// URLs; use the psycopg (v3) driver for them.
for _prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(_prefix):
        DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(_prefix):]

# LLM -------------------------------------------------------------------------
# LLM_MODE: "auto" (use Claude when credentials exist, else rules), "claude", "rules"
LLM_MODE = os.getenv("LLM_MODE", "auto").lower()
# LLM_PROVIDER: "auto" (Claude if an Anthropic key is set, else Gemini if a Gemini key is set), "anthropic", "gemini"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash")

# Operations defaults ----------------------------------------------------------
OPS_TEAM_EMAIL = os.getenv("OPS_TEAM_EMAIL", "").strip()  # empty -> the assistant asks for an address
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")

# Email -------------------------------------------------------------------------
# EMAIL_PROVIDER: "auto" (Brevo if BREVO_API_KEY is set, else SMTP if SMTP_HOST is set), "brevo", "smtp",
# or "dry_run" (record only, nothing is sent - used by the automated tests).
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "auto").lower()
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "FlowPilot")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "") or os.getenv("EMAIL_FROM", "") or SMTP_USER
EMAIL_FROM = os.getenv("EMAIL_FROM", "") or SMTP_FROM

# Jira Cloud (https://<site>.atlassian.net) ---------------------------------------
JIRA_URL = os.getenv("JIRA_URL", "").rstrip("/")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "OPS")
JIRA_ISSUE_TYPE = os.getenv("JIRA_ISSUE_TYPE", "Task")
