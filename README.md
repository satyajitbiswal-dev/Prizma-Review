# 🤖 Prizmareview — AI Code Review GitHub App

> A GitHub App that automatically reviews every pull request for DSA anti-patterns, security vulnerabilities, and algorithmic inefficiencies — posting inline comments like a senior engineer, directly on the flagged lines.

[Stack](/)
[AI](/)
[License](/)

---

## What It Does

When a developer opens or updates a pull request, Prizmareview:

1. Receives the GitHub webhook event instantly
2. Posts a `⏳ pending` commit status — developer sees the spinner immediately
3. Fetches the PR diff using the GitHub API (with pagination for large PRs)
4. Sanitizes the diff — skips deleted files, binary files, generated files, lock files
5. Sends each file chunk to an LLM with a DSA + Security aware prompt
6. Posts **inline review comments** directly on the flagged lines
7. Posts a **summary comment** at the top of the PR with a health score breakdown
8. Updates the commit status to ✅ or ❌ based on the score threshold

All asynchronously — the webhook returns in under 50ms via Celery + Redis.

---

## Demo

> Open a PR with an O(n²) loop or a hardcoded API key → Prizmareview posts inline comments within seconds.

```
🔴 CRITICAL — DSA Issue

Problem: Recursive fibonacci function called with repeated arguments, no cache

Fix: Implement memoization using a dictionary to store previously computed values

⚡ Complexity: O(2^n) → O(n)

💡 Suggested Fix

- return fibonacci(n-1) + fibonacci(n-2)
+ memo = {0: 0, 1: 1}
+ def fibonacci(n, memo=memo):
+     if n not in memo: memo[n] = fibonacci(n-1, memo) + fibonacci(n-2, memo)
+     return memo[n]
```

---

## Features

### DSA Detection

- **O(n²) loop detection** — nested loops over the same collection → suggests O(n log n) or O(n) alternatives
- **Missing memoization** — recursive functions with repeated arguments and no cache
- **Wrong data structures** — `list.count()`, `in list`, `list.index()` where set/dict gives O(1)
- **Unnecessary sorting** — sorting just to find min/max/first element
- **Repeated computation** — same expensive call inside a loop (len(), DB query, regex compile)
- **Sliding window / two-pointer misuse** — brute force when O(n) pattern applies
- **Stack/Queue misuse** — `list.insert(0, x)` instead of `collections.deque`
- **Missing early exit** — looping entire collection when answer found early

### Security Scanning

- **Hardcoded secrets** — API keys, tokens, passwords, private keys in source code
- **SQL injection** — raw string formatting in queries instead of parameterized queries
- **Command injection** — `os.system()`, `subprocess` with `shell=True` + user input
- **Unsafe deserialization** — `pickle.loads()`, `yaml.load()` on untrusted data (RCE risk)
- **Cryptography misuse** — MD5/SHA1 for passwords, `random` instead of `secrets`, AES-ECB
- **Auth issues** — JWT decoded without verification, HMAC compared with `==` (timing attack)
- **Input validation gaps** — path traversal, missing bounds checking, spoofable headers

### Reliability Issues

- **Resource leaks** — unclosed files, DB connections not closed in finally block
- **Silent error swallowing** — bare `except:` clauses hiding real errors
- **Concurrency issues** — shared mutable state across threads

### Product Features

- **💡 Suggested Fix** — LLM generates the actual corrected code as a diff block
- **PR Health Score** — 0–100 score with CRITICAL/WARNING/SUGGESTION breakdown
- **Commit Status Check** — ✅/❌ appears directly on the PR, blockable on merge
- `**prizmareview: recheck`** — comment on any PR to trigger a fresh review (bot reacts with 👀)
- `**.prizmareview.yml`** — per-repo config for thresholds, skip paths, language focus
- **README Health Badge** — embed a live score badge in your repo README
- **Multi-language** — Python, JavaScript, TypeScript, Java, Go, Rust, and more
- **Severity levels** — `CRITICAL` / `WARNING` / `SUGGESTION` with noise filtering
- **Partial review notes** — large files (300+ changed lines) are truncated with a note

---

## Tech Stack


| Component          | Technology                                               |
| ------------------ | -------------------------------------------------------- |
| Backend Framework  | Django 5 + Django REST Framework                         |
| Task Queue         | Celery + Redis                                           |
| GitHub Integration | GitHub App API + Webhooks                                |
| AI Engine          | Gemini 2.0 Flash / OpenRouter / OpenAI (tiered rotation) |
| Database           | SQLite (dev)                                             |
| Dashboard          | Django Templates + HTMX + Chart.js                       |
| Auth               | GitHub OAuth via GitHub App                              |


---

## How It Works

```
PR Opened on GitHub
        │
        ▼
POST /api/webhooks/github/
(Django DRF — validates HMAC-SHA256, posts ⏳ pending status, returns 200 in <50ms)
        │
        ▼
Redis Queue ──► Celery Worker
                    │
                    ├─ Fetch .prizmareview.yml config (or use defaults)
                    ├─ Fetch PR diff via GitHub API (paginated)
                    ├─ Sanitize: skip deleted, binary, generated, test files
                    ├─ Chunk by file (skip files > 300 changed lines with note)
                    ├─ Analyze each chunk via LLM (tiered key rotation)
                    ├─ Save issues to DB (bulk insert)
                    ├─ Post inline comments + summary via GitHub Review API
                    └─ Update commit status ✅ / ❌
```

---

## LLM Key Rotation

Prizmareview uses a **3-tier cascading key rotation** system across 12 API keys:

```
Tier 1 (OpenRouter × 4) ──► Tier 2 (Gemini × 4) ──► Tier 3 (OpenAI × 4)
```

