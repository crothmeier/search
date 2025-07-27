# ChatGPT Conversation Search

A high-performance search application for ChatGPT conversation exports, featuring streaming JSON parsing, SQLite FTS5 full-text search, REST API, and web UI.

## Features

- **Streaming JSON Parser**: Handles large ChatGPT export files (560MB+) without loading entire file into memory
- **Full-Text Search**: SQLite FTS5 with Porter stemming for fast, accurate search
- **REST API**: FastAPI with Prometheus metrics and health checks
- **Web UI**: Streamlit interface with search history and export capabilities
- **Docker Deployment**: Easy containerized deployment with docker-compose
- **Automated Backups**: Scheduled backups to secondary node with integrity checks

## Quick Start

### 1. Clone and Setup

```bash
git clone <repository>
cd search
cp .env.example .env
```

### 2. Import ChatGPT Data

```bash
# Install ETL dependencies
pip install -r requirements-etl.txt

# Import your ChatGPT export
python scripts/import_chatgpt.py /path/to/conversations.json

# Or with custom options
python scripts/import_chatgpt.py /path/to/conversations.json \
  --db-path data/conversations.db \
  --batch-size 1000 \
  --clean  # Remove existing database
```

### 3. Run with Docker

```bash
# Build and start services
docker-compose up -d

# View logs
docker-compose logs -f

# Access services
# API: http://localhost:8000
# UI: http://localhost:8501
```

### 4. Run Locally (Development)

```bash
# API
pip install -r requirements-api.txt
cd api && python main.py

# UI (in another terminal)
pip install -r requirements-ui.txt
streamlit run ui/app.py
```

## API Endpoints

- `GET /health` - Health check
- `GET /search?q={query}&limit=20&offset=0` - Search conversations
- `GET /conversations/{id}` - Get full conversation
- `GET /stats` - Database statistics
- `GET /metrics` - Prometheus metrics
- `GET /suggest?q={partial}` - Query suggestions

### Search Syntax

- Simple search: `machine learning`
- Phrase search: `"exact phrase"`
- Boolean: `python AND code`, `javascript OR typescript`
- Prefix: `pyth*`

## Configuration

Environment variables (see `.env.example`):

```bash
DATABASE_PATH=/app/data/conversations.db
API_PORT=8000
UI_PORT=8501
BACKUP_HOST=phx-ai01
BACKUP_PATH=/mnt/backups/chatgpt
LOG_LEVEL=INFO
BATCH_SIZE=1000
```

## Backup

Set up automated backups with cron:

```bash
# Add to crontab
0 2 * * * /path/to/search/scripts/backup_db.sh

# Manual backup
./scripts/backup_db.sh
```

## Testing

```bash
# Test API endpoints
python scripts/test_search.py --api-url http://localhost:8000

# Save test results
python scripts/test_search.py --output test-results.json
```

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│  Streamlit  │────▶│  FastAPI    │────▶│   SQLite     │
│     UI      │     │    API      │     │  FTS5 DB     │
└─────────────┘     └─────────────┘     └──────────────┘
                            │
                            ▼
                    ┌──────────────┐
                    │  Prometheus  │
                    │   Metrics    │
                    └──────────────┘
```

## Performance

- Streaming parser: Constant memory usage regardless of file size
- FTS5 search: Sub-second queries on millions of messages
- Batch inserts: 1000 records per transaction
- Database optimization: Automatic VACUUM and ANALYZE after import

## Troubleshooting

### Import Issues

```bash
# Check file format
head -n 100 conversations.json | jq .

# Verify database
sqlite3 data/conversations.db "SELECT COUNT(*) FROM conversations;"
```

### Search Issues

```bash
# Test FTS5
sqlite3 data/conversations.db "SELECT * FROM conversations WHERE conversations MATCH 'test' LIMIT 5;"

# Check indexes
sqlite3 data/conversations.db ".schema"
```

### Docker Issues

```bash
# Rebuild containers
docker-compose build --no-cache

# Check container logs
docker logs chatgpt-search-api
docker logs chatgpt-search-ui
```

## Development

### Project Structure

```
search/
├── src/               # Core libraries
│   ├── streaming_parser.py
│   ├── database.py
│   └── search.py
├── api/               # FastAPI application
│   └── main.py
├── ui/                # Streamlit UI
│   └── app.py
├── scripts/           # Utility scripts
│   ├── import_chatgpt.py
│   ├── backup_db.sh
│   └── test_search.py
└── data/              # SQLite database
```

### Adding Features

1. Search enhancements: Edit `src/search.py`
2. New API endpoints: Edit `api/main.py`
3. UI improvements: Edit `ui/app.py`
4. Database schema: Edit `src/database.py`

## License

MIT License - See LICENSE file for details