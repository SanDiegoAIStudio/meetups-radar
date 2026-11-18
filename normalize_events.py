#!/usr/bin/env python3
"""Multi-city AI/tech event radar normalizer.

Reads raw Apify dataset dumps from a work dir (items-<city>-luma.json,
items-<city>-meetup-<kw>.json, items-<city>-eventbrite*.json), classifies
each active city's events (the curated group registry applies to cities whose
cities.json entry sets "registry": true), and writes, each file atomically:

  <out>/<city>/<date>.json + <date>.md + LATEST.json + LATEST.md
  <out>/ALL-CITIES.json   (site feed: per-city in-person radar + one deduped
                           city-agnostic topic-gated `online` collection +
                           topics/formats manifests + partial flags)
  <out>/_feed/<date>.json (dated snapshot of the feed)
  <out>/LATEST.md         (cross-city digest index; LATEST.json = feed alias)

<out> is the RADAR_OUT environment variable, default "pulls" beside this script.

Usage: normalize_events.py WORK_DIR [YYYY-MM-DD]
       normalize_events.py --check [YYYY-MM-DD]

--check reads cities.json and groups.json beside this script, prints one line per problem
on stderr ("<file>: <problem>") and exits 2, or prints "config ok" and exits 0. It reads no
work folder and writes nothing. An ordinary run does the same check first.

Exit status: 0 files written; 1 zero usable items files (nothing written); 2 bad command
line or bad configuration (nothing written).
"""
import fnmatch, json, math, os, re, html, sys, unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("RADAR_OUT") or (HERE / "pulls"))
LA_TZ = ZoneInfo("America/Los_Angeles")
TZ_ABBREV = {"America/Los_Angeles": "PT", "America/New_York": "ET", "America/Chicago": "CT", "America/Denver": "MT"}

# Bump on any change to the per-event dict shape. Version 4 has stable event ids, a per-city
# timezone, and the online events split into a city-agnostic topic-gated collection. The
# summary (display text) and end fields were added to version 4 without a bump; readers
# ignore unknown keys.
SCHEMA_VERSION = 4
# Dedupe precedence: Luma wins over Meetup wins over Eventbrite (richer, cleaner records first).
SOURCE_ORDER = {"luma": 0, "meetup": 1, "eventbrite": 2}
PREVIEW_CAP = 40  # max bullets per filtered-out list in the MD digest

USAGE = "usage: normalize_events.py WORK_DIR [YYYY-MM-DD]"
# fullmatch, never match with $: "$" also accepts a trailing newline.
_SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_KEYWORD_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9 .+#&-]{0,38}[A-Za-z0-9.+#&-])?")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _show(value):
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_text(value):
    return isinstance(value, str) and len(value) > 0


def _read_config(name, problems):
    """Parsed JSON of a config file beside this script, or None with a problem line."""
    try:
        text = (HERE / name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"{name}: cannot be read ({type(exc).__name__})")
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:
        problems.append(f"{name}: is not valid JSON ({exc})")
        return None


def _check_cities(data, problems):
    name = "cities.json"
    if not isinstance(data, dict):
        problems.append(f"{name}: the top-level value must be a JSON object")
        return
    active = data.get("active")
    if not isinstance(active, list) or not active:
        problems.append(f"{name}: active must be a list with at least one city")
        return
    slugs = []
    for i, c in enumerate(active):
        where = f"{name}: active[{i}]"
        if not isinstance(c, dict):
            problems.append(f"{where} must be an object")
            continue
        slug = c.get("slug")
        if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
            problems.append(f"{where}.slug {_show(slug)} must be lower-case letters and digits in words joined by single hyphens")
        else:
            slugs.append((i, slug))
        if not _is_text(c.get("label")):
            problems.append(f"{where}.label must be a string with at least one character")
        tz = c.get("tz")
        if not _is_text(tz):
            problems.append(f"{where}.tz must be a time zone name")
        else:
            try:
                ZoneInfo(tz)
            except Exception:
                problems.append(f"{where}.tz {_show(tz)} is not a time zone this system knows")
        if "luma_city" in c and c["luma_city"] is not None and not isinstance(c["luma_city"], str):
            problems.append(f"{where}.luma_city must be a string or null")
        meetup = c.get("meetup")
        if not isinstance(meetup, dict):
            problems.append(f"{where}.meetup must be an object")
        else:
            for key in ("city", "state"):
                if not _is_text(meetup.get(key)):
                    problems.append(f"{where}.meetup.{key} must be a string with at least one character")
            radius = meetup.get("radius")
            if not _is_number(radius) or radius <= 0:
                problems.append(f"{where}.meetup.radius {_show(radius)} must be a number above 0")
        kws = c.get("kws")
        if not isinstance(kws, list):
            problems.append(f"{where}.kws must be a list of strings")
        else:
            seen_kw = set()
            for kw in kws:
                if not isinstance(kw, str) or not _KEYWORD_RE.fullmatch(kw):
                    problems.append(f"{where}.kws entry {_show(kw)} must be 1 to 40 letters, digits, spaces or . + # & -, starting and ending with a letter, digit or . + # &")
                elif kw.lower() in seen_kw:
                    problems.append(f"{where}.kws entry {_show(kw)} repeats another keyword of this city (case is ignored)")
                else:
                    seen_kw.add(kw.lower())
        if "registry" in c and not isinstance(c["registry"], bool):
            problems.append(f"{where}.registry must be true or false")
        if "eventbrite" in c:
            eb = c["eventbrite"]
            if not isinstance(eb, dict):
                problems.append(f"{where}.eventbrite must be an object")
            elif "region" in eb and (not isinstance(eb["region"], list) or not all(isinstance(x, str) for x in eb["region"])):
                problems.append(f"{where}.eventbrite.region must be a list of strings")
    # The items files of a city are found by the slug plus a hyphen, so "san" beside
    # "san-diego" would read the files of both.
    for i, a in slugs:
        for j, b in slugs:
            if i < j and a == b:
                problems.append(f"{name}: active[{j}].slug {_show(b)} repeats active[{i}].slug")
            elif a != b and b.startswith(a + "-"):
                problems.append(f"{name}: active[{i}].slug {_show(a)} is the start of active[{j}].slug {_show(b)}, their items files would mix")


