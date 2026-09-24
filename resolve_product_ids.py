# -*- coding: utf-8 -*-
"""
Resolve books.com.tw product IDs for each book's ISBN by querying the
public search page (server-rendered HTML) and parsing the top result's
product id out of `id="prod-itemlist-XXXX"` blocks.

Paced deliberately slow and respectful of books.com.tw's WAF: a single
request per ~1.5-2.5s, and on detecting a soft-block ("連線暫時異常" /
non-200 status), backs off for several minutes before retrying rather
than hammering the site.

Resumable: progress is checkpointed to product_ids.json after every
book, so re-running picks up where it left off. Existing "matched" or
"notfound" entries are not re-queried.
"""
import json
import re
import time
import random
import requests

BOOKS_PATH = "books.json"
OUT_PATH = "product_ids.json"
LOG_PATH = "resolve_product_ids.log"

ISBN_RE = re.compile(r'^97[89]\d{10}$')
ITEM_RE = re.compile(r'id="prod-itemlist-([A-Za-z0-9]+)">.*?title="([^"]+)"', re.S)
BLOCK_MARKER = "連線暫時異常"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MIN_DELAY = 1.6
MAX_DELAY = 2.6
BACKOFF_START = 90
BACKOFF_CAP = 900

def log(msg):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S") + " " + msg + "\n")

def load_progress():
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_progress(data):
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    import os
    os.replace(tmp, OUT_PATH)

def pick_best(matches):
    physical = [(pid, t) for pid, t in matches if re.match(r'^\d{10}$', pid)]
    if physical:
        return physical[0]
    if matches:
        return matches[0]
    return None

def query_once(session, isbn):
    """Returns ('ok', result_dict) or ('blocked', None) or ('error', msg)."""
    url = f"https://search.books.com.tw/search/query/key/{isbn}/cat/all"
    try:
        r = session.get(url, headers=HEADERS, timeout=20)
    except Exception as e:
        return "error", str(e)

    if r.status_code == 404:
        return "ok", {"status": "notfound"}
    if r.status_code != 200:
        return "blocked", None

    if BLOCK_MARKER in r.text:
        return "blocked", None

    matches = ITEM_RE.findall(r.text)
    best = pick_best(matches)
    if not best:
        return "ok", {"status": "notfound"}
    pid, title = best
    return "ok", {"status": "matched", "product_id": pid, "title": title}

def main():
    with open(BOOKS_PATH, encoding="utf-8") as f:
        books = json.load(f)

    isbns = sorted({
        str(b.get("isbn") or "").strip()
        for b in books
        if ISBN_RE.match(str(b.get("isbn") or "").strip())
    })

    progress = load_progress()
    todo = [i for i in isbns if i not in progress]

    log(f"=== run start: {len(isbns)} total unique isbns, {len(todo)} remaining ===")
    print(f"total={len(isbns)} remaining={len(todo)}", flush=True)

    session = requests.Session()
    backoff = BACKOFF_START
    idx = 0
    n = len(todo)
    while idx < n:
        isbn = todo[idx]
        status, result = query_once(session, isbn)

        if status == "blocked":
            log(f"BLOCKED at isbn={isbn} (idx={idx}/{n}); backing off {backoff}s")
            print(f"blocked, backing off {backoff}s ({idx}/{n})", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_CAP)
            session = requests.Session()
            continue  # retry same isbn

        backoff = BACKOFF_START  # reset after a success

        if status == "error":
            progress[isbn] = {"status": "error", "error": result}
        else:
            progress[isbn] = result

        idx += 1
        if idx % 20 == 0 or idx == n:
            save_progress(progress)
            matched_so_far = sum(1 for v in progress.values() if v.get("status") == "matched")
            log(f"progress: {idx}/{n}, isbn={isbn} -> {progress[isbn].get('status')}, total_matched={matched_so_far}")
            print(f"{idx}/{n} matched_total={matched_so_far}", flush=True)

        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    save_progress(progress)
    matched = sum(1 for v in progress.values() if v.get("status") == "matched")
    notfound = sum(1 for v in progress.values() if v.get("status") == "notfound")
    errored = sum(1 for v in progress.values() if v.get("status") == "error")
    log(f"=== run end: matched={matched} notfound={notfound} error={errored} total={len(progress)} ===")
    print(f"DONE matched={matched} notfound={notfound} error={errored} total={len(progress)}", flush=True)

if __name__ == "__main__":
    main()
