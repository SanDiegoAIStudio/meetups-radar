# meetups-radar

meetups-radar finds AI and tech meetups in the cities you list. One shell script starts up to three kinds of Apify event scraper per city (Luma, Meetup and Eventbrite), waits for them, and a Python step sorts what came back into a digest page and a JSON feed. You can try the sorting step without an Apify account, on the sample items in `fixtures/`.

## What you get

Each run writes a digest per city, a cross-city index page and one JSON feed. These are the first 19 lines of `pulls/LATEST.md`, written by the sorting step run on the sample items:

```
# Event Radar: all cities: 2026-11-02

| City | Radar | Online | Raw | Digest |
|---|---|---|---|---|
| San Diego | 26 | 5 | 43 | [san-diego/LATEST.md](san-diego/LATEST.md) |
| Los Angeles | 10 | 9 | 21 | [los-angeles/LATEST.md](los-angeles/LATEST.md) |
| Orange County | 10 | 2 | 14 | [orange-county/LATEST.md](orange-county/LATEST.md) |
| SF Bay | 22 | 8 | 35 | [sf-bay/LATEST.md](sf-bay/LATEST.md) |
| Las Vegas | 8 | 2 | 11 | [las-vegas/LATEST.md](las-vegas/LATEST.md) |

Combined site feed: `ALL-CITIES.json`, per-city in-person radar + one deduped topic-gated `online` collection (20 events).

## San Diego: top of radar

| When (PT) | Event | Where | Host | Topic/Format | Source |
|---|---|---|---|---|---|
| 2026-11-04 19:00 | Monthly Open Bench for Builders, Menders and Crafters | Repair Cafe Hall | Example Makers Guild (*) | hardware/talk | meetup |
| 2026-11-07 10:00 | [AI Hosts Evening](https://lu.ma/personal-hosts) | San Diego | Makers & Backers, Host A | ai/talk | luma |
| 2026-11-08 10:00 | [AI Open Calendar](https://lu.ma/personal-no-hosts) | San Diego | - | ai/talk | luma |
```

The sample events are invented. Some host names in them, and the `host_topics` rules in `groups.json` that match those hosts, are the names of real public tech groups, so the rules can be seen working; the events listed under those names are made up. The sorting step prints two lines: the summary line and, on a second line, the path of the feed it wrote. This is the summary line it printed for the sample items (command in the next section):

```
san-diego:radar=26 los-angeles:radar=10 orange-county:radar=10 sf-bay:radar=22 las-vegas:radar=8 total_radar=76
```

`ALL-CITIES.json` has these top-level keys: `generated_at`, `schema_version`, `timezone_note`, `topics`, `formats`, `partial`, `partial_legs`, `cities` and `online`. Each entry of `cities` has `slug`, `label`, `status`, `tz`, `pulled_at`, `counts` and `events`; the in-person events of that city sit in its `events`. The events that happen online sit in the top-level `online`, merged across cities, and carry one more key, `cities`.

Each event has these keys:

| Key | Meaning |
|---|---|
| `id` | stable id: the source name and the source's own id, for example `meetup:457519973`, or a content hash when the source gave no id |
| `source` | `luma`, `meetup` or `eventbrite` |
| `title` | event title |
| `url` | link to the event page, or null when the source gave no `http` or `https` link |
| `start` | wall time in the city's time zone, `YYYY-MM-DDTHH:MM` |
| `end` | end time in the same form, or null when the source gave none or it was not after the start |
| `online` | true when the event is online |
| `venue` | venue name, or null |
| `city` | the venue's city when the source gave one; otherwise `online` for an online Meetup or Eventbrite event, the city's `label` for a Luma event or an in-person Meetup event, and `?` for an in-person Eventbrite event |
| `host` | organizing group, or null |
| `free` | true or false when the source says whether the event is paid, otherwise null |
| `topic` | `ai`, `cyber`, `hardware` or `tech`; in a city with `registry` set, an event of a group listed in `groups.json` whose own text gave `tech` or no topic takes that group's `category` instead |
| `format` | `conference`, `hackathon`, `social`, `talk` or `workshop` |
| `desc` | the source's description, cut short |
| `summary` | a plain-text cut of the description for display |
| `bucket` | where the sorting step put the event: `radar` (the main list), `online`, `non_tech` or `training_mill`; the feed carries the `radar` and `online` events |
| `group_registry` | present when the host matches a group in `groups.json`: that group's name |
| `also` | present when the same event was found on other listings: their `http` and `https` links |
| `also_in` | present on an in-person event that more than one city found (their search areas overlap): the slugs of the other cities that list it |