def _check_groups(data, problems):
    name = "groups.json"
    if not isinstance(data, dict):
        problems.append(f"{name}: the top-level value must be a JSON object")
        return
    shapes = {"groups": ("name", "category", "match"), "host_topics": ("topic", "match")}
    for key in ("groups", "host_topics", "exclude_hosts"):
        if key not in data:
            continue
        if not isinstance(data[key], list):
            problems.append(f"{name}: {key} must be a list")
            continue
        for i, entry in enumerate(data[key]):
            if key == "exclude_hosts" and isinstance(entry, str):
                continue
            fields = shapes.get(key, ("match",))
            if not isinstance(entry, dict) or not all(isinstance(entry.get(f), str) for f in fields):
                problems.append(f"{name}: {key}[{i}] must be an object with the text fields {', '.join(fields)}")


def run_config_check(date_text):
    """Problem lines ("<file>: <problem>") for cities.json, groups.json and the date."""
    problems = []
    cities = _read_config("cities.json", problems)
    if cities is not None:
        _check_cities(cities, problems)
    groups = _read_config("groups.json", problems)
    if groups is not None:
        _check_groups(groups, problems)
    if date_text is not None:
        ok = bool(_DATE_RE.fullmatch(date_text))
        if ok:
            try:
                date(int(date_text[:4]), int(date_text[5:7]), int(date_text[8:]))
            except ValueError:
                ok = False
        if not ok:
            problems.append(f"date: {date_text} is not a calendar date in YYYY-MM-DD form")
    return problems


def _die(lines, code):
    for line in lines:
        print(line, file=sys.stderr)
    sys.exit(code)


_ARGS = sys.argv[1:]
_DATE_ARG = _ARGS[1] if len(_ARGS) > 1 else None
if _ARGS[:1] == ["--check"]:
    if len(_ARGS) > 2:
        _die([USAGE], 2)
    _found = run_config_check(_DATE_ARG)
    if _found:
        _die(_found, 2)
    print("config ok")
    sys.exit(0)
if not 1 <= len(_ARGS) <= 2:
    _die([USAGE], 2)
WORK = Path(_ARGS[0])
if not WORK.is_dir():
    _die([f"ERROR: {_ARGS[0]} is not a folder"], 2)
_found = run_config_check(_DATE_ARG)
if _found:
    _die(_found, 2)
TODAY = _DATE_ARG if _DATE_ARG is not None else datetime.now(LA_TZ).strftime("%Y-%m-%d")

# Items files: a usable one parses as a JSON list (an empty list counts). With none, the
# feed on disk is left as it was. An unusable one is skipped, and its leg is marked partial.
_ITEMS = {}
_BAD_ITEM_LEGS = []
for _p in sorted(WORK.glob("items-*.json")):
    try:
        _data = json.loads(_p.read_bytes().decode("utf-8"))
    except Exception:
        _data = None
    if isinstance(_data, list):
        _ITEMS[_p.name] = _data
    else:
        print(f"WARN: {_p.name}: not a JSON list, skipped", file=sys.stderr)
        _BAD_ITEM_LEGS.append(_p.name[len("items-"):-len(".json")])
if not _ITEMS:
    _die([f"ERROR: zero usable items files in {_ARGS[0]}, nothing was written"], 1)

CITIES = json.loads((HERE / "cities.json").read_text(encoding="utf-8"))
_GROUPS_FILE = json.loads((HERE / "groups.json").read_text(encoding="utf-8"))
REGISTRY = _GROUPS_FILE.get("groups", [])
_registry_ok = []
for g in REGISTRY:
    # groups.json is hand-edited; one bad regex must not stop the digest from being written
    try:
        g["_re"] = re.compile(g["match"], re.I)
        _registry_ok.append(g)
    except re.error as exc:
        print(f"WARN: groups.json pattern for {g.get('name')!r} invalid ({exc}), skipped", file=sys.stderr)
REGISTRY = _registry_ok


def _compile_list(entries, what):
    """Compile regex strings from groups.json; an invalid one is skipped with a warning."""
    out = []
    for entry in entries:
        pattern = entry["match"] if isinstance(entry, dict) else entry
        try:
            out.append((entry, re.compile(pattern, re.I)))
        except re.error as exc:
            print(f"WARN: groups.json {what} pattern {pattern!r} invalid ({exc}), skipped", file=sys.stderr)
    return out


# Host rules read in file order: the first matching rule gives the topic of an otherwise
# keyword-free event.
HOST_TOPIC_RULES = [(entry["topic"], rx) for entry, rx in _compile_list(_GROUPS_FILE.get("host_topics", []), "host_topics")]
# Hosts whose events are filtered out of the digest. An empty list switches the filter off.
EXCLUDED_HOST = [rx for _, rx in _compile_list(_GROUPS_FILE.get("exclude_hosts", []), "exclude_hosts")]


def write_atomic(path: Path, text: str) -> None:
    """Truncate+write torn-read protection: same-dir temp then atomic rename."""
    tmp = path.with_name(path.name + ".tmp")
    # utf-8 whatever the locale; a lone surrogate from a scraped string becomes "?" instead of
    # ending the run half way through the writes
    tmp.write_bytes(text.encode("utf-8", "replace"))
    os.replace(tmp, path)

