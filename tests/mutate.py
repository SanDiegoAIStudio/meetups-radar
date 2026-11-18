"""Mutation check for the test suite.

Each mutation makes one small change to a shipped file in a fresh copy of the repository and
runs the test modules or classes it names there. A mutation is "killed" when those tests fail
and "survived" when they still pass, which means a test is missing. Exit status 1 when any
survived. The runs stop at the first failure, so a killed mutation is quick.

Run: python3 tests/mutate.py            all mutations
     python3 tests/mutate.py bearer     only the mutations whose name contains "bearer"

Each entry: (name, file, text to find, replacement, replace every occurrence, tests to run).
Some names start with a letter, such as B2 or K: those mutations came with the second round of fixes, and mutations with the same letter guard the same rule.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT_SECONDS = 900

R = "tests.test_radar."
NORM = ["tests.test_normalize"]
LUMA = ["tests.test_luma_details"]
START = ["tests.test_start_leg"]
LOCKT = ["tests.test_lock"]

MUTATIONS = [
    # --- radar.sh: earlier behaviour ---
    ("radar.sh: accept a dataset that is not an array", "radar.sh",
     "jq -e 'type==\"array\"'", "jq -e 'true'", False,
     [R + "HappyPath", R + "PartialLegs"]),
    ("radar.sh: never write partial-legs.txt", "radar.sh",
     '>> "$WORK/partial-legs.txt"', '>> /dev/null', True,
     [R + "PartialLegs"]),
    ("radar.sh: send the Eventbrite leg on any day", "radar.sh",
     '[ "$DAY_OF_MONTH" -le 7 ]', '[ "$DAY_OF_MONTH" -le 31 ]', False,
     [R + "Eventbrite"]),
    ("radar.sh: keep every run folder", "radar.sh",
     "tail -n +9", "tail -n +900", False,
     [R + "Guards", R + "ZeroDataKeepsTheFeed"]),
    ("radar.sh: cleanup also removes folders that are not dated run folders", "radar.sh",
     '"$RUNS"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/', '"$RUNS"/*/', False,
     [R + "Guards", R + "ZeroDataKeepsTheFeed"]),
    ("radar.sh: cleanup removes nothing", "radar.sh",
     '[ -L "${old%/}" ] || rm -rf -- "$old"', '[ -L "${old%/}" ] || : "$old"', False,
     [R + "Guards", R + "ZeroDataKeepsTheFeed"]),
    ("radar.sh: missing token exits 1", "radar.sh",
     'echo "radar.sh: APIFY_TOKEN is not set" >&2\n  exit 2',
     'echo "radar.sh: APIFY_TOKEN is not set" >&2\n  exit 1', False,
     [R + "Guards"]),
    ("radar.sh: start a Luma leg for a city without luma_city", "radar.sh",
     'if [ -n "$LUMA_CITY" ]; then', 'if true; then', False,
     [R + "HappyPath", R + "Guards"]),
    ("radar.sh: a failed normalize step exits 0", "radar.sh",
     '(see $WORK/radar.log)"\n  prune_runs\n  exit 1', '(see $WORK/radar.log)"\n  prune_runs\n  exit 0', False,
     [R + "Guards", R + "ZeroDataKeepsTheFeed"]),
    ("radar.sh: report no partial legs in the summary", "radar.sh",
     'PARTIAL_N="$(grep -c . "$WORK/partial-legs.txt" || true)"', 'PARTIAL_N=0', False,
     [R + "PartialLegs"]),
    ("start_leg.sh: do not retry a 5xx", "start_leg.sh",
     '[ "${code#5}" != "$code" ]', 'false', False, START),
    ("start_leg.sh: retry every non-201 answer", "start_leg.sh",
     '[ "$code" = "429" ]', '[ "$code" != "201" ]', False, START),
    ("start_leg.sh: keep the output file of an earlier try", "start_leg.sh",
     'rm -f -- "$4"', ':', False, START),

    # --- A: zero data never overwrites the feed ---
    ("A1 normalize_events.py: write a feed when zero items files are usable", "normalize_events.py",
     "if not _ITEMS:", "if False:", False, NORM),
    ("A1 normalize_events.py: an empty list does not count as usable", "normalize_events.py",
     "    if isinstance(_data, list):\n        _ITEMS[_p.name] = _data",
     "    if isinstance(_data, list) and _data:\n        _ITEMS[_p.name] = _data", False, NORM),
    ("A2 radar.sh: go on to the sorting step with zero legs returned", "radar.sh",
     'if [ "$ITEMS_OK" -eq 0 ]; then', 'if false; then', False,
     [R + "ZeroDataKeepsTheFeed"]),
    ("A2 radar.sh: zero data exits 0", "radar.sh",
     'the feed was left as it was"\n  prune_runs\n  exit 1', 'the feed was left as it was"\n  prune_runs\n  exit 0', False,
     [R + "ZeroDataKeepsTheFeed"]),
    ("A2 radar.sh: count a leg as data when the file is not accepted", "radar.sh",
     "      ITEMS_OK=$((ITEMS_OK + 1))\n    else\n      rm -f",
     "      ITEMS_OK=$((ITEMS_OK + 1))\n    else\n      ITEMS_OK=$((ITEMS_OK + 1)); rm -f", False,
     [R + "ZeroDataKeepsTheFeed"]),

    # --- B: configuration is checked before any request ---
    ("B1 normalize_events.py: accept an upper-case slug", "normalize_events.py",
     "if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):",
     "if not isinstance(slug, str):", False, NORM),
    ("B1 normalize_events.py: accept a radius of zero", "normalize_events.py",
     "if not _is_number(radius) or radius <= 0:", "if not _is_number(radius):", False, NORM),
    ("B1 normalize_events.py: accept a boolean radius", "normalize_events.py",
     "isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)",
     "isinstance(value, (int, float)) and math.isfinite(value)", False, NORM),
    ("B1 normalize_events.py: accept a slug that starts another slug", "normalize_events.py",
     'elif a != b and b.startswith(a + "-"):', "elif False:", False, NORM),
    ("B1 normalize_events.py: accept an unknown time zone", "normalize_events.py",
     "                ZoneInfo(tz)\n            except Exception:\n                problems.append(f\"{where}.tz {_show(tz)} is not",
     "                pass\n            except Exception:\n                problems.append(f\"{where}.tz {_show(tz)} is not", False, NORM),
    ("B1 normalize_events.py: accept a bad calendar date", "normalize_events.py",
     'problems.append(f"date: {date_text} is not a calendar date in YYYY-MM-DD form")', "pass", False, NORM),
    ("B1 normalize_events.py: accept a repeated keyword", "normalize_events.py",
     'problems.append(f"{where}.kws entry {_show(kw)} repeats another keyword of this city (case is ignored)")',
     "pass", False, NORM),
    ("B2 radar.sh: skip the configuration check", "radar.sh",
     'if ! python3 "$HERE/normalize_events.py" --check "$STAMP" >/dev/null; then', "if false; then", False,
     [R + "SettingsAreChecked"]),
    ("B2 radar.sh: accept a setting of any length", "radar.sh",
     '[ "${#1}" -le 9 ]', "true", False, [R + "SettingsAreChecked"]),
    ("B2 radar.sh: accept a token with whitespace", "radar.sh",
     "  *[[:space:][:cntrl:]]*)\n    echo", "  NOMATCH_NEVER)\n    echo", False,
     [R + "SettingsAreChecked"]),
    ("B2 radar.sh: accept zero start tries", "radar.sh",
     'if [ "$setting" = "RADAR_START_TRIES" ] && [ "$((10#$value))" -lt 1 ]; then',
     "if false; then", False, [R + "SettingsAreChecked"]),
    ("B2 radar.sh: run when the configuration gives zero legs", "radar.sh",
     'if [ "$LEG_COUNT" -eq 0 ]; then', "if false; then", False, [R + "SettingsAreChecked"]),
    ("B3 start_leg.sh: a tries setting that is not a number is used as it is", "start_leg.sh",
     "case \"$tries\" in ''|*[!0-9]*) tries=3 ;; esac", "case \"$tries\" in '') tries=3 ;; esac", False, START),
    ("B3 start_leg.sh: a tries setting of zero is used as it is", "start_leg.sh",
     '|| [ "$((10#$tries))" -lt 1 ]; then tries=3; fi', '|| false; then tries=3; fi', False, START),
    ("B3 start_leg.sh: a pause with a leading zero is read as octal", "start_leg.sh",
     "pause=$((10#$pause))", "pause=$((pause))", False, START),
    ("B3 start_leg.sh: tries with a leading zero are read as octal", "start_leg.sh",
     "tries=$((10#$tries))", "tries=$((tries))", False, START),
    ("B3 start_leg.sh: a bad pause is used as it is", "start_leg.sh",
     "case \"$pause\" in ''|*[!0-9]*) pause=10 ;; esac", "case \"$pause\" in '') pause=10 ;; esac", False, START),

    # --- C: lock ---
    ("C lock.sh: never reclaim a stale lock", "lock.sh",
     "-mmin +45", "-mmin +99999", False, LOCKT + [R + "LockInTheRun"]),
    ("C lock.sh: reclaim a lock after one minute", "lock.sh",
     "-mmin +45", "-mmin +1", False, LOCKT),
    ("C lock.sh: release another run's lock", "lock.sh",
     '[ -n "$held" ] && [ "$held" = "$$" ]', "true", False, LOCKT),
    ("C lock.sh: refresh does nothing", "lock.sh",
     "perl -e 'utime undef, undef, $ARGV[0]' \"$1\" 2>/dev/null || true", "true", False,
     LOCKT + [R + "LockInTheRun"]),
    ("C lock.sh: take never writes the owner", "lock.sh",
     "printf '%s\\n' \"$$\" > \"$dir/pid\"", "true", False, LOCKT),
    ("C lock.sh: a lock that cannot be removed is reported as held", "lock.sh",
     "if ! rmdir \"$dir\" 2>/dev/null; then return 2; fi", "if ! rmdir \"$dir\" 2>/dev/null; then return 1; fi", False,
     LOCKT),
    ("C radar.sh: never refresh the lock while polling", "radar.sh",
     "  while :; do\n    lock_refresh \"$LOCKDIR\"", "  while :; do\n    :", False,
     [R + "LockInTheRun"]),
    ("C radar.sh: go on after a stale lock that cannot be removed", "radar.sh",
     'elif [ "$LOCK_RC" -ne 0 ]; then', "elif false; then", False, [R + "LockInTheRun"]),

    # --- D: the token stays out of the argument list ---
    ("D radar.sh: token in the status request arguments", "radar.sh",
     '-H @- "$API/actor-runs/$rid"', '-H "Authorization: Bearer $APIFY_TOKEN" "$API/actor-runs/$rid"', False,
     [R + "TokenStaysOutOfArguments"]),
    ("D radar.sh: token in the dataset request arguments", "radar.sh",
     '-H @- "$API/datasets/$DS/items?clean=true"',
     '-H "Authorization: Bearer $APIFY_TOKEN" "$API/datasets/$DS/items?clean=true"', False,
     [R + "TokenStaysOutOfArguments"]),
    ("D start_leg.sh: token in the start request arguments", "start_leg.sh",
     "-X POST -H @- -H", '-X POST -H "Authorization: Bearer $APIFY_TOKEN" -H', False,
     START + [R + "TokenStaysOutOfArguments"]),
    ("D radar.sh: drop the bearer header", "radar.sh",
     "printf 'Authorization: Bearer %s\\n' \"$APIFY_TOKEN\" \\\n      | curl -sS -m 20",
     "printf 'X-Radar-Note: %s\\n' \"$APIFY_TOKEN\" \\\n      | curl -sS -m 20", False,
     [R + "TokenStaysOutOfArguments", R + "HappyPath"]),

    # --- E: a bad status answer never replaces a good one ---
    ("E radar.sh: a status answer that does not parse replaces the good one", "radar.sh",
     "if jq -e '(.data.status | type) == \"string\"' \"$WORK/.st-$leg.tmp\" >/dev/null 2>&1; then",
     "if true; then", False, [R + "StatusAndCost"]),
    ("E radar.sh: leave the temporary status file behind", "radar.sh",
     '      rm -f "$WORK/.st-$leg.tmp"\n    fi\n    ST=', '      :\n    fi\n    ST=', False,
     [R + "StatusAndCost"]),

    # --- F: cost survives odd values ---
    ("F radar.sh: count a string cost", "radar.sh",
     "select(type == \"number\")", "select(true)", False, [R + "StatusAndCost"]),
    ("F radar.sh: refuse an exponent cost", "radar.sh",
     "([eE][-+]?[0-9]+)?$", "$", False, [R + "StatusAndCost"]),
    ("F radar.sh: print the cost with two decimals", "radar.sh",
     "printf '%.5f' \"$COST_RAW\"", "printf '%.2f' \"$COST_RAW\"", False, [R + "StatusAndCost"]),

    # --- G: old run folders are pruned on the failure paths too ---
    ("G radar.sh: no pruning when zero legs returned data", "radar.sh",
     'the feed was left as it was"\n  prune_runs', 'the feed was left as it was"\n  :', False,
     [R + "ZeroDataKeepsTheFeed"]),
    ("G radar.sh: no pruning when the sorting step fails", "radar.sh",
     '(see $WORK/radar.log)"\n  prune_runs', '(see $WORK/radar.log)"\n  :', False,
     [R + "ZeroDataKeepsTheFeed"]),

    # --- H: titles in any script ---
    ("H normalize_events.py: stems keep ASCII letters only", "normalize_events.py",
     "if ch.isalnum() and not unicodedata.combining(ch))",
     "if ch.isalnum() and ord(ch) < 128 and not unicodedata.combining(ch))", False, NORM),
    ("H normalize_events.py: accents are not folded", "normalize_events.py",
     'unicodedata.normalize("NFKD", text or "")', 'unicodedata.normalize("NFC", text or "")', False, NORM),
    ("H normalize_events.py: an empty stem is a merge key", "normalize_events.py",
     'return title_stem(ev["title"]) or "id:" + str(ev["id"])', 'return title_stem(ev["title"])', False, NORM),
    ("H normalize_events.py: an empty stem hashes to the same id", "normalize_events.py",
     'stem = fold_alnum(title)[:40] or (title or "")', 'stem = fold_alnum(title)[:40]', False, NORM),
    ("H normalize_events.py: the id stem is cut at four characters", "normalize_events.py",
     "stem = fold_alnum(title)[:40]", "stem = fold_alnum(title)[:4]", False, NORM),

    # --- I: a broken items file or item never stops the run ---
    ("I normalize_events.py: take a JSON value that is not a list as items", "normalize_events.py",
     "    if isinstance(_data, list):\n        _ITEMS[_p.name] = _data",
     "    if _data is not None:\n        _ITEMS[_p.name] = _data", False, NORM),
    ("I normalize_events.py: do not mark the leg of a skipped file as partial", "normalize_events.py",
     "partial_legs += [leg for leg in _BAD_ITEM_LEGS if leg not in partial_legs]", "pass", False, NORM),
    ("I normalize_events.py: do not report skipped items", "normalize_events.py",
     "    if count:\n        print(f\"WARN: {slug} {source}: {count} items skipped",
     "    if False:\n        print(f\"WARN: {slug} {source}: {count} items skipped", False, NORM),
    ("I normalize_events.py: one odd Meetup item ends the loop", "normalize_events.py",
     "        except Exception:\n            skipped += 1", "        except KeyboardInterrupt:\n            skipped += 1", True, NORM),

    # --- J: only http and https links reach the JSON ---
    ("J normalize_events.py: Luma links are not checked", "normalize_events.py",
     'url=safe_url(it.get("lumaUrl"))', 'url=it.get("lumaUrl")', False, NORM),
    ("J normalize_events.py: Meetup links are not checked", "normalize_events.py",
     'url=safe_url(it.get("eventShortUrl") or it.get("eventUrl"))',
     'url=(it.get("eventShortUrl") or it.get("eventUrl"))', False, NORM),
    ("J normalize_events.py: Eventbrite links are not checked", "normalize_events.py",
     'url = safe_url(it.get("url") or (f"https://www.eventbrite.com/e/{eid}" if eid else None))',
     'url = (it.get("url") or (f"https://www.eventbrite.com/e/{eid}" if eid else None))', False, NORM),
    ("J normalize_events.py: the link check lets any scheme through", "normalize_events.py",
     'if not (u and str(u).startswith(("https://", "http://"))):', "if not u:", False, NORM),

    # --- K: command line of the sorting step ---
    ("K normalize_events.py: any number of arguments", "normalize_events.py",
     "if not 1 <= len(_ARGS) <= 2:", "if False:", False, NORM),
    ("K normalize_events.py: a missing folder is an empty week", "normalize_events.py",
     "if not WORK.is_dir():", "if False:", False, NORM),
    ("K normalize_events.py: the date is not checked in an ordinary run", "normalize_events.py",
     "_found = run_config_check(_DATE_ARG)\nif _found:\n    _die(_found, 2)\nTODAY", "TODAY", False, NORM),

    # --- L: luma_details.py ---
    ("L1 luma_details.py: the breaker leaves the held ids out of the cache", "luma_details.py",
     "                        if not has_text(cache.get(held_id)):", "                        if False:", False, LUMA),
    ("L2 luma_details.py: an old entry with text is pruned though its id is in this run", "luma_details.py",
     "            if key in run_ids and has_text(entry):\n                continue",
     "            if False:\n                continue", False, LUMA),
    ("L2 luma_details.py: an old text stays out of the sidecar after a failed refetch", "luma_details.py",
     "if known_id not in details and has_text(cache.get(known_id)):", "if False:", False, LUMA),
    ("L3 luma_details.py: an unplanned error saves nothing", "luma_details.py",
     "        try:\n            save_progress()\n        except Exception:\n            pass\n        raise",
     "        raise", False, LUMA),
    ("L4 luma_details.py: exit 3 even when no request was made", "luma_details.py",
     "if network and not (cached or fetched or empty):", "if not (cached or fetched or empty):", False, LUMA),
    ("L5 luma_details.py: a negative max-fetch is accepted", "luma_details.py",
     "if args.max_fetch < 0:", "if False:", False, LUMA),
    ("L5 luma_details.py: a negative delay is accepted", "luma_details.py",
     "if not args.delay >= 0:", "if False:", False, LUMA),
    ("L5 luma_details.py: a timeout of zero is accepted", "luma_details.py",
     "if not args.timeout > 0:", "if False:", False, LUMA),
    ("luma_details.py: cache entries never count as fresh", "luma_details.py",
     "return age <= timedelta(days=21)", "return age <= timedelta(days=0)", False, LUMA),
    ("luma_details.py: no backoff after a 429", "luma_details.py",
     "return code == 429 or 500 <= code <= 599", "return False", False, LUMA),
    ("luma_details.py: no breaker after repeated failures", "luma_details.py",
     "if consecutive_failures >= 5:", "if consecutive_failures >= 5000:", False, LUMA),

    # --- N: tests fail, they do not skip ---
    ("N tests/test_start_leg.py: a skip decorator on StartLegTests", "tests/test_start_leg.py",
     "class StartLegTests(unittest.TestCase):",
     "@unit" + "test.sk" + "ipUnless(False, 'x')\nclass StartLegTests(unittest.TestCase):", False,
     ["tests.test_no_skips"]),

    # --- earlier behaviour of normalize_events.py ---
    ("normalize_events.py: drop the source order in the duplicate rule", "normalize_events.py",
     'SOURCE_ORDER = {"luma": 0, "meetup": 1, "eventbrite": 2}',
     'SOURCE_ORDER = {"luma": 2, "meetup": 1, "eventbrite": 0}', False, NORM),
    ("normalize_events.py: switch off host exclusion", "normalize_events.py",
     'EXCLUDED_HOST = [rx for _, rx in _compile_list(_GROUPS_FILE.get("exclude_hosts", []), "exclude_hosts")]',
     'EXCLUDED_HOST = []', False, NORM),
    ("normalize_events.py: switch off the topic gate", "normalize_events.py",
     'elif e["topic"] == "other" and not e.get("group_registry"):', 'elif False:', False, NORM),
    ("normalize_events.py: skip the atomic write", "normalize_events.py",
     "    tmp.write_bytes(text.encode(\"utf-8\", \"replace\"))\n    os.replace(tmp, path)",
     "    path.write_bytes(text.encode(\"utf-8\", \"replace\"))", False, NORM),
    ("normalize_events.py: digest index prints the kept count in the Raw column", "normalize_events.py",
     "{r['counts']['online']} | {r['counts']['raw']} |", "{r['counts']['online']} | {r['counts']['kept']} |", False, NORM),
    ("normalize_events.py: keep past events", "normalize_events.py",
     'if ev["start"][:10] < TODAY: past += 1; continue', 'if False: continue', False, NORM),
    ("luma_details.py: skip the atomic write", "luma_details.py",
     '        with open(tmp, "w", encoding="utf-8") as handle:\n            handle.write(text)\n        os.replace(tmp, path)',
     '        with open(path, "w", encoding="utf-8") as handle:\n            handle.write(text)', False, LUMA),
]

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "runs", "pulls", ".git", "*.tmp")


def run_one(name, filename, old, new, every, tests):
    copy = tempfile.mkdtemp(prefix="radar-mutant-")
    try:
        target_root = os.path.join(copy, "repo")
        shutil.copytree(ROOT, target_root, ignore=IGNORE)
        path = os.path.join(target_root, filename)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if old not in text:
            raise SystemExit("mutation %r: text to replace was not found in %s" % (name, filename))
        mutated = text.replace(old, new) if every else text.replace(old, new, 1)
        if mutated == text:
            raise SystemExit("mutation %r: the mutated file equals the original" % name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(mutated)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        try:
            proc = subprocess.run([sys.executable, "-m", "unittest", "-f"] + list(tests),
                                  cwd=target_root, env=env, capture_output=True, text=True,
                                  timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return True, "timed out"
        if proc.returncode == 0:
            return False, ""
        tail = [x for x in proc.stderr.splitlines() if x.startswith(("FAIL:", "ERROR:"))]
        return True, tail[0] if tail else "tests failed"
    finally:
        shutil.rmtree(copy, ignore_errors=True)


def main(argv):
    only = argv[1] if len(argv) > 1 else ""
    killed = survived = 0
    for name, filename, old, new, every, tests in MUTATIONS:
        if only and only not in name:
            continue
        dead, why = run_one(name, filename, old, new, every, tests)
        if dead:
            killed += 1
            print("killed: %s (%s)" % (name, why), flush=True)
        else:
            survived += 1
            print("survived: %s" % name, flush=True)
    print("mutations: %d killed, %d survived" % (killed, survived))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
