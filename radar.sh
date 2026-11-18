#!/usr/bin/env bash
# meetups-radar: start up to three kinds of Apify event scraper per city listed in cities.json
# (one Luma run, one Meetup run per keyword, one Eventbrite run in the first week of the
# month), wait for them, then sort the results into one digest page and one JSON feed.
#
# Per city: one Luma leg (unless "luma_city" is null), one Meetup leg per keyword in "kws",
# and one Eventbrite leg when the city has an "eventbrite" object and the day of the month
# is 7 or less. The Python steps (luma_details.py, normalize_events.py) write the digest
# and the feed into RADAR_OUT.
#
# Environment:
#   APIFY_TOKEN              required, an Apify API token (no whitespace or control character)
#   RADAR_OUT                output folder (default: pulls beside this script)
#   RADAR_WORK               run folders and the lock (default: runs beside this script)
#   APIFY_API_BASE           default https://api.apify.com/v2
#   RADAR_POLL_SECONDS       pause between status checks (default 10)
#   RADAR_DEADLINE_SECONDS   stop polling after this long (default 1500)
#   RADAR_TODAY              run date YYYY-MM-DD (default: today)
#   RADAR_LUMA_MAX_FETCH     passed to luma_details.py as --max-fetch when set
#   RADAR_START_TRIES        start attempts per leg (default 3), see start_leg.sh
#   RADAR_START_SLEEP        pause between start attempts in seconds (default 10)
# The numeric settings must be whole numbers of 1 to 9 digits (RADAR_START_TRIES at least 1).
#
# Exit status: 0 when the summary line was printed (partial legs included) or when another
# run holds the lock; 1 when the sorting step failed, when zero legs returned data, or when
# a stale lock could not be removed; 2 for a missing tool, a missing or malformed token, a
# malformed RADAR_TODAY, a bad setting, a configuration problem, or zero legs to run.
# Runs under bash 3.2 (no associative arrays).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

missing=""
for tool in bash curl jq perl python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then missing="$missing $tool"; fi
done
if [ -n "$missing" ]; then
  for tool in $missing; do echo "radar.sh: missing required tool: $tool" >&2; done
  exit 2
fi
if [ -z "${APIFY_TOKEN:-}" ]; then
  echo "radar.sh: APIFY_TOKEN is not set" >&2
  exit 2
fi

OUT="${RADAR_OUT:-$HERE/pulls}"
RUNS="${RADAR_WORK:-$HERE/runs}"
API="${APIFY_API_BASE:-https://api.apify.com/v2}"
POLL="${RADAR_POLL_SECONDS:-10}"
DEADLINE_SECONDS="${RADAR_DEADLINE_SECONDS:-1500}"
STAMP="${RADAR_TODAY:-$(date +%Y-%m-%d)}"
case "$STAMP" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) echo "radar.sh: RADAR_TODAY must look like YYYY-MM-DD" >&2; exit 2 ;;
esac
DAY_OF_MONTH=$((10#${STAMP:8:2}))

# Settings and configuration are checked before the lock, any file or any request.
case "$APIFY_TOKEN" in
  *[[:space:][:cntrl:]]*)
    echo "radar.sh: APIFY_TOKEN holds a whitespace or control character" >&2
    exit 2 ;;
esac
whole_number() { # value: 1 to 9 digits and nothing else
  case "$1" in ''|*[!0-9]*) return 1 ;; esac
  [ "${#1}" -le 9 ]
}
for setting in RADAR_POLL_SECONDS RADAR_DEADLINE_SECONDS RADAR_START_SLEEP RADAR_LUMA_MAX_FETCH RADAR_START_TRIES; do
  value="${!setting:-}"
  if [ -n "$value" ]; then
    if ! whole_number "$value"; then
      echo "radar.sh: $setting must be a whole number of 1 to 9 digits" >&2
      exit 2
    fi
    if [ "$setting" = "RADAR_START_TRIES" ] && [ "$((10#$value))" -lt 1 ]; then
      echo "radar.sh: RADAR_START_TRIES must be at least 1" >&2
      exit 2
    fi
  fi