AI_KW = re.compile(r"(?<!t['\u2019\u02bc])(?<![\w.])\ba\.?i\.?\b|\b(ai4|agi|artificial intelligence|machine learning|reinforcement learning|multi[- ]agent|llms?|genai|gen ai|hugging ?face|mcp|data science|deep learning|agentic|ai agents?|gpt|chat ?gpt|language models?|claude|openai|cuda|pytorch|robotics?|robots?|autonomous|self.?driving|robocars?|computer vision|ocai)\b", re.I)
AI_TITLE_KW = re.compile(r"\bROS(?:\s?2)?\b")
# Hardware and maker adjacency: the physical-computing world a builder community cares about
# (3D printing, makerspaces, drones, IoT, embedded, AR/VR/XR, fabrication). Precision-tuned:
# bare "makers" and "electronics" are left out of description matching to keep hobby noise
# out, and strong maker signals that only count in a title live in HW_TITLE_KW below.
# Hardware ranks below ai in topic(), so "AI robotics" and "autonomous vehicles" read as ai;
# only the non-AI maker events land as hardware.
HW_KW = re.compile(r"\b(3d ?print\w*|(?:additive|advanced) manufactur\w*|maker ?faire|fab ?lab|uav|quadcopters?|fpv|drone (?:racing|show|flight|pilots?|photograph\w*|club|meetup|fest\w*)|\biot\b|internet of things|embedded systems?|arduino|raspberry ?pi|\bfpga\b|\bpcb\b|(?<!ace )hardware|augmented reality|virtual reality|mixed reality|\b(?:vr|xr)\b|spatial computing|\bcnc\b|laser[- ]?cut\w*|solder\w*|3d ?scan\w*)\b", re.I)
HW_TITLE_KW = re.compile(r"\b(electronics?|circuits? (?:hacking|social)|wearables?|maker ?space (?:tour|open house)|laser (?:lab|basics)|(?:wood|metal) lathe|maker social|woodworking(?: tools?)?)\b", re.I)
TECH_KW = re.compile(r"\b(coding|coder|developer[- ](?:first|advocate|relations?|experience)|(?<!no )software|startup|foundernv|data|cloud|saas|web3|crypto|blockchain|bitcoin(?:ers?)?|tokeni[sz]\w*|hack|hackathon|product manage\w*|programming languages?|python|javascript|rust|linux|kubernetes|devops|semiconductor|biotech|rhug|red hat user group)\b", re.I)
# Bare `tech`, `code`, `developer(s)`, `founders` and engineer words are useful in titles but
# too weak in descriptions: "QR code", "retired Civil Engineer", "founder of X" bio lines and a
# real-estate "Investors, Realtors, Developers, Vendors" list would all admit unrelated events.
# The unambiguous devrel sense of developer ("developer-first", "developer relations") stays in
# TECH_KW; the bare people-list noun is title-only. `software` has a (?<!no ) guard because
# Meetup's online boilerplate says "No software or download needed". `\w*techweek` exists
# because glued hashtags (#LATechWeek) defeat \btech\b. (?<!inner ) blocks the "Inner
# Engineering" meditation brand without touching real engineering titles, HW_KW's (?<!ace )
# blocks the hardware-store brand, and money ?20/20 names a fintech conference whose
# satellite events carry empty descriptions.
TECH_TITLE_KW = re.compile(r"\b(tech|\w*techweek|code|codex|developers?|game dev\w*|founders?(?! day)|(?<!inner )engineer(?:s|ing)?|fintech|open source|pyladies|wordpress|wordcamp|swiftui|verilog|rocq|atproto|formal math|openmetadata|okta|datadog|sambanova|inngest|re:invent|techqueria|igda|bim|revit|aws|codathon|ieee|octw|grace hopper|money ?20/20|ux|figma|digital health)\b|(?<!\w)c\+\+(?!\w)|(?<!\w)init\(\)", re.I)
NONTECH_TITLE = re.compile(r"\bhigh tech (?:elementary|middle|high)\b.*\b(?:teacher|student) outreach\b|\b(?:jigsaw puzzle|tote bag sewing|serger basics)\b|\bspace yacht\b.*\btech my house\b|\b(?:certified|pre-approved)(?:,\s*(?:certified|pre-approved))*\s+teams? only\b", re.I)
SPORT_SOCIAL_TITLE = re.compile(r"\b(?:skiers?\s*&\s*snowboarders?|snowboarders?\s*&\s*skiers?)\s+happy hour\b", re.I)
CYBER_KW = re.compile(r"\b(cyber(?:security)?|infosec|security|owasp|defcon|def con|red team|blue team|ctf|soc|siem|pentest|socalsec|malware|ciso|isc2|least privilege|threat hunt\w*|attack surfaces?|secret scann\w*|sec nerds|bitwarden)\b", re.I)
# Titles of paid multi-day courses and bootcamps are filtered out whatever the host is.
COURSE_TITLE = re.compile(r"\b\d+[- ]?Days? +(Workshop|Training|Session|Bootcamp|Intro)|Days? +(Training|Workshop|Session) +(in|\|)|1 Day (Intro )?(Training|Session|Workshop)|(Training|Bootcamp) in [A-Za-z.' ]{3,30}, ?[A-Za-z]{2}\b", re.I)

FORMAT_RULES = [
    ("hackathon", re.compile(r"\bhack(athon)?\b|slopathon|\bcodathon\b", re.I)),
    ("workshop", re.compile(r"\bworkshop\b|hands.?on|bootcamp|build (a|an|your)|coding session|study group|paper club", re.I)),
    ("social", re.compile(r"\bmixer\b|happy hour|social|networking|coffee|beer|drinks|hangout|meet ?and ?greet", re.I)),
    ("conference", re.compile(r"\bconference\b|\bsummit\b|\bexpo\b|dev days", re.I)),
]
SOCIAL_TITLE_KW = re.compile(r"\b(?:after[- ]hours|brunch|co-?working|coworking)\b", re.I)
INSTRUCTIONAL_TITLE = re.compile(r"\b(?:course|masterclass|certification|safety training)\b|\bclass(?:\s+@|\s+(?:at|in|on|for)\b|$)", re.I)
LUMA_SHELF_NOISE = re.compile(r"\binfinite wellness masterclasses\b", re.I)
VIRTUAL_TITLE = re.compile(r"^\s*\(virtual\)|\bvirtual (?:event|meetup|meeting|session|office hours?)\b", re.I)
REMOTE_MEETING_URL = re.compile(r"https?://(?:meet\.google\.com|[^\s/]*zoom\.us)/", re.I)

