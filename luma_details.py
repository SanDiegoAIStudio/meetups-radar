#!/usr/bin/env python3
"""Fetch Luma event descriptions into a run sidecar.

The Apify actor returns an empty description. This step reads evt- ids from the
run's Luma item files, reuses a cache (21 days, or 3 days when the stored
description is empty), and writes luma-details.json for the normalizer's
display summary.

Usage:
  python3 luma_details.py <workdir> [--cache PATH] [--max-fetch N]
      [--delay SECONDS] [--timeout SECONDS]
"""
import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EVENT_ID = re.compile(r"^evt-[A-Za-z0-9]+$")
EVENT_URL = "https://api.lu.ma/event/get"
USER_AGENT = "meetups-radar/1"
BLOCK_END = ("heading", "blockquote", "code_block")


def fetch_json(url, timeout):
    """GET JSON. Tests replace this. HTTP errors and non-JSON bodies raise."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8"))


def _node_text(node):
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    if kind == "text":
        text = node.get("text")
        return text if isinstance(text, str) else ""
    if kind == "hard_break":
        return "\n"
    content = node.get("content")
    if not isinstance(content, list):
        content = []
    inner = "".join(_node_text(child) for child in content)
    if kind == "list_item":
        return "- " + inner + "\n"
    if kind == "paragraph":
        return inner + "\n"
    if kind in BLOCK_END:
        return inner + "\n"
    return inner


def mirror_text(doc):
    """Flatten a ProseMirror document to display text. Never raises."""
    try:
        raw = _node_text(doc)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = "\n".join(line.strip() for line in raw.split("\n"))
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()
    except Exception:
        return ""


def json_int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def cache_fresh(entry, now):
    if not isinstance(entry, dict):
        return False
    fetched = parse_time(entry.get("fetched_at"))
    if fetched is None:
        return False
    age = now - fetched
    if age < timedelta(0):
        return False
    # An empty description is retried after 3 days. Text stays cached for 21.
    if entry.get("description") == "":
        return age <= timedelta(days=3)
    return age <= timedelta(days=21)


def blank_description(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def collect_ids(workdir):
    found = []
    seen = set()
    for name in sorted(os.listdir(workdir)):
        if not (name.startswith("items-") and name.endswith(".json") and "luma" in name):
            continue
        path = os.path.join(workdir, name)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            event_id = item.get("id")
            if not isinstance(event_id, str) or not EVENT_ID.match(event_id):
                continue
            if "description" in item and not blank_description(item.get("description")):
                continue
            if event_id in seen:
                continue
            seen.add(event_id)
            found.append(event_id)
    return found


def classify(payload):
    if not isinstance(payload, dict) or "description_mirror" not in payload:
        return "failed", "", None
    text = mirror_text(payload.get("description_mirror"))
    guest_count = json_int(payload.get("guest_count"))
    if text:
        return "fetched", text, guest_count
    return "empty", "", guest_count


def retryable_status(exc):
    """HTTP 429 and any 5xx earn one backoff sleep for the whole run."""
    code = getattr(exc, "code", None)
    if isinstance(code, bool) or not isinstance(code, int):
        return False
    return code == 429 or 500 <= code <= 599


def removed_status(exc):
    """HTTP 404 and 410 mean Luma removed the event: an empty description."""
    code = getattr(exc, "code", None)
    if isinstance(code, bool) or not isinstance(code, int):
        return False
    return code == 410 or code == 404


def has_text(entry):
    """True when a cache entry holds a description of at least one character."""
    return isinstance(entry, dict) and isinstance(entry.get("description"), str) \
        and len(entry["description"]) >= 1


def prune_cache(cache, now, run_ids):
    """Drop entries whose fetched_at is older than 21 days or unparseable, except an entry
    that holds text and whose id is in this run's ids: if the refetch failed or was
    deferred, that text still goes to the sidecar, so the entry must stay."""
    limit = timedelta(days=21)
    run_ids = set(run_ids)
    stale = []
    for key, entry in cache.items():
        fetched = parse_time(entry.get("fetched_at")) if isinstance(entry, dict) else None
        if fetched is None or now - fetched > limit:
            if key in run_ids and has_text(entry):
                continue
            stale.append(key)
    for key in stale:
        del cache[key]


def detail_from_cache(entry):
    text = ""
    guest_count = None
    if isinstance(entry, dict):
        raw = entry.get("description")
        if isinstance(raw, str):
            text = raw
        guest_count = json_int(entry.get("guest_count"))
    return {"description": text, "guest_count": guest_count}


def load_cache(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def write_json(path, payload):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def event_url(event_id):
    return EVENT_URL + "?" + urllib.parse.urlencode({"event_api_id": event_id})


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Fetch Luma event descriptions into a run sidecar.")
    parser.add_argument("workdir")
    parser.add_argument("--cache", default=os.path.join(HERE, "luma-details-cache.json"))
    parser.add_argument("--max-fetch", type=int, default=400)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args(argv)
    # The negated comparisons also refuse nan.
    if args.max_fetch < 0:
        parser.error("--max-fetch must be 0 or more")
    if not args.delay >= 0:
        parser.error("--delay must be 0 or more")
    if not args.timeout > 0:
        parser.error("--timeout must be above 0")
    return args


def run(argv):
    args = parse_args(argv)
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = collect_ids(args.workdir)
    cache = load_cache(args.cache)
    details = {}
    cached = fetched = empty = failed = deferred = 0
    network = 0
    consecutive_failures = 0
    seen_good = False
    held = []
    gone_breaker = False
    breaker_noted = False
    pending_backoff = False
    backoff_used = False
    max_fetch = args.max_fetch
    delay = args.delay
    timeout = args.timeout
    ids_n = len(ids)

    run_ids = set(ids)

    def save_progress():
        prune_cache(cache, now, run_ids)
        # An id this run did not fetch successfully (failed or deferred) keeps whatever
        # text its cache entry holds, however old.
        for known_id in ids:
            if known_id not in details and has_text(cache.get(known_id)):
                details[known_id] = detail_from_cache(cache[known_id])
        write_json(os.path.join(args.workdir, "luma-details.json"), {
            "schema": 1,
            "generated_at": now_iso,
            "counts": {
                "ids": ids_n,
                "cached": cached,
                "fetched": fetched,
                "empty": empty,
                "failed": failed,
                "deferred": deferred,
            },
            "details": details,
        })
        write_json(args.cache, cache)

    def flush_held():
        nonlocal empty
        for held_id in held:
            record = {"description": "", "guest_count": None}
            details[held_id] = record
            cache[held_id] = {
                "description": "",
                "guest_count": None,
                "fetched_at": now_iso,
            }
            empty += 1
        held.clear()

    try:
        for event_id in ids:
            entry = cache.get(event_id)
            if cache_fresh(entry, now):
                cached += 1
                details[event_id] = detail_from_cache(entry)
                continue
            # Five failed fetches in a row: stop calling Luma and defer the rest.
            if consecutive_failures >= 5:
                if not breaker_noted:
                    print("LUMA-DETAILS: breaker after %d consecutive failures" % consecutive_failures)
                    breaker_noted = True
                deferred += 1
                continue
            # Ten 404 or 410 answers and no real payload: stop calling Luma and defer the rest.
            if gone_breaker:
                deferred += 1
                continue
            if network >= max_fetch:
                deferred += 1
                continue
            if network and pending_backoff:
                time.sleep(5)
                pending_backoff = False
            if network:
                time.sleep(delay)
            network += 1
            failure = None
            try:
                payload = fetch_json(event_url(event_id), timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                    json.JSONDecodeError, ValueError, OSError, http.client.HTTPException) as exc:
                payload = None
                failure = exc
            outcome, text, guest_count = classify(payload)
            if failure is not None and removed_status(failure):
                outcome, text, guest_count = "empty", "", None
            # Hold a removed event until a real payload shows Luma is answering.
            holding = (
                not seen_good
                and failure is not None
                and removed_status(failure)
                and outcome == "empty"
            )
            if holding:
                held.append(event_id)
                if len(held) >= 10:
                    print("LUMA-DETAILS: breaker after 10 answers of 404 or 410 and no good answer")
                    failed += len(held)
                    # Written as empty entries (the record flush_held writes) so the next run
                    # starts with later ids; they stay out of this run's sidecar. An entry
                    # that already holds text is left alone.
                    for held_id in held:
                        if not has_text(cache.get(held_id)):
                            cache[held_id] = {
                                "description": "",
                                "guest_count": None,
                                "fetched_at": now_iso,
                            }
                    held.clear()
                    gone_breaker = True
            elif not seen_good and failure is None and outcome in ("fetched", "empty"):
                seen_good = True
                flush_held()
            if outcome == "failed":
                failed += 1
                consecutive_failures += 1
                if failure is not None and retryable_status(failure) and not backoff_used:
                    backoff_used = True
                    pending_backoff = True
            else:
                consecutive_failures = 0
                if not holding:
                    if outcome == "empty":
                        empty += 1
                    else:
                        fetched += 1
            if outcome != "failed":
                if not holding:
                    record = {"description": text, "guest_count": guest_count}
                    details[event_id] = record
                    cache[event_id] = {
                        "description": text,
                        "guest_count": guest_count,
                        "fetched_at": now_iso,
                    }
            if network % 25 == 0:
                save_progress()
    except Exception:
        # An error nobody planned for ends the loop; what was fetched so far is kept.
        try:
            save_progress()
        except Exception:
            pass
        raise
    if not gone_breaker:
        flush_held()
    save_progress()
    print("LUMA-DETAILS: ids=%d cached=%d fetched=%d empty=%d failed=%d deferred=%d" % (
        ids_n, cached, fetched, empty, failed, deferred))
    # Exit 3 means Luma was asked and gave nothing usable. A run that made no request
    # (nothing to do, or --max-fetch 0) is not a failure.
    if network and not (cached or fetched or empty):
        return 3
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        return run(argv)
    except Exception as exc:
        print("LUMA-DETAILS: error %s: %s" % (type(exc).__name__, exc))
        return 4


if __name__ == "__main__":
    sys.exit(main())