- **429 hit** → key goes into Redis penalty box for 5 minutes → next key used immediately
- **401/403 hit** → key permanently disabled in DB (needs manual re-activation)
- **All tier exhausted** → cascades to next tier automatically
- Keys managed securely via Django admin — encrypted at rest using `django-encrypted-model-fields`
- No server restart needed to add/remove keys

---

## Per-Repo Configuration

Drop a `.prizmareview.yml` file in your repo root to customize behavior:

```yaml
# .prizmareview.yml

# Health score below this = ❌ commit status fails (default: 50)
fail_threshold: 60

# Max inline comments per PR (default: 15)
max_comments: 10

# Skip entire categories
skip_categories:
  - SUGGESTION

# Skip specific paths (supports glob patterns)
skip_paths:
  - "legacy/*"
  - "vendor/*"
  - "*.generated.py"

# Only review these languages (empty = review all)
language_focus:
  - python
  - typescript

# Review test files? (default: false)
review_tests: false
```

Zero config needed — all defaults work out of the box.

---

## Recheck Command

Comment `prizmareview: recheck` on any PR to trigger a fresh review:

```
@you: prizmareview: recheck
👀 (bot reacts)
... fresh review posted within seconds
```

---

## README Badge

Embed a live health score badge in your repo:

```markdown
![Prizmareview](https://yourapp.com/badge/owner/repo-name)
```

Renders as a green/yellow/red badge showing average score of last 10 PRs.

---

## Installation

### 1. Clone and install dependencies

```bash
git clone https://github.com/your-username/prizmareview.git
cd prizmareview
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file:

```env
# Django
SECRET_KEY=your-django-secret-key
DEBUG=True

# GitHub App
GITHUB_APP_ID=your_app_id
GITHUB_PRIVATE_KEY_PATH=/path/to/private-key.pem
GITHUB_WEBHOOK_SECRET=your_webhook_secret
GITHUB_CLIENT_ID=your_oauth_client_id
GITHUB_CLIENT_SECRET=your_oauth_client_secret

# LLM Keys (add via Django admin after setup)
FIELD_ENCRYPTION_KEY=your-fernet-key

# Redis
REDIS_URL=redis://localhost:6379/0

# Database 
```

Generate your encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Run migrations and start server

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Start Celery worker in a separate terminal:

```bash
celery -A prizmareview worker --loglevel=info
```

### 4. Add LLM keys via Django Admin

Go to `http://localhost:8000/admin` → **LLM Key Ring Slots** → Add keys:


| Account         | Provider           | Key       |
| --------------- | ------------------ | --------- |
| openrouter_acc1 | Tier 1: OpenRouter | sk-or-... |
| openai_acc1     | Tier 2: OpenAI     | sk-...    |
| gemini_acc1     | Tier 3: Gemini     | AIzaSy... |


### 5. Register the GitHub App

1. Go to **GitHub → Settings → Developer Settings → GitHub Apps → New GitHub App**
2. Set webhook URL to your server (use [Smee.io](https://smee.io) for local dev)
3. Set callback URL to `http://localhost:8000/auth/github/callback/`
4. Permissions needed:
  - Pull requests → Read & Write
  - Contents → Read
  - Commit statuses → Read & Write
  - Issue comments → Read & Write (for `/recheck`)
5. Subscribe to events: `pull_request`, `issue_comment`
6. Download the private key `.pem` file

### 6. Forward webhooks to localhost (dev)

```bash
npm install -g smee-client
smee --url https://smee.io/your-channel --target http://localhost:8000/api/webhooks/github/
```

---

## Project Structure

```
prizmareview/
├── accounts/
│   ├── models.py                 # GithubProfile Model
│   ├── context_processor.py      # Expose GitHub profile + avatar safely on every template
│   └── views.py                  # login,logout view
├── analyzer/
│   ├── providers/
│   │   ├── base.py          # Abstract provider interface
│   │   ├── gemini.py        # Gemini 2.0 Flash provider
│   │   ├── openai.py        # OpenAI provider
│   │   └── openrouter.py    # OpenRouter provider
│   ├── rotator.py           # Tiered Redis-backed key rotation
│   ├── llm_client.py        # analyze_chunk — routes to correct provider
│   ├── prompts.py           # DSA + Security system prompt
|   ├── models.py            # LLKeyRingSlot Model 
|   └── repo_config.py       # .prizmareview.yml fetcher
├── github_client/
│   ├── gh_client.py         # GitHub App auth, diff fetcher (paginated)
│   ├── diff_sanitizer.py    # Skip deleted/binary/generated files
│   ├── comment_poster.py    # Hunk parser, inline comments, summary
│   ├── installations.py     # Sync GitHub App installations and repositories 
│   └── status_poster.py     # Commit status (pending/success/failure/error)
├── reviews/
│   └── models.py            # Repo, PullRequest, Review, Comment
├── webhooks/
│   └── views.py             # HMAC validation, webhook router, /recheck handler
├── templates/
│   ├── registration/         # OAuth page
|   ├── dashboard/            # dashboard, repo detail, review detail, badge page
│   └── 404.html              #The 404 page
├── tasks/
│   └── review_tasks.py      # Main Celery task — full pipeline
└── config/
    └── settings/            # base / local / production
```

---

## Dashboard

The web dashboard shows:

- **Repository fleet** — all repos with avg score, total PRs, critical issue count
- **PR history** — all reviewed PRs with health scores and status
- **Review detail** — file-by-file breakdown with inline issues, complexity badges, suggested fix diffs
- **Health score trend chart** — line graph of last 10 PR scores per repo
- **README badge generator** — copy-paste embed code

Login with your GitHub account via OAuth — no separate signup needed.

## License

MIT
EOF