def clean(s, n=240):
    if not s: return ""
    # unescape first, then strip tags: the reverse order revives &lt;img&gt; into live HTML
    s = re.sub(r"<[^>]+>", " ", html.unescape(str(s)))
    s = re.sub(r"[\u200b\u200c\u200d\u2060\ufeff]", "", s)  # zero-width characters
    return re.sub(r"\s+", " ", s).strip()[:n]


def md_cell(s):
    """Escape scraped text for a Markdown table cell (backslash first, or pre-escaped
    pipes sneak through; pipes break tables, brackets forge links)."""
    return (str(s or "").replace("\\", "\\\\").replace("|", "\\|")
            .replace("[", "\\[").replace("]", "\\]"))


def safe_url(u):
    """http(s) only, with markdown-destination breakout closed: a ')' or space inside the
    URL would end the link early and let the remainder render as attacker markdown."""
    if not (u and str(u).startswith(("https://", "http://"))):
        return None
    # Encode every ASCII control/space, not only literal spaces; otherwise a newline or
    # tab can escape a Markdown destination even though the scheme check passed.
    u = re.sub(r"[\x00-\x20\x7f]", lambda m: f"%{ord(m.group()):02X}", str(u))
    return u.replace("(", "%28").replace(")", "%29")


# Promo and partner prefixes defeat title-stem dedupe ("OC Event: X" vs "[Partner Event] X" vs "X").
PROMO_PREFIX = re.compile(r"^\s*(?:\[[^\]]{1,30}\]|\([^)]{1,30}\))?\s*(?:(?:oc|la|sf|lv|sd) event|discount|free|partner event|repost)?[\s:\u2013-]*", re.I)


# Cross-posted listings retitle the same event with the metro spelled out or abbreviated
# ("Group Night San Francisco" and "Group Night SF"): fold long metro names to their short
# forms before truncation so both stems agree. Short forms are never expanded (there are no
# safe word boundaries inside a stem).
METRO_STEM_FOLD = (("sanfrancisco", "sf"), ("sandiego", "sd"), ("losangeles", "la"),
                   ("lasvegas", "lv"), ("orangecounty", "oc"))


def fold_alnum(text):
    """Letters and digits of any script: NFKD, combining marks dropped, lower case."""
    decomposed = unicodedata.normalize("NFKD", text or "").lower()
    return "".join(ch for ch in decomposed if ch.isalnum() and not unicodedata.combining(ch))


def title_stem(title):
    s = fold_alnum(PROMO_PREFIX.sub("", (title or "").lower()))
    for long_form, short_form in METRO_STEM_FOLD:
        s = s.replace(long_form, short_form)
    return s[:55]


def stem_or_id(ev):
    """Merge identity of an event: its title stem, or its own id when the title has no letter
    or digit (two such events must never merge on an empty stem)."""
    return title_stem(ev["title"]) or "id:" + str(ev["id"])

def to_city_time(iso, tz):
    """Convert an ISO timestamp to the city's local wall time (naive local strings pass through)."""
    if not iso: return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo: dt = dt.astimezone(tz)
        return dt.strftime("%Y-%m-%dT%H:%M")
    except Exception:
        # unparseable start -> None so the event lands in the no_start counter
        # instead of passing date filters as garbage text
        return None


HASH_ID_MARK = ":sha1:"  # unambiguous: a native id could legitimately start with "h"


def stable_id(source, raw_id, title, start):
    """Stable per-event id: the source's own id when present, else a content hash."""
    if raw_id:
        return f"{source}:{raw_id}"
    import hashlib
    # A title without a letter or digit has no stem: hash the whole raw title instead, so
    # two such events at the same start still get different ids.
    stem = fold_alnum(title)[:40] or (title or "")
    digest = hashlib.sha1((stem + "|" + str(start)).encode("utf-8", "replace")).hexdigest()[:10]
    return f"{source}{HASH_ID_MARK}{digest}"

def topic(title, desc="", host=""):
    blob = f"{title} {desc}"
    if SPORT_SOCIAL_TITLE.search(title): return "other"
    if CYBER_KW.search(blob): return "cyber"
    if AI_KW.search(blob) or AI_TITLE_KW.search(title): return "ai"  # AI wins: robotics/ROS = ai
    if HW_KW.search(title) or HW_TITLE_KW.search(title): return "hardware"
    if NONTECH_TITLE.search(title): return "other"
    if HW_KW.search(desc): return "hardware"
    if TECH_KW.search(blob) or TECH_TITLE_KW.search(title):
        return "tech"
    # Exact-match technical organizers recover opaque recurring titles without letting
    # arbitrary host boilerplate (or broad "AI" group names) force unrelated cross-posts in.
    for host_topic, rx in HOST_TOPIC_RULES:
        if rx.search(host or ""): return host_topic
    return "other"

def event_format(title, desc=""):
    if INSTRUCTIONAL_TITLE.search(title): return "workshop"
    if SOCIAL_TITLE_KW.search(title): return "social"
    blob = f"{title} {desc}"
    for name, rx in FORMAT_RULES:
        if rx.search(blob): return name
    return "talk"

def registry_hit(ev):
    # Prefer a group/host identity across the whole registry before falling back to
    # titles. Otherwise a title that names one group could match an earlier entry before
    # its real host reaches the entry that owns it.
    host = ev.get("host") or ""
    for g in REGISTRY:
        if g["_re"].search(host): return g
    for g in REGISTRY:
        if g["_re"].search(ev["title"]): return g
    return None

def is_excluded(e):
    """True for an excluded host, a course-style title, or a venue that reads "For venue..."."""
    return bool(any(rx.search(e.get("host") or "") for rx in EXCLUDED_HOST) or COURSE_TITLE.search(e["title"])
                or (e.get("venue") or "").lower().startswith("for venue"))

