# Berlin Philharmonic Ticket Monitor for EIF 2026 🎫

**English** | [简体中文](README.zh-CN.md)

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows11&logoColor=white)](https://www.microsoft.com/windows/)
[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Get notified when standard tickets reappear for two sold-out Berliner Philharmoniker concerts at the 2026 Edinburgh International Festival.

This conservative Windows monitor checks official EIF availability data and alerts you by Gmail, desktop notification, sound, and browser. It does **not** log in, select seats, reserve tickets, bypass website protection, or purchase anything automatically.

## Why this monitor?

- Checks official, publicly accessible EIF ticket-availability signals.
- Distinguishes standard public tickets from Access-only, wheelchair, and essential-companion seats.
- Requires independent availability signals to agree before reporting tickets.
- Sends Gmail, Windows desktop, sound, and browser alerts.
- Prevents duplicate notifications while the same inventory remains available.
- Uses per-performance retry backoff for HTTP 403/429, queues, and verification pages.
- Treats unclear or changed website responses as `UNKNOWN` instead of risking a false alert.

## Monitored performances

| Date and time (UK) | Performance |
| --- | --- |
| 29 August 2026, 19:00 | Berliner Philharmoniker: Elgar & Tchaikovsky |
| 30 August 2026, 19:30 | Berliner Philharmoniker: Closing Concert |

The monitor is purpose-built for these two EIF performances. Ticket availability can change between checks; always confirm and complete the purchase yourself on the official website.

## Quick start

### 1. Prerequisites

- Windows 10 or 11
- Python 3.11 or later (64-bit), with **Add python.exe to PATH** enabled
- A Gmail account with two-step verification and an [app password](https://support.google.com/accounts/answer/185833)

### 2. Download and configure

Clone the repository:

```powershell
git clone https://github.com/carrotProgrammer/berlin-philharmonic-ticket-monitor.git
cd berlin-philharmonic-ticket-monitor
```

Double-click `start_monitor.bat`, or run:

```powershell
.\start_monitor.bat
```

On the first launch, the script creates `.venv`, installs dependencies and Playwright Chromium, copies `.env.example` to `.env`, and opens the configuration file. Add your notification settings:

```dotenv
GMAIL_ADDRESS=your-address@gmail.com
GMAIL_APP_PASSWORD=your-16-character-app-password
RECIPIENT_EMAIL=recipient@example.com
```

The local `.env` file is ignored by Git. Never commit or share it.

### 3. Verify notifications

Double-click `test_notification.bat`, or run:

```powershell
.venv\Scripts\python.exe monitor.py --test-notification
```

This sends a test email, shows a desktop notification, plays a sound, and opens the official booking page.

### 4. Check once or start monitoring

Run one non-notifying availability check:

```powershell
.\check_once.bat
```

Start continuous monitoring:

```powershell
.\start_monitor.bat
```

Keep the computer awake, online, and the console window open. Press `Ctrl+C` to stop cleanly and save state.

## How availability is decided

The monitor reads two public data sources used by the EIF website:

1. The event availability response must report ordinary inventory above zero and a publicly bookable instance.
2. The matching performance response must independently report ordinary inventory above zero.
3. The event page must expose the correct public booking path when a browser fallback is required.

Eligibility-restricted inventory such as Access Pass, wheelchair, and Essential Companion seats is not counted as standard availability. If the sources disagree, the response structure changes, or the evidence is insufficient, the result is `UNKNOWN` and no ticket alert is sent.

## Notifications and state

Each performance maintains independent status, retry backoff, and notification deduplication. After a confirmed alert, the monitor pauses that performance for five minutes while continuing to check the other one. A new alert is sent only after inventory has clearly returned to unavailable and then becomes available again.

Runtime data is stored locally:

- `state.json` records the latest status and notification state.
- `logs\monitor.log` contains rotating diagnostic logs without passwords, cookies, or login tokens.

Both paths are excluded from Git.

## Website protection and responsible use

The monitor does not bypass HTTP 403/429 responses, CAPTCHA, Cloudflare checks, waiting rooms, or rate limits. It backs off for 1, 2, 5, and 10 minutes when protection is detected. Network and temporary server failures are recorded as `UNKNOWN` and retried later.

Do not reduce the polling interval to a few seconds or modify the project to evade website controls.

## Project structure

| Path | Purpose |
| --- | --- |
| `monitor.py` | Availability, state, notification, and retry logic |
| `.env.example` | Safe configuration template |
| `start_monitor.bat` | First-time setup and continuous monitoring |
| `test_notification.bat` | Email, desktop, sound, and browser notification test |
| `check_once.bat` | One safe availability check |
| `tests\test_monitor.py` | Availability, deduplication, failure, and launcher tests |

## Tests

```powershell
python -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)

---

This is an independent personal notification tool and is not affiliated with Berliner Philharmoniker, Edinburgh International Festival, or Spektrix.