## Quick start without an account

```
python3 normalize_events.py fixtures 2026-11-02
```

The first argument is the folder that holds the item files and the second is the run date. The digest and feed land in `pulls/` beside the script: `pulls/LATEST.md`, `pulls/ALL-CITIES.json`, and one folder per city with a dated page and a `LATEST.md`. Set `RADAR_OUT` to write somewhere else.

An items file that does not hold a JSON list, and an item that does not have the expected shape, is skipped with a `WARN` line on stderr; a skipped file's leg is marked partial in the feed. When the folder holds no usable items file at all, the step writes nothing, so an earlier feed stays as it was, and exits 1. A wrong command line, a folder that does not exist or a date that is not a real `YYYY-MM-DD` date exits 2 with nothing written.

## Quick start with Apify

```
export APIFY_TOKEN=your-token
./radar.sh
```

The script starts one Luma run, one Meetup run per keyword and, in the first week of the month, one Eventbrite run for each city that has an `eventbrite` block. It polls until every run ends, downloads the results into `runs/<date>/`, fetches Luma descriptions (see below), runs the sorting step and prints a summary line that ends in `partial_legs=N/M cost=$X`. A leg that failed to start, ended in failure or returned something that was not a list is counted in `partial_legs`, listed in `runs/<date>/partial-legs.txt` and marked in the feed, so a short feed is never mistaken for a complete one. When no leg returns data, the script prints an `ERROR` line, leaves the existing feed as it was and exits 1.

The token reaches curl on standard input, so it does not show in the process list.

The Luma actor returns events without their descriptions, so `luma_details.py` asks Luma for each one at `https://api.lu.ma/event/get`, a public endpoint that Luma does not document, with the user agent `meetups-radar/1`: at most 400 requests a run (`RADAR_LUMA_MAX_FETCH`), half a second apart, with answers kept in `runs/luma-details-cache.json` for 21 days. This step sends no token. If it fails, the run goes on and those events have no summary.

## What you need

- bash, curl, jq, perl and python3: `radar.sh` checks for them at the start, names any that is missing and exits 2. It runs under bash 3.2. curl must be 7.55 or newer, because the token header is read from standard input with `-H @-`.
- Python 3.9 or newer, standard library only, and the system time zone database that `zoneinfo` reads
- for the tests, bash at `/bin/bash`
- an Apify account whose plan lets you run these three Store actors:
  - `matyascimbulka/luma-event-scraper` (Luma)
  - `filip_cicvarek/meetup-scraper` (Meetup)
  - `aitorsm/eventbrite` (Eventbrite)

The actors belong to their authors. Each run bills usage to your Apify account under that actor's own pricing; check the actor pages before you schedule anything.

## Run it every week

```
0 6 * * 1 APIFY_TOKEN=your-token /path/to/meetups-radar/radar.sh >> /path/to/meetups-radar/runs/cron.log 2>&1
```

The script takes a lock, so a second run that starts while the first is still going exits at once with status 0. The running script refreshes the lock as it works, and a lock left untouched for 45 minutes is treated as left by a crashed run and taken over. When a run ends, with its summary line or with a failure of the sorting step or zero data, it deletes dated run folders (`YYYY-MM-DD`) beyond the 8 most recently changed and removes nothing else from the work folder.

## Configure

`cities.json` holds an `active` list. Each city has:

| Key | Meaning |
|---|---|
| `slug` | folder and id prefix, for example `san-diego` |
| `label` | name shown on the page |
| `tz` | IANA time zone used for event times |
| `luma_city` | city name sent to the Luma actor, or `null` for no Luma leg |
| `meetup` | `city`, `state` and `radius` (miles) for the Meetup actor |
| `kws` | one Meetup search per keyword |
| `registry` | `true` to mark the groups listed in `groups.json` |
| `eventbrite` | optional object with `city` (the Eventbrite city slug) and `region` (lowercase city names; results outside it are dropped) |