def load(pattern):
    """Items of every usable items file whose name matches the pattern (see _ITEMS)."""
    out = []
    for name in sorted(_ITEMS):
        if fnmatch.fnmatchcase(name, pattern):
            out.extend(_ITEMS[name])
    return out

GENERIC_LUMA_ORGANIZERS = {"personal"}
_EMAIL_NAME = re.compile(r"\S+@\S+\.\S+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\((?:https?://|mailto:)[^)]*\)")
_MD_ESC = re.compile(r"\\([-*_#.!()\[\]])")
_MD_HEADING = re.compile(r"(^| )#{1,6} ")


def luma_host(it):
    """Organizer name, or up to two real hosts when the calendar name is generic."""
    org = clean((it.get("organizer") or {}).get("name"), 80)
    if org and org.lower() not in GENERIC_LUMA_ORGANIZERS:
        return org
    picked = []
    seen = set()
    hosts = it.get("hosts")
    if isinstance(hosts, list):
        for entry in hosts:
            if not isinstance(entry, dict):
                continue
            name = clean(entry.get("name"), 80)
            if not name:
                continue
            if _EMAIL_NAME.search(name):
                continue
            if name.lower() in GENERIC_LUMA_ORGANIZERS:
                continue
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            picked.append(name)
            if len(picked) == 2:
                break
    joined = clean(", ".join(picked), 80) if picked else ""
    if not joined or joined.lower() in GENERIC_LUMA_ORGANIZERS:
        return None
    return joined


def drop_leading_title(text, title):
    """Drop leading lines that equal the title after case folding and whitespace collapse."""
    if not isinstance(text, str):
        return ""
    folded = re.sub(r"\s+", " ", title or "").strip().casefold()
    if not folded:
        return text
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = re.sub(r"\s+", " ", lines[index]).strip().casefold()
        if line != folded:
            break
        index += 1
    return "\n".join(lines[index:])


def summary_text(raw, n=600):
    """Display text from the raw description desc uses, cut on a word boundary."""
    s = clean(raw, 100000)
    s = _MD_LINK.sub(r"\1", s)
    s = s.replace("**", "").replace("__", "")
    s = _MD_ESC.sub(r"\1", s)
    s = _MD_HEADING.sub(r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= n:
        return s
    cut = s.rfind(" ", 0, n)
    if cut > n * 0.6:
        s = s[:cut]
    else:
        s = s[:n]
    return s.rstrip(" ,;:-(") + "\u2026"


def eventbrite_clock(raw):
    """Normalize an Eventbrite clock to HH:MM. A 12-hour '7:00 PM' is accepted."""
    text = str(raw if raw else "00:00")
    m12 = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AP]M)\s*$", text, re.I)
    if m12:  # tolerate a 12h-format time ("7:00 PM") instead of silently dropping the event
        hh = int(m12.group(1)) % 12 + (12 if m12.group(3).upper() == "PM" else 0)
        return f"{hh:02d}:{m12.group(2)}"
    return text


def keep_end(start, end):
    """Keep a city-local end only when it is a string strictly after start."""
    if isinstance(end, str) and isinstance(start, str) and end > start:
        return end
    return None


def load_luma_details(work):
    """Schema-1 sidecar of display text. Any other file is an empty map."""
    path = Path(work) / "luma-details.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict) or data.get("schema") != 1:
        return {}
    details = data.get("details")
    if not isinstance(details, dict):
        return {}
    return details


def luma_detail_text(event_id):
    """Non-empty sidecar description for one Luma id, else ""."""
    if not isinstance(event_id, str):
        return ""
    entry = LUMA_DETAILS.get(event_id)
    if not isinstance(entry, dict):
        return ""
    text = entry.get("description")
    if not isinstance(text, str) or not text.strip():
        return ""
    return text


LUMA_DETAILS = load_luma_details(WORK)


def _note_skipped(slug, source, count):
    if count:
        print(f"WARN: {slug} {source}: {count} items skipped (unexpected shape)", file=sys.stderr)


