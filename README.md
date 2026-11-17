# emcache-back-end

Backend for **emcache**, a financial reconciliation system with multimodal ingestion via text, audio, and image.

## Stack

- **Database:** PostgreSQL 16 (`postgres:16-alpine`) via Docker Compose
- **CI/CD:** GitHub Actions (`.github/workflows/deploy.yml`) deploying over SSH to Ubuntu 22.04 LTS

## Project Structure

```text
emcache-back-end/
├── .github/workflows/
│   └── deploy.yml       # Validate Compose config & deploy to VPS on push to main
├── backend/             # Application source (FastAPI service to be added)
├── db/
│   └── init.sql         # PostgreSQL schema & indexes (uuid-ossp, transactions)
├── .env.example         # Environment variable template
├── .gitignore
├── docker-compose.yml   # Local & VPS PostgreSQL service
└── README.md
```

## Local Setup

1. **Copy the environment template and set a strong password:**
   ```bash
   cp .env.example .env
   ```

2. **Start PostgreSQL:**
   ```bash
   docker compose up -d
   ```

3. **Verify the database is running and initialized:**
   ```bash
   docker compose ps
   docker exec -it emcache_db psql -U dev_user -d emcache -c "\d transactions"
   ```

   PostgreSQL is bound strictly to `127.0.0.1:5432` and persists data in the `pgdata` Docker volume.

## VPS Automated Deployment

Pushes to `main` trigger `.github/workflows/deploy.yml`:
1. **`validate`**: Checks out the repository and runs `docker compose config --quiet`.
2. **`deploy`**: Connects to the VPS via SSH (`appleboy/ssh-action@v1.0.3`), pulls `main` in `~/emcache-back-end`, rebuilds/restarts services with `docker compose up -d --build --remove-orphans`, and cleans up unused images with `docker image prune -f`.

### Required GitHub Repository Secrets
- `VPS_HOST` — VPS IP address
- `VPS_PORT` — SSH port (`22022`)
- `VPS_USER` — SSH user (`dev`)
- `VPS_SSH_KEY` — Private SSH deploy key authorized on the VPS
