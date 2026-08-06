# Generate any random string (e.g. python3 -c "import secrets; print(secrets.token_hex(16))")
PROXY_SECRET         = 'your-proxy-secret-here'

# Slack workspace ID — visible in any Slack URL: slack.com/client/T0000000/...
SLACK_WORKSPACE_ID     = 'T0000000'

# Slack workspace domain — e.g. yourcompany.slack.com
SLACK_WORKSPACE_DOMAIN = 'yourworkspace.slack.com'

# GitHub Personal Access Token — github.com → Settings → Developer settings → Personal access tokens
# Needs repo:read scope on the myToday repo
GITHUB_PAT           = 'github_pat_...'

# GitHub API URLs for auto-pull — path to calendar.html and feeds.json in the repo
GITHUB_API_URL       = 'https://api.github.com/repos/your-username/myToday/contents/calendar.html'
GITHUB_FEEDS_URL     = 'https://api.github.com/repos/your-username/myToday/contents/feeds.json'

# PagerDuty API token — PagerDuty → Integrations → API Access Keys → Create New API Key
PAGERDUTY_TOKEN      = 'your-pagerduty-token'

# PagerDuty teams — schedule and service IDs found in the PagerDuty URL for each schedule/service
PAGERDUTY_TEAMS      = [
    {'name': 'Your Team', 'schedule': 'XXXXXXX', 'service': 'XXXXXXX'},
]

# Finnhub API key — finnhub.io → Dashboard → API Key (free tier works)
FINNHUB_KEY          = 'your-finnhub-api-key'

# Azure app registration — portal.azure.com → Azure Active Directory → App registrations
# Client ID and Tenant ID are on the app's Overview page
MS_CLIENT_ID         = 'your-azure-app-client-id'
MS_TENANT_ID         = 'your-azure-ad-tenant-id'

AUTO_PULL            = True   # set to False to serve local files without fetching from GitHub
PULL_INTERVAL        = 30     # seconds between background sync checks
PORT                 = 8080   # change if 8080 is already in use on your machine