# The three source loaders below look similar but differ in real ways (Luma category
# shortcut, Meetup isPaidEvent inversion, Eventbrite region drop and URL synthesis). They are
# kept separate on purpose: a table-driven mapper would cost more than the repetition saves.
def collect_city(city):
    slug = city["slug"]
    tz = ZoneInfo(city.get("tz", "America/Los_Angeles"))
    events, eb_out = [], []
    # Reading one item never stops the run: an item that is not an object, or whose nested
    # values have a type the code does not expect, is skipped and counted per source.
    skipped = 0
    # luma_city:null = this city has no Luma leg. Skip Luma items even if a stale work
    # folder still contains them.
    luma_items = load(f"items-{slug}-luma*.json") if city.get("luma_city") else []
    for it in luma_items:
        try:
            if not isinstance(it, dict):
                raise TypeError("item is not an object")
            loc = it.get("location") or {}; tick = it.get("ticketing") or {}
            # Sidecar text fills summary when the raw description is blank. desc stays raw.
            raw_desc = it.get("description")
            side_text = luma_detail_text(it.get("id"))
            t = clean(it.get("name"), 160); d = clean(raw_desc, 200)
            h = luma_host(it)
            start = to_city_time(it.get("startAt"), tz)
            end = keep_end(start, to_city_time(it.get("endAt"), tz))
            side_text = drop_leading_title(side_text, t)
            l_summary = summary_text(raw_desc)
            if not (isinstance(raw_desc, str) and raw_desc.strip()) and side_text:
                l_summary = summary_text(side_text)
            # Luma's location fields come back null, so `city` here is the DISCOVER-QUERY metro
            # (accurate at metro granularity only). Luma's category shelf is loose ("ai" includes
            # generic networking): trust it only as a tiebreaker, never over our own keywords.
            t_topic = topic(t, d, h)
            if t_topic == "other" and it.get("category") == "ai" and not LUMA_SHELF_NOISE.search(t):
                t_topic = "tech"
            events.append(dict(id=stable_id("luma", it.get("id"), t, start),
                source="luma", title=t, url=safe_url(it.get("lumaUrl")), start=start,
                online=(loc.get("locationType") == "online"), venue=clean(loc.get("fullAddress") or "", 100) or None,
                city=loc.get("city") or city["label"], host=h,
                free=tick.get("isFree"), topic=t_topic, desc=d,
                summary=l_summary, end=end))
        except Exception:
            skipped += 1
    _note_skipped(slug, "luma", skipped)
    skipped = 0
    for it in load(f"items-{slug}-meetup-*.json"):
        try:
            if not isinstance(it, dict):
                raise TypeError("item is not an object")
            ven = it.get("venue") or {}
            t = clean(it.get("eventName"), 160); d = clean(it.get("eventDescription"), 200)
            online = (bool(it.get("isOnline")) or ven.get("venueType") == "online"
                      or bool(VIRTUAL_TITLE.search(t))
                      or (not ven.get("name") and bool(REMOTE_MEETING_URL.search(d))))
            h = clean(it.get("organizedByGroup") or (it.get("group") or {}).get("name"), 80) or None
            start = to_city_time(it.get("startDateTime"), tz)
            end = keep_end(start, to_city_time(it.get("endDateTime"), tz))
            m_summary = summary_text(it.get("eventDescription"))
            m_topic = topic(t, d, h)
            events.append(dict(id=stable_id("meetup", it.get("eventId"), t, start),
                source="meetup", title=t, url=safe_url(it.get("eventShortUrl") or it.get("eventUrl")),
                start=start, online=online, venue=clean(ven.get("name") or "", 100) or None,
                city=ven.get("city") or ("online" if online else city["label"]),
                host=h,
                free=(not it.get("isPaidEvent")) if it.get("isPaidEvent") is not None else None,
                topic=m_topic, desc=d, summary=m_summary, end=end))
        except Exception:
            skipped += 1
    _note_skipped(slug, "meetup", skipped)
    skipped = 0
    # A city whose "eventbrite" object lists a region keeps only in-person items from those
    # places; a city with no region list keeps every Eventbrite item.
    region_list = (city.get("eventbrite") or {}).get("region")
    region = {str(x).lower() for x in region_list} if isinstance(region_list, list) and region_list else set()
    for it in load(f"items-{slug}-eventbrite*.json"):
        try:
            if not isinstance(it, dict):
                raise TypeError("item is not an object")
            ven = it.get("primary_venue") or {}; addr = ven.get("address") or {}
            vcity = clean(addr.get("city"), 80); online = bool(it.get("is_online_event"))
            t = clean(it.get("name"), 160)
            if region and not online and vcity and vcity.lower() not in region:
                eb_out.append(f"{t[:60]} ({vcity})"); continue
            eid = it.get("eid") or it.get("eventbrite_event_id") or it.get("id")
            url = safe_url(it.get("url") or (f"https://www.eventbrite.com/e/{eid}" if eid else None))
            raw_time = eventbrite_clock(it.get("start_time"))
            start = to_city_time(it.get("start_datetime") or (f"{it.get('start_date')}T{raw_time}" if it.get("start_date") else None), tz)
            eb_end = None
            if not it.get("hide_end_date"):
                eb_day = it.get("end_date")
                eb_clock = it.get("end_time")
                if eb_day and isinstance(eb_clock, str) and eb_clock.strip():
                    eb_end = to_city_time(f"{eb_day}T{eventbrite_clock(eb_clock)}", tz)
            end = keep_end(start, eb_end)
            d = clean(it.get("summary"), 200)
            e_summary = summary_text(it.get("summary"))
            h = clean((it.get("primary_organizer") or {}).get("name"), 80) or None
            events.append(dict(id=stable_id("eventbrite", eid, t, start),
                source="eventbrite", title=t, url=url, start=start, online=online,
                venue=clean(ven.get("name") or "", 100) or None, city=vcity or ("online" if online else "?"),
                host=h, free=None, topic=topic(t, d, h), desc=d, summary=e_summary, end=end))
        except Exception:
            skipped += 1
    _note_skipped(slug, "eventbrite", skipped)
    return events, eb_out


def _norm_host(h):
    return re.sub(r"[^a-z0-9]+", "", (h or "").lower())


