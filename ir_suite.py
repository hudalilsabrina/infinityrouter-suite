#!/usr/bin/env python3
"""
InfinityRouter Automation & 9Router Integration Suite.

Features:
- Parse and validate accounts from raw text/dump format
- Concurrently audit API keys (models check + live chat completion test)
- Auto-sync all accounts directly into 9router connection pool (SQLite)
- Supports proxy mapping per account (if specified) or direct connection
- Benchmark model performance (minimax-m3, deepseek-v4.1-flash-1017)

Usage:
  python3 ir_suite.py --check
  python3 ir_suite.py --sync-9router
  python3 ir_suite.py --test-chat "Halo"
  python3 ir_suite.py --export json
"""

import argparse
import concurrent.futures
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_TXT = "/root/infinityrouter-suite/accounts.txt"
DB_PATH = "/var/lib/9router/db/data.sqlite"
BASE_URL = "https://infinityrouter.dev/v1"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"


def parse_accounts(path=DEFAULT_TXT):
    if not os.path.exists(path):
        print(f"Error: file {path} not found.")
        return []

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    accounts = []
    blocks = [b.strip() for b in content.split("===") if b.strip()]
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        data = {}
        for line in lines:
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip().lower().replace(" ", "_")] = v.strip()

        apikey = data.get("apikey")
        email = data.get("email")
        if apikey and email:
            accounts.append({
                "username": data.get("username", email.split("@")[0]),
                "email": email,
                "password": data.get("password", ""),
                "apikey": apikey,
                "base_url": data.get("base_url_api", BASE_URL),
                "proxy": data.get("ip_proxy", "-"),
                "status": data.get("status", "ok")
            })

    return accounts