`groups.json` has three lists:

| Key | Meaning |
|---|---|
| `groups` | groups you want marked with a star: `name`, `match` (a regular expression tested against host and title), `platform`, `category`, `cadence`, `venue`, `note` |
| `host_topics` | `topic` and `match`: hosts whose events carry no keyword but belong to a topic anyway |
| `exclude_hosts` | regular expressions for hosts whose events go to the filtered-out list, such as certification mills |

`python3 normalize_events.py --check` checks both files and prints `config ok`, or prints one line per problem on stderr and exits 2. `radar.sh` runs the same check before it takes the lock or sends any request.

Environment variables for `radar.sh`:

| Variable | Default | Meaning |
|---|---|---|
| `APIFY_TOKEN` | none, required | Apify API token |
| `RADAR_OUT` | `pulls` beside the script | where the digest and feed are written |
| `RADAR_WORK` | `runs` beside the script | run folders, the lock and the Luma cache |
| `APIFY_API_BASE` | `https://api.apify.com/v2` | API base URL |
| `RADAR_POLL_SECONDS` | `10` | pause between status checks |
| `RADAR_DEADLINE_SECONDS` | `1500` | stop waiting for runs after this long |
| `RADAR_TODAY` | today | run date, `YYYY-MM-DD`; also decides whether the Eventbrite leg runs (day 1 to 7) |
| `RADAR_LUMA_MAX_FETCH` | `400` | most Luma description requests per run |
| `RADAR_START_TRIES` | `3` | start attempts per leg |
| `RADAR_START_SLEEP` | `10` | seconds between start attempts |

The numeric settings take whole numbers of 1 to 9 digits, and `RADAR_START_TRIES` must be at least 1.

Exit status of `radar.sh`:

- 0: the summary line was printed (partial legs included), or another run holds the lock.
- 1: the sorting step failed, zero legs returned data, or a stale lock could not be removed.
- 2: a missing tool, a missing or malformed token, a malformed `RADAR_TODAY`, a bad setting, a configuration problem, or zero legs to run.

The script runs under `set -e`, so any other command that fails stops it with that command's own status; for example, a run folder that cannot be created ends the run with 1.

## Tests

```
python3 -m unittest discover -s tests
python3 tests/mutate.py
```

The first command runs the unit tests. The second makes one small change at a time to the scripts in a temporary copy and checks that the tests fail each time. The tests talk to a local stand-in for Apify that listens on 127.0.0.1, so no request leaves the machine and no account is needed.

## Limits

- The Eventbrite leg covers San Diego only, because only that city has an `eventbrite` block. Give another city its own `eventbrite` values to add it.
- The three actors are other authors' services. Their output can change, and a change can break a leg until the sorting step is updated.
- Luma descriptions come from an endpoint Luma does not document, so it can change or stop without notice. Some descriptions are missing, and the digest then shows no summary for that event. An event that answers 404 or 410 is cached with an empty description and asked again after 3 days.
- The topic gate is keyword rules. It misses some events and lets some through. The digest lists what it filtered out so you can see what was dropped.
- Every leg starts at once: the shipped `cities.json` starts 44 runs together, 45 in the first week of the month. A start that gets no answer, a 429 or a 5xx is tried again; any other 4xx, such as a plan that refuses more runs at once, is not, and that leg is marked partial.
- Run by hand, the sorting step does not check that every active city has an items file. A city with none is written with no events and the feed is not marked partial. `radar.sh` does not have this gap: a leg that leaves no items file is listed as partial.
- Taking over a stale lock is not atomic: two runs that start in the same moment against a lock older than 45 minutes can both go ahead.
- `tests/mutate.py` does not run the unchanged suite first, and it counts any failing run or a run past its time limit as killed. Run the first test command and see it pass before you read the mutation count.

## License

MIT. See `LICENSE`.

## Built at San Diego AI Studio

The radar comes out of San Diego AI Studio, the builders' community in San Diego where builders show the systems they run. More projects from the community: https://sandiegoaistudio.com/projects