def process_city(city):
    slug = city["slug"]
    raw, eb_out = collect_city(city)
    kept, seen, past = [], {}, 0
    no_title = no_start = deduped = 0
    for ev in sorted(raw, key=lambda e: SOURCE_ORDER[e["source"]]):
        if not ev["title"]:
            no_title += 1; continue
        if not ev["start"]:
            no_start += 1; continue
        if ev["start"][:10] < TODAY: past += 1; continue
        stem = stem_or_id(ev)
        no_stem = stem.startswith("id:")  # real stems hold letters and digits only
        # An event whose title has no letter or digit takes part in no merge tier but the
        # exact one on its own id (so the same listing from two legs still merges).
        # Two-tier dedupe:
        #  1) same stem + same EXACT start datetime = the same event, merge regardless of
        #     host (cross-posted "[Partner Event] X" reposts share the wall-clock start).
        #  2) same stem + same DAY but different time: two events UNLESS hosts look like
        #     the same org (prefix match or either empty), which keeps luma/meetup cross-listings
        #     merged ("DEVx" vs "DEVx San Diego") while distinct groups' generic-title
        #     events ("AI Happy Hour") stay separate.
        exact_key = ("dt", stem, ev["start"][:16])
        day_key = ("di" if no_stem else "d", stem, ev["start"][:10])
        target = None
        if exact_key in seen:
            target = exact_key
        elif not no_stem and len(stem) >= 20:
            # reseller and duplicate listings retitle the same event ("DISCOUNT - Summit
            # Tickets" vs "Summit 2026"): same day and one stem containing the other = same event
            day = ev["start"][:10]
            for (kind, other_stem, *rest) in list(seen.keys()):
                if kind == "d" and rest and rest[0] == day and len(other_stem) >= 20 \
                   and other_stem != stem \
                   and (stem in other_stem or other_stem in stem):
                    target = ("d", other_stem, day)
                    break
        if target is None and day_key in seen:
            h_new, h_old = _norm_host(ev.get("host")), _norm_host(seen[day_key].get("host"))
            if (not h_new or not h_old or h_new.startswith(h_old) or h_old.startswith(h_new)):
                target = day_key
            else:
                host_key = ("dh", stem, ev["start"][:10], h_new[:20])
                if host_key in seen:
                    target = host_key
                else:
                    seen[exact_key] = seen[host_key] = ev
                    kept.append(ev)
                    continue
        if target is not None:
            deduped += 1
            # only record a GENUINELY different url (same-url dupes from overlapping
            # keyword legs were inflating the "+N sources" badge on ~57 of 58 merges)
            also_url = safe_url(ev["url"])
            if also_url and also_url != seen[target].get("url"):
                seen[target]["also"] = sorted(set((seen[target].get("also") or []) + [also_url]))
            continue
        seen[exact_key] = seen[day_key] = ev
        kept.append(ev)
    kept.sort(key=lambda e: e["start"])

    hit_groups = set()
    if city.get("registry"):
        for e in kept:
            g = registry_hit(e)
            if g:
                e["group_registry"] = g["name"]
                if e["topic"] in ("other", "tech"): e["topic"] = g["category"]
                hit_groups.add(g["name"])
                # the exclusion exemption requires the HOST to match the registry group:
                # a commercial "Python Bootcamp" title alone must not borrow a group's pass
                e["_reg_host"] = bool(g["_re"].search(e.get("host") or ""))

    for e in kept:
        e["format"] = event_format(e["title"], e.get("desc") or "")

    radar, mills, nontech, online = [], [], [], []
    for e in kept:
        milled = is_excluded(e) and not e.get("_reg_host")
        e.pop("_reg_host", None)
        if milled: mills.append(e); e["bucket"] = "training_mill"
        elif e["online"]: online.append(e); e["bucket"] = "online"
        elif e["topic"] == "other" and not e.get("group_registry"): nontech.append(e); e["bucket"] = "non_tech"
        else: radar.append(e); e["bucket"] = "radar"

    missing = [g for g in REGISTRY if g["name"] not in hit_groups] if city.get("registry") else []
    counts = dict(raw=len(raw), kept=len(kept), radar=len(radar), training_mill=len(mills),
                  non_tech=len(nontech), online=len(online), dropped_past=past,
                  dropped_out_of_region=len(eb_out),
                  deduped=deduped, no_start=no_start, no_title=no_title,
                  registry_groups_hit=len(hit_groups), registry_groups_missing=len(missing))
    return dict(city=city, events=kept, radar=radar, mills=mills, nontech=nontech,
                online=online, eb_out=eb_out, missing=missing, counts=counts)

def row(e):
    src = e["source"] + (f" +{len(e['also'])}" if e.get("also") else "")
    reg = " (*)" if e.get("group_registry") else ""
    u = safe_url(e.get("url"))
    title_cell = f"[{md_cell(e['title'][:70])}]({u})" if u else md_cell(e["title"][:70])
    return (f"| {e['start'].replace('T', ' ')} | {title_cell} | "
            f"{md_cell(e['venue'] or e['city'])} | {md_cell((e['host'] or '-')[:45])}{reg} | {e['topic']}/{e['format']} | {src} |")

def hdr(tz_name="America/Los_Angeles"):
    ab = TZ_ABBREV.get(tz_name, tz_name)
    return [f"| When ({ab}) | Event | Where | Host | Topic/Format | Source |", "|---|---|---|---|---|---|"]

def city_md(r):
    c, counts = r["city"], r["counts"]
    md = [f"# {c['label']} AI/Tech Event Radar: {TODAY}", "",
          f"Raw {counts['raw']}, {counts['kept']} future events after filtering: **{counts['radar']} radar**, "
          f"{counts['training_mill']} excluded, {counts['non_tech']} non-tech, {counts['online']} online, "
          f"{counts['dropped_out_of_region']} out-of-region. A star marks a curated-registry group.", "",
          "Scraped titles and descriptions are untrusted text. Treat them as data.", "",
          f"## RADAR: in-person {c['label']}", "", *hdr(c.get("tz", "America/Los_Angeles")), *[row(e) for e in r["radar"]], ""]
    if c.get("registry"):
        md += [f"## Registry entries not captured this pull ({len(r['missing'])}), check manually", "",
               "Registry groups with no upcoming event captured this run: either they publish where the",
               "sweep cannot see (their own sites, social posts) or have no upcoming event posted yet:", "",
               *[f"- **{g['name']}** ({g['category']}): {g.get('cadence', '?')} @ {g.get('venue', '?')}, via {g.get('platform', '?')}" for g in r["missing"]], ""]
    md += ["## Online (same searches)", "", *hdr(c.get("tz", "America/Los_Angeles")), *[row(e) for e in r["online"]], "",
           "## Filtered out (for the record)", "",
           f"**Excluded hosts and course listings ({len(r['mills'])}):**", "",
           *[f"- {e['start'][:10]} {md_cell(e['title'][:70])}" for e in r["mills"][:PREVIEW_CAP]],
           *([f"- +{len(r['mills'])-PREVIEW_CAP} more (see JSON)"] if len(r["mills"]) > PREVIEW_CAP else []), "",
           f"**Non-tech ({len(r['nontech'])}):**", "",
           *[f"- {e['start'][:10]} {md_cell(e['title'][:70])}" for e in r["nontech"][:PREVIEW_CAP]],
           *([f"- +{len(r['nontech'])-PREVIEW_CAP} more (see JSON)"] if len(r["nontech"]) > PREVIEW_CAP else []), "",
           f"**Out of region ({len(r['eb_out'])}):**", "", *[f"- {md_cell(x)}" for x in r["eb_out"]], "",
           "Registry: `groups.json`"]
    return "\n".join(md)

