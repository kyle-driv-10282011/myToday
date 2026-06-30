# myToday

A personal dashboard that shows your Outlook calendar, Slack messages, Teams chats, PagerDuty on-call status, stock prices, and RSS feeds — all in one browser tab, served from your local machine.

---

## What the pieces do

### `myToday.exe`
The local web server. It:
- Serves the dashboard UI (`calendar.html`) at `http://localhost:8080`
- Proxies Slack API calls (the browser can't call Slack directly due to CORS)
- Proxies PagerDuty and Finnhub (stocks) API calls
- Optionally auto-pulls the latest `calendar.html` and `feeds.json` from GitHub on a timer

### `calendar.html`
The dashboard UI. It:
- Authenticates with Microsoft (via a popup) to read your Outlook calendar and Teams messages
- Polls the local server every 60 seconds for Slack unread counts
- Shows a countdown to your next meeting with a join link for Teams/Zoom calls
- Displays RSS feeds, stock ticker, and PagerDuty on-call status

---

## End-user setup (receiving the zip from someone else)

### Requirements
- Windows 10 or 11
- A modern browser (Edge or Chrome)
- Internet access for the first-time browser install

### Steps

**1. Unzip**

Extract the `myToday` folder somewhere permanent (e.g. `C:\Users\yourname\myToday`). Do not run it from the zip or from Downloads if files get blocked.

**2. Create your config**

In the `myToday` folder, copy `config.example.py` and rename the copy to `config.py`. Open it in Notepad and fill in your credentials. See the [Config reference](#config-reference) section below.

**3. Create your feeds**

Copy `feeds.example.json` and rename the copy to `feeds.json`. Edit it to add your stock tickers, PagerDuty teams, and RSS feeds. See the [Feeds reference](#feeds-reference) section below.

**4. Run first-time browser install**

Double-click `setup.bat`. This downloads the Chromium browser that myToday uses for the Slack login (~200MB). You only need to do this once.

```
Installing Playwright browser (one-time setup)...
Done. You can now run myToday.exe
```

**5. Start the app**

Double-click `myToday.exe`. You should see:

```
Serving at http://localhost:8080
```

If you see `Port 8080 unavailable, trying 8081...` that is normal — something else on your machine is using that port and the server stepped to the next available one. Note the port number it lands on.

**6. Open the dashboard**

Open your browser and go to `http://localhost:8080` (or whichever port was printed).

**7. Sign in**

- Click **Sign in with Microsoft** to load your Outlook calendar and Teams messages.
- Click the **gear icon** in the Slack column and then **Open Login Browser** to authenticate Slack. A Chromium window will open — sign in and complete MFA. Credentials are captured automatically and held in memory for the session.

> Slack credentials are not saved to disk. You will need to sign in via the gear icon each time you start the app.

---

## Developer setup (building from source)

### Requirements
- Python 3.11 or later
- Windows (PyInstaller builds are platform-specific)
- Git

### Steps

**1. Clone and navigate**

```
git clone https://github.com/kyle-driv-10282011/myToday.git
cd myToday\calendar
```

**2. Create and activate a virtual environment**

```
python -m venv .venv
.venv\Scripts\activate
```

**3. Install dependencies**

```
pip install -r requirements.txt
playwright install chromium
```

**4. Configure**

Copy `config.example.py` to `config.py` and fill in your credentials.

**5. Run directly**

```
python server.py
```

Then open `http://localhost:8080`.

### Building the distributable

Run `build.bat` from the `calendar` folder. It cleans any previous build, installs PyInstaller, and produces `dist\myToday\`.

```
build.bat
```

Zip up `dist\myToday\` and distribute.

---

## Config reference

All settings live in `config.py` alongside the exe. A template is provided in `config.example.py`.

| Key | Description | Where to get it |
|-----|-------------|-----------------|
| `PROXY_SECRET` | Random string that secures the local Slack proxy | Generate with: `python -c "import secrets; print(secrets.token_hex(16))"` |
| `SLACK_WORKSPACE_ID` | Your Slack workspace ID (`T0000000`) | Visible in any Slack URL: `slack.com/client/T0000000/...` |
| `SLACK_WORKSPACE_DOMAIN` | Your Slack domain | e.g. `yourcompany.slack.com` |
| `GITHUB_PAT` | GitHub Personal Access Token | github.com → Settings → Developer settings → Personal access tokens (repo:read scope) |
| `GITHUB_API_URL` | GitHub API URL to `calendar.html` in your repo | See `config.example.py` for the format |
| `GITHUB_FEEDS_URL` | GitHub API URL to `feeds.json` in your repo | See `config.example.py` for the format |
| `PAGERDUTY_TOKEN` | PagerDuty API token | PagerDuty → Integrations → API Access Keys → Create New API Key |
| `FINNHUB_KEY` | Finnhub API key for stock prices | finnhub.io → Dashboard (free tier works) |
| `MS_CLIENT_ID` | Azure app registration Client ID | portal.azure.com → Azure Active Directory → App registrations → your app → Overview |
| `MS_TENANT_ID` | Azure AD Tenant ID | Same page as Client ID |
| `AUTO_PULL` | `True` to auto-sync `calendar.html` and `feeds.json` from GitHub on startup | Set to `False` to use local files only |
| `PULL_INTERVAL` | Seconds between background GitHub sync checks | Default: `30` |
| `PORT` | Port the server listens on | Default: `8080`. Change if that port is in use on your machine. |

## Feeds reference

All feeds configuration lives in `feeds.json` alongside the exe. A template is provided in `feeds.example.json` — copy it to `feeds.json` and customize it.

```json
{
  "stocks": ["AAPL", "MSFT"],
  "pagerduty": [
    {"name": "Your Team", "schedule": "XXXXXXX", "service": "XXXXXXX"}
  ],
  "feeds": [
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"}
  ]
}
```

| Key | Description |
|-----|-------------|
| `stocks` | List of ticker symbols shown in the stock ticker |
| `pagerduty` | List of teams — `name` is display name, `schedule` and `service` are PagerDuty IDs found in the PagerDuty URL when viewing that schedule/service |
| `feeds` | List of RSS/Atom feeds — `name` is display name, `url` is the feed URL |

---

## Troubleshooting

**Port already in use**
The server will automatically try the next port (up to 20 attempts). Check the console output for the actual port and use that URL.

**Slack shows "Not signed in"**
Click the gear icon in the Slack column and use **Open Login Browser**. Credentials are only held for the current session.

**`setup.bat` opens and closes immediately**
Right-click `setup.bat` and choose **Run as administrator**, or open a PowerShell window in the `myToday` folder and run `.\setup.bat` to see any error output.

**Windows Defender / antivirus warning**
PyInstaller executables are sometimes flagged as suspicious because they self-extract. The exe contains only Python and the packages listed in `requirements.txt`. You may need to add an exception in your antivirus software.