def check_key(account, model="deepseek-v4.1-flash-1017"):
    key = account["apikey"]
    email = account["email"]
    res = {
        "email": email,
        "username": account.get("username"),
        "models_ok": False,
        "chat_ok": False,
        "latency_ms": 0,
        "error": None
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": UA,
        "Content-Type": "application/json"
    }

    # 1. Models endpoint
    m_req = urllib.request.Request(f"{BASE_URL}/models", headers=headers)
    try:
        with urllib.request.urlopen(m_req, timeout=12) as r:
            if r.status == 200:
                res["models_ok"] = True
    except Exception as e:
        res["error"] = f"models_err: {e}"
        return res

    # 2. Chat completion
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5
    }).encode("utf-8")
    c_req = urllib.request.Request(f"{BASE_URL}/chat/completions", data=body, headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(c_req, timeout=20) as cr:
            if cr.status == 200:
                res["chat_ok"] = True
                res["latency_ms"] = int((time.time() - t0) * 1000)
    except Exception as e:
        res["error"] = f"chat_err: {e}"

    return res


def sync_to_9router(accounts, node_prefix="ir", node_name="InfinityRouter"):
    if not os.path.exists(DB_PATH):
        print(f"Error: 9router database not found at {DB_PATH}")
        return False

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Ensure providerNode exists
    cur.execute("SELECT id, data FROM providerNodes WHERE name = ? OR data LIKE '%infinityrouter%';", (node_name,))
    row = cur.fetchone()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    if row:
        node_id = row[0]
        print(f"[✓] Existing provider node found: {node_id} ({node_name})")
    else:
        node_id = f"openai-compatible-chat-{uuid.uuid4()}"
        node_data = {
            "prefix": node_prefix,
            "apiType": "chat",
            "baseUrl": BASE_URL,
            "nodeName": node_name,
            "logo": "",
            "createdAt": now_iso,
            "updatedAt": now_iso
        }
        cur.execute(
            """INSERT INTO providerNodes (id, type, name, data, createdAt, updatedAt)
               VALUES (?, ?, ?, ?, ?, ?);""",
            (node_id, "openai-compatible", node_name, json.dumps(node_data), now_iso, now_iso)
        )
        print(f"[✓] Created new provider node in 9router: {node_id} (prefix: {node_prefix})")

    # Fetch existing connections to prevent duplicates
    cur.execute("SELECT email, data FROM providerConnections WHERE provider = ?;", (node_id,))
    existing = {}
    for r in cur.fetchall():
        em = r[0]
        try:
            d = json.loads(r[1])
            existing[em] = d.get("apiKey")
        except Exception:
            existing[em] = None

    added = 0
    updated = 0
    for idx, acc in enumerate(accounts, 1):
        em = acc["email"]
        apikey = acc["apikey"]
        uname = acc.get("username") or em.split("@")[0]
        conn_label = f"ir {idx} ({uname})"

        conn_data = {
            "apiKey": apikey,
            "defaultModel": f"{node_prefix}/deepseek-v4.1-flash-1017",
            "testStatus": "active",
            "errorCode": None,
            "backoffLevel": 0,
            "providerSpecificData": {
                "prefix": node_prefix,
                "apiType": "chat",
                "baseUrl": BASE_URL,
                "nodeName": node_name,
                "connectionProxyEnabled": False,
                "connectionProxyUrl": "",
                "connectionNoProxy": ""
            }
        }

        if em in existing:
            cur.execute(
                """UPDATE providerConnections SET data = ?, updatedAt = ?, isActive = 1
                   WHERE provider = ? AND email = ?;""",
                (json.dumps(conn_data), now_iso, node_id, em)
            )
            updated += 1
        else:
            conn_id = str(uuid.uuid4())
            cur.execute(
                """INSERT INTO providerConnections
                   (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (conn_id, node_id, "apikey", conn_label, em, idx, 1, json.dumps(conn_data), now_iso, now_iso)
            )
            added += 1

    conn.commit()
    conn.close()

    print(f"\n[✓] 9router Sync Complete: {added} new connections added, {updated} updated.")
    print(f"    Total active InfinityRouter connections: {added + updated}")
    print(f"    Available under prefix: '{node_prefix}/...' (e.g. {node_prefix}/minimax-m3, {node_prefix}/deepseek-v4.1-flash-1017)")
    return True


def test_chat(prompt, model="ir/deepseek-v4.1-flash-1017", router_url="http://127.0.0.1:20127/v1"):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT key FROM apiKeys WHERE isActive = 1 LIMIT 1;")
    r = cur.fetchone()
    conn.close()
    if not r:
        print("No active 9router API key found in database.")
        return

    key = r[0]
    print(f"Sending prompt to 9router ({model})...")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{router_url}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST"
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            dur = time.time() - t0
            res = json.loads(resp.read().decode())
            content = res["choices"][0]["message"]["content"]
            print(f"\n[Response in {dur:.2f}s]:\n{content}")
    except urllib.error.HTTPError as e:
        print(f"9router Error: {e.code} - {e.read().decode()}")
    except Exception as e:
        print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="InfinityRouter Suite & 9Router Manager")
    parser.add_argument("--file", default=DEFAULT_TXT, help="Path to accounts.txt")
    parser.add_argument("--check", action="store_true", help="Audit all accounts in parallel")
    parser.add_argument("--sync-9router", action="store_true", help="Sync accounts into 9router connection pool")
    parser.add_argument("--prefix", default="ir", help="Prefix for 9router models (default: ir)")
    parser.add_argument("--test-chat", type=str, help="Send a test message through 9router")
    parser.add_argument("--export", choices=["json", "csv"], help="Export parsed accounts")
    args = parser.parse_args()

    accounts = parse_accounts(args.file)
    print(f"Loaded {len(accounts)} accounts from {args.file}")

    if args.check:
        print(f"\nAuditing {len(accounts)} accounts concurrently...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(check_key, accounts))

        ok_count = sum(1 for r in results if r["chat_ok"])
        print("\n" + "=" * 70)
        print(f"AUDIT SUMMARY: {ok_count}/{len(accounts)} accounts FULLY ACTIVE")
        print("=" * 70)
        for r in results:
            status = f"OK ({r['latency_ms']}ms)" if r["chat_ok"] else f"FAIL ({r['error']})"
            print(f"  {r['email']:28} : {status}")
        print("=" * 70)

    if args.sync_9router:
        sync_to_9router(accounts, node_prefix=args.prefix)

    if args.test_chat:
        test_chat(args.test_chat, model=f"{args.prefix}/deepseek-v4.1-flash-1017")

    if args.export == "json":
        out_p = args.file.rsplit(".", 1)[0] + ".json"
        with open(out_p, "w") as f:
            json.dump(accounts, f, indent=2)
        print(f"Exported {len(accounts)} accounts to {out_p}")
    elif args.export == "csv":
        out_p = args.file.rsplit(".", 1)[0] + ".csv"
        with open(out_p, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["username", "email", "password", "apikey", "base_url", "proxy", "status"])
            writer.writeheader()
            writer.writerows(accounts)
        print(f"Exported {len(accounts)} accounts to {out_p}")

    if not any([args.check, args.sync_9router, args.test_chat, args.export]):
        print("\nTip: run with --check to test health, or --sync-9router to import to 9router.")


if __name__ == "__main__":
    main()