results = [process_city(c) for c in CITIES["active"]]

# radar.sh records legs that failed to start, finish or fetch here; the partial flag goes
# into every payload so consumers can tell a quiet week from a sweep with legs missing.
partial_legs = []
_pl = WORK / "partial-legs.txt"
if _pl.exists():
    partial_legs = [ln.strip() for ln in _pl.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
# An items file that was skipped as unreadable marks its own leg partial too.
partial_legs += [leg for leg in _BAD_ITEM_LEGS if leg not in partial_legs]

now_iso = datetime.now(LA_TZ).isoformat(timespec="seconds")
for r in results:
    slug = r["city"]["slug"]
    d = OUT / slug
    d.mkdir(parents=True, exist_ok=True)
    city_partial = [l for l in partial_legs if l.startswith(slug + "-")]
    payload = dict(schema_version=SCHEMA_VERSION, pulled_at=now_iso, city=slug,
                   label=r["city"]["label"], counts=r["counts"],
                   partial=bool(city_partial), partial_legs=city_partial,
                   source_workdir=str(WORK),
                   source_items=[n for n in sorted(_ITEMS) if fnmatch.fnmatchcase(n, f"items-{slug}-*.json")],
                   events=r["events"])
    payload_json = json.dumps(payload, indent=2, ensure_ascii=False)
    md_text = city_md(r)
    write_atomic(d / f"{TODAY}.json", payload_json)
    write_atomic(d / f"{TODAY}.md", md_text)
    write_atomic(d / "LATEST.json", payload_json)
    write_atomic(d / "LATEST.md", md_text)

# Online events are city-agnostic (the same webinar matches every metro search): publish
# them once, topic-gated (no non-tech noise), deduped across cities, with the list of
# metros whose searches surfaced them.
online_seen = {}
for r in results:
    for e in r["online"]:
        if e["topic"] == "other":
            continue
        # Syndicated Meetup groups assign different native ids to the same webinar. A
        # normalized title stem + wall-clock start is the cross-metro identity while active
        # cities share one timezone.
        okey = (stem_or_id(e), (e["start"] or "")[:16])
        if okey in online_seen:
            base = online_seen[okey]
            base["cities"] = sorted(set(base["cities"] + [r["city"]["slug"]]))
            alt_urls = set(base.get("also") or []) | set(e.get("also") or [])
            if e.get("url") and e["url"] != base.get("url"):
                alt_urls.add(e["url"])
            alt_urls.discard(base.get("url"))
            if alt_urls:
                base["also"] = sorted(alt_urls)
        else:
            online_seen[okey] = {**e, "cities": [r["city"]["slug"]]}
online_collection = sorted(online_seen.values(), key=lambda e: e["start"] or "")

# Cross-city radius overlap is expected (an Irvine event is within 30 miles of Los Angeles):
# keep the event in every city that found it, and tag copies with also_in so consumers
# can collapse the views.
_by_key = {}
for r in results:
    for e in r["radar"]:
        k = (stem_or_id(e), (e["start"] or "")[:16])
        _by_key.setdefault(k, []).append((r["city"]["slug"], e))
for k, hits in _by_key.items():
    if len(hits) > 1:
        slugs = [s for s, _ in hits]
        for s, e in hits:
            e["also_in"] = [x for x in slugs if x != s]

_all_feed_events = [e for r in results for e in r["radar"]] + online_collection
feed = dict(generated_at=now_iso, schema_version=SCHEMA_VERSION,
            timezone_note="event `start` strings are wall time in each city's tz (cities[].tz)",
            topics=sorted({e["topic"] for e in _all_feed_events}),
            formats=sorted({e.get("format", "talk") for e in _all_feed_events}),
            partial=bool(partial_legs), partial_legs=partial_legs, cities=[
    *[dict(slug=r["city"]["slug"], label=r["city"]["label"], status="active",
           tz=r["city"].get("tz", "America/Los_Angeles"),
           pulled_at=now_iso, counts=r["counts"],
           events=[e for e in r["events"] if e["bucket"] == "radar"])
      for r in results],
], online=online_collection)
feed_json = json.dumps(feed, indent=2, ensure_ascii=False)
write_atomic(OUT / "ALL-CITIES.json", feed_json)
# dated feed snapshot: the root feed is overwritten on every run
(OUT / "_feed").mkdir(exist_ok=True)
write_atomic(OUT / "_feed" / f"{TODAY}.json", feed_json)

idx = [f"# Event Radar: all cities: {TODAY}", "",
       "| City | Radar | Online | Raw | Digest |", "|---|---|---|---|---|",
       *[f"| {r['city']['label']} | {r['counts']['radar']} | {r['counts']['online']} | {r['counts']['raw']} | [{r['city']['slug']}/LATEST.md]({r['city']['slug']}/LATEST.md) |"
         for r in results],
       "", f"Combined site feed: `ALL-CITIES.json`, per-city in-person radar + one deduped topic-gated `online` collection ({len(online_collection)} events).",
       *(["", f"PARTIAL PULL: {len(partial_legs)} legs missing: {', '.join(partial_legs)}"] if partial_legs else []),
       ""]
for r in results:
    idx += [f"## {r['city']['label']}: top of radar", "",
            *hdr(r["city"].get("tz", "America/Los_Angeles")), *[row(e) for e in r["radar"][:12]], ""]
write_atomic(OUT / "LATEST.md", "\n".join(idx))
# intentional alias of ALL-CITIES.json (keeps LATEST.{json,md} symmetry; external readers may use either)
write_atomic(OUT / "LATEST.json", feed_json)

summary = " ".join(f"{r['city']['slug']}:radar={r['counts']['radar']}" for r in results)
print(f"{summary} total_radar={sum(r['counts']['radar'] for r in results)}")
print(OUT / "ALL-CITIES.json")
