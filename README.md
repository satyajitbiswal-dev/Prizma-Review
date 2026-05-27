# 🤖 AI Code Reviewer

> A GitHub App that automatically reviews pull requests for DSA anti-patterns, algorithmic inefficiencies, and security issues — posting inline comments like a senior engineer.

[![Stack](https://img.shields.io/badge/Stack-Django%20%2B%20Celery%20%2B%20Redis-blue)](/)
[![AI](https://img.shields.io/badge/AI-Claude%20Sonnet-purple)](/)
[![License](https://img.shields.io/badge/License-MIT-green)](/)

---

## What it does

When a developer opens or updates a pull request, this app:

1. Receives the GitHub webhook event
2. Fetches the PR diff using the GitHub API
3. Sends it to Claude with a DSA-aware prompt
4. Posts **inline review comments** directly on the flagged lines
5. Adds a **summary comment** at the top of the PR with a health score and top issues

All asynchronously — the webhook returns in under 50ms.

---

## Features

- **O(n²) loop detection** — flags nested loops and suggests optimized alternatives
- **Missing memoization** — catches recursive functions without caching
- **Wrong data structures** — e.g. `list.contains()` where a `set` would be O(1)
- **Time complexity annotations** — Big-O analysis on every flagged function
- **Security scanning** — hardcoded secrets, SQL injection, missing auth checks
- **Severity levels** — `CRITICAL` / `WARNING` / `SUGGESTION`
- **Multi-language** — Python, JavaScript, Java
- **Educational comments** — every issue explains *why* it matters and shows the fix

---

## Tech Stack

| Component | Technology |
|---|---|
| Backend | Django 5 + Django REST Framework |
| Task Queue | Celery + Redis |
| GitHub Integration | PyGithub + GitHub Webhooks |
| AI | Claude Sonnet (Anthropic API) |
| Database | PostgreSQL |
| Deployment | Railway |
| Dashboard | Django Templates + HTMX |

---

## How It Works

```
PR Opened on GitHub
        │
        ▼
POST /api/webhooks/github/   (Django — validates HMAC, returns 200 in <50ms)
        │
        ▼
Redis Queue ──► Celery Worker
                    │
                    ├─ Fetch diff (PyGithub)
                    ├─ Chunk by file
                    ├─ Send to Claude API
                    └─ Post inline comments + summary (GitHub Review API)
```

---

## Installation

### 1. Clone and install dependencies

```bash
git clone https://github.com/your-username/ai-code-reviewer.git
cd ai-code-reviewer
pip install -r requirements.txt
```

### 2. Start local services

```bash
docker-compose up -d        # PostgreSQL + Redis
python manage.py migrate
python manage.py runserver
```

Start the Celery worker in a separate terminal:

```bash
celery -A reviewer worker --loglevel=info
```

### 3. Configure environment variables

Create a `.env` file:

```env
GITHUB_APP_ID=your_app_id
GITHUB_PRIVATE_KEY=path/to/private-key.pem
GITHUB_WEBHOOK_SECRET=your_webhook_secret
ANTHROPIC_API_KEY=your_anthropic_key
DATABASE_URL=postgresql://user:pass@localhost:5432/reviewer
REDIS_URL=redis://localhost:6379/0
```

### 4. Register the GitHub App

1. Go to **GitHub → Settings → Developer Settings → GitHub Apps → New GitHub App**
2. Set webhook URL to your server (or use [Smee.io](https://smee.io) for local dev)
3. Permissions: **Pull requests** (read/write), **Contents** (read)
4. Subscribe to event: `pull_request`
5. Download the private key and set it as `GITHUB_PRIVATE_KEY`

---

## Project Structure

```
reviewer/
├── apps/
│   ├── webhooks/        # Webhook endpoint, HMAC validation, task dispatch
│   ├── reviews/         # Models: Repo, PullRequest, Review, Comment
│   ├── analyzer/        # Claude API client, prompt builder, DSA rules
│   ├── github_client/   # PyGithub wrapper, diff fetcher, comment poster
│   └── dashboard/       # Review history dashboard
├── tasks/               # Celery tasks
├── config/settings/     # base / local / production
└── docker-compose.yml
```

---

## Local Development with Smee

To receive GitHub webhooks on localhost:

```bash
npm install -g smee-client
smee --url https://smee.io/your-channel --target http://localhost:8000/api/webhooks/github/
```

Set your GitHub App's webhook URL to your Smee channel URL.

---

## License

MIT