done
POLL=$((10#$POLL))
DEADLINE_SECONDS=$((10#$DEADLINE_SECONDS))
if ! python3 "$HERE/normalize_events.py" --check "$STAMP" >/dev/null; then
  echo "radar.sh: the configuration check failed, nothing was started" >&2
  exit 2
fi

CITIES="$HERE/cities.json"
export RADAR_OUT="$OUT"

. "$HERE/start_leg.sh"
. "$HERE/lock.sh"

# Delete dated run folders (YYYY-MM-DD) beyond the 8 newest. Nothing else in $RUNS is touched.
prune_runs() {
  ls -1dt "$RUNS"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/ 2>/dev/null | tail -n +9 | while IFS= read -r old; do
    [ -L "${old%/}" ] || rm -rf -- "$old"
  done || true
}

# Single run at a time: a second run on the same day would share the run folder and corrupt
# both. lock.sh takes the lock with mkdir plus an owner file; the run refreshes it as it works,
# and a lock untouched for 45 minutes belongs to a crashed run and is reclaimed.
mkdir -p "$RUNS"
LOCKDIR="$RUNS/.radar.lock.d"
LOCK_RC=0
lock_take "$LOCKDIR" || LOCK_RC=$?
if [ "$LOCK_RC" -eq 1 ]; then
  echo "$(date '+%F %T') another radar run is active, exiting"
  exit 0
elif [ "$LOCK_RC" -ne 0 ]; then
  echo "$(date '+%F %T') ERROR: the stale lock $LOCKDIR could not be removed"
  exit 1
fi
trap 'lock_release "$LOCKDIR"' EXIT

WORK="$RUNS/$STAMP"
mkdir -p "$WORK"
rm -f "$WORK"/items-*.json "$WORK"/.items-*.tmp "$WORK"/.st-*.tmp "$WORK"/st-*.json "$WORK"/start-*.json \
      "$WORK"/in-*.json "$WORK"/manifest.tsv "$WORK"/partial-legs.txt

# Parallel indexed arrays: LEGS[i] is the leg name, LEG_ACTOR[i] its actor, LEG_RID[i] its run id.
LEGS=()
LEG_ACTOR=()
LEG_RID=()

add_leg() { # leg-name actor-slug
  LEGS+=("$1")
  LEG_ACTOR+=("$2")
  LEG_RID+=("")
}

while IFS= read -r city; do
  cslug="$(jq -r '.slug' <<<"$city")"
  LUMA_CITY="$(jq -r '.luma_city // empty' <<<"$city")"
  MCITY="$(jq -r '.meetup.city' <<<"$city")"
  MSTATE="$(jq -r '.meetup.state' <<<"$city")"
  RADIUS="$(jq -r '.meetup.radius' <<<"$city")"
  EB_CITY="$(jq -r 'if (.eventbrite | type) == "object" then (.eventbrite.city // "") else "" end' <<<"$city")"
  EB_ON="$(jq -r '(.eventbrite | type) == "object"' <<<"$city")"

  # luma_city null means no Luma leg for this city.
  if [ -n "$LUMA_CITY" ]; then
    jq -n --arg c "$LUMA_CITY" '{slugs:["ai","tech"],cities:[$c],maxEventsPerCity:80}' > "$WORK/in-$cslug-luma.json"
    add_leg "$cslug-luma" "matyascimbulka~luma-event-scraper"
  fi

  while IFS= read -r kw; do
    jq -n --arg k "$kw" --arg c "$MCITY" --arg st "$MSTATE" --argjson r "$RADIUS" \
      '{mode:"events",searchKeyword:$k,city:$c,state:$st,country:"us",radius:$r,sortBy:"DATETIME",maxResults:40}' \
      > "$WORK/in-$cslug-meetup-$kw.json"
    add_leg "$cslug-meetup-$kw" "filip_cicvarek~meetup-scraper"
  done < <(jq -r '.kws[]' <<<"$city")

  # The Eventbrite leg runs only in the first week of the month.
  if [ "$EB_ON" = "true" ] && [ "$DAY_OF_MONTH" -le 7 ]; then
    if [ -z "$EB_CITY" ]; then
      echo "$(date '+%F %T') WARN: $cslug has an eventbrite object without a city, leg skipped"
    else
      jq -n --arg c "$EB_CITY" \
        '{country:"united-states",city:$c,category:"custom",keyword:"artificial intelligence",maxResults:50,enrichOrganizers:false}' \
        > "$WORK/in-$cslug-eventbrite.json"
      add_leg "$cslug-eventbrite" "aitorsm~eventbrite"
    fi
  fi
done < <(jq -c '.active[]' "$CITIES")

LEG_COUNT=${#LEGS[@]}
if [ "$LEG_COUNT" -eq 0 ]; then
  echo "radar.sh: the configuration gives zero legs to run today" >&2
  exit 2
fi

i=0
while [ "$i" -lt "$LEG_COUNT" ]; do
  leg="${LEGS[$i]}"
  lock_refresh "$LOCKDIR"
  start_leg "$leg" "${LEG_ACTOR[$i]}" "$WORK/in-$leg.json" "$WORK/start-$leg.json"
  LEG_RID[$i]="$START_ID"
  i=$((i + 1))
done
echo "$(date '+%F %T') started $LEG_COUNT legs across $(jq -r '.active | length' "$CITIES") cities"

DEADLINE=$(( $(date +%s) + DEADLINE_SECONDS ))
ITEMS_OK=0
i=0
while [ "$i" -lt "$LEG_COUNT" ]; do
  leg="${LEGS[$i]}"
  rid="${LEG_RID[$i]}"
  i=$((i + 1))
  if [ -z "$rid" ]; then echo "$leg" >> "$WORK/partial-legs.txt"; continue; fi
  ST="?"
  while :; do
    lock_refresh "$LOCKDIR"
    # The header travels on standard input so the token is in no process argument list. The
    # answer goes to a temporary file and replaces the last good status only when it parses
    # and holds a string status: an HTML error page must not erase a good answer.
    printf 'Authorization: Bearer %s\n' "$APIFY_TOKEN" \
      | curl -sS -m 20 -H @- "$API/actor-runs/$rid" -o "$WORK/.st-$leg.tmp" 2>/dev/null || true
    if jq -e '(.data.status | type) == "string"' "$WORK/.st-$leg.tmp" >/dev/null 2>&1; then
      mv -f "$WORK/.st-$leg.tmp" "$WORK/st-$leg.json"
    else
      rm -f "$WORK/.st-$leg.tmp"
    fi
    ST="$(jq -r '.data.status // "?"' "$WORK/st-$leg.json" 2>/dev/null || echo "?")"
    case "$ST" in
      SUCCEEDED) break ;;
      FAILED|ABORTED|TIMED-OUT) echo "$(date '+%F %T') WARN: $leg ended $ST"; break ;;
    esac
    if [ "$(date +%s)" -gt "$DEADLINE" ]; then echo "$(date '+%F %T') WARN: $leg poll deadline"; break; fi
    sleep "$POLL"
  done
  DS="$(jq -r '.data.defaultDatasetId // empty' "$WORK/st-$leg.json" 2>/dev/null || true)"
  printf '%s\t%s\t%s\t%s\n' "$leg" "$rid" "${DS:--}" "$ST" >> "$WORK/manifest.tsv"
  if [ -n "$DS" ] && [ "$ST" = "SUCCEEDED" ]; then
    # Download to a temporary file and require a JSON array before accepting it: a
    # half-written body would otherwise count as a complete leg and shrink the feed.
    printf 'Authorization: Bearer %s\n' "$APIFY_TOKEN" \
      | curl -fsS -m 60 -H @- "$API/datasets/$DS/items?clean=true" -o "$WORK/.items-$leg.tmp" 2>/dev/null || true
    if jq -e 'type=="array"' "$WORK/.items-$leg.tmp" >/dev/null 2>&1; then
      mv "$WORK/.items-$leg.tmp" "$WORK/items-$leg.json"
      ITEMS_OK=$((ITEMS_OK + 1))
    else
      rm -f "$WORK/.items-$leg.tmp"
    fi
  fi
  # A leg without an accepted items file is partial: normalize_events.py stamps it into the payloads.
  if [ ! -s "$WORK/items-$leg.json" ]; then echo "$leg" >> "$WORK/partial-legs.txt"; fi
done

PARTIAL_N=0
if [ -f "$WORK/partial-legs.txt" ]; then PARTIAL_N="$(grep -c . "$WORK/partial-legs.txt" || true)"; fi

# With no data at all the feed stays as it was: an empty night must not overwrite a good one.
if [ "$ITEMS_OK" -eq 0 ]; then
  echo "$(date '+%F %T') ERROR: zero legs returned data, the feed was left as it was"
  prune_runs
  exit 1
fi

# Luma descriptions are blank in the actor output, so luma_details.py fetches display text
# into a sidecar file. A timeout or any other failure warns and the run continues.
lock_refresh "$LOCKDIR"
LUMA_MAX=()
if [ -n "${RADAR_LUMA_MAX_FETCH:-}" ]; then LUMA_MAX=(--max-fetch "$RADAR_LUMA_MAX_FETCH"); fi
LUMA_RC=0
LUMA_LINE="$(perl -e 'alarm shift; exec @ARGV' 900 python3 "$HERE/luma_details.py" "$WORK" \
  --cache "$RUNS/luma-details-cache.json" ${LUMA_MAX[@]+"${LUMA_MAX[@]}"} 2>>"$WORK/radar.log")" || LUMA_RC=$?
echo "$(date '+%F %T') $LUMA_LINE" >> "$WORK/radar.log"
if [ "$LUMA_RC" -ne 0 ]; then
  echo "$(date '+%F %T') WARN: luma details exited $LUMA_RC"
fi

# normalize_events.py writes the digest and the feed. If it fails the feed was not updated.
lock_refresh "$LOCKDIR"
NORM_RC=0
NORM_OUT="$(python3 "$HERE/normalize_events.py" "$WORK" "$STAMP" 2>>"$WORK/radar.log")" || NORM_RC=$?
SUMMARY="$(printf '%s\n' "$NORM_OUT" | sed -n 1p)"
if [ "$NORM_RC" -ne 0 ] || [ -z "$SUMMARY" ]; then
  echo "$(date '+%F %T') ERROR: normalize_events.py failed, the feed was not updated (see $WORK/radar.log)"
  prune_runs
  exit 1
fi

# Cost: only numeric usageTotalUsd values count; jq prints small ones in exponent form (1e-05).
COST_RAW="$(jq -s '([.[] | try .data.usageTotalUsd catch null | select(type == "number")] | add) // 0' "$WORK"/st-*.json 2>/dev/null || echo '?')"
if [[ "$COST_RAW" =~ ^[0-9]+([.][0-9]+)?([eE][-+]?[0-9]+)?$ ]]; then
  COST="$(printf '%.5f' "$COST_RAW")"
else
  COST='?'
fi
echo "$SUMMARY partial_legs=$PARTIAL_N/$LEG_COUNT cost=\$$COST"

prune_runs
