# InfinityRouter Suite & 9Router Integration Tool

A lightweight management toolkit for **InfinityRouter** (`https://infinityrouter.dev/v1`):
- **Health & Audit Engine**: Concurrently audits accounts via `/v1/models` and live `/v1/chat/completions`.
- **9Router Sync & Load Balancing**: Imports and registers API keys directly into 9router connection pool (`/var/lib/9router/db/data.sqlite`).
- **Format Converter**: Parses account dumps and exports to JSON or CSV.
- **Pre-configured Models**: `minimax-m3`, `deepseek-v4.1-flash-1017`.

---

## Quick Start

```bash
# Audit all accounts in accounts.txt concurrently
python3 ir_suite.py --check

# Sync all active accounts into 9router connection pool
python3 ir_suite.py --sync-9router --prefix ir

# Test query through 9router
python3 ir_suite.py --test-chat "Halo"

# Export accounts to JSON / CSV
python3 ir_suite.py --export json
python3 ir_suite.py --export csv
```

---

## 9Router Routing

Once synced, models are callable through 9router under the configured prefix:
- `ir/minimax-m3`
- `ir/deepseek-v4.1-flash-1017`

```bash
curl -X POST http://127.0.0.1:20127/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <9ROUTER_KEY>" \
  -d '{
    "model": "ir/deepseek-v4.1-flash-1017",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## License

MIT License.
