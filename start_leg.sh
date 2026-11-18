# start_leg LEG ACTOR_SLUG INPUT_FILE OUTPUT_FILE
# Sourced by radar.sh, which sets API (the base URL) and APIFY_TOKEN (the token). The token
# goes to curl as a header on standard input, never as an argument, so no process list shows it.
# Starts one Apify actor run, retries on a timeout, 429 or 5xx, and sets START_ID to the new
# run id (empty when the leg could not start). It prints a NOTE line after a retry that
# worked and a WARN line when the start failed; it always returns 0.
# Accepted risk: Apify run starts are not idempotent, so a 5xx returned after Apify
# had already created the run makes the retry start a second run of the same leg.
# That costs one extra leg run and the second run's id is the one polled.
# Each attempt starts from an empty out file: the run folder is reused by a same-day rerun, and
# curl writes nothing on a timeout, so a leftover file would hand back an earlier run's id.
start_leg() {
  local tries="${RADAR_START_TRIES:-3}"
  local pause="${RADAR_START_SLEEP:-10}"
  # A value that is not a whole number falls back to the default, so the loop never skips.
  case "$tries" in ''|*[!0-9]*) tries=3 ;; esac
  case "$pause" in ''|*[!0-9]*) pause=10 ;; esac
  if [ "${#tries}" -gt 9 ] || [ "$((10#$tries))" -lt 1 ]; then tries=3; fi
  if [ "${#pause}" -gt 9 ]; then pause=10; fi
  tries=$((10#$tries))
  pause=$((10#$pause))
  local n=1
  local code=""
  local prev=""
  START_ID=""
  while [ "$n" -le "$tries" ]; do
    rm -f -- "$4"
    code="$(printf 'Authorization: Bearer %s\n' "$APIFY_TOKEN" \
      | curl -sS -m 30 -X POST -H @- -H "Content-Type: application/json" \
        "$API/acts/$2/runs" -d @"$3" -o "$4" -w '%{http_code}' 2>/dev/null)" || true
    case "$code" in
      [0-9][0-9][0-9]) ;;
      *) code="000" ;;
    esac
    START_ID="$(jq -r '.data.id // empty' "$4" 2>/dev/null || true)"
    if [ -n "$START_ID" ]; then
      if [ "$n" -gt 1 ]; then
        echo "$(date '+%F %T') NOTE: $1 started on try $n after http $prev"
      fi
      return 0
    fi
    if [ "$code" = "000" ] || [ "$code" = "429" ] || [ "${code#5}" != "$code" ]; then
      if [ "$n" -lt "$tries" ]; then
        prev="$code"
        sleep "$pause"
        n=$((n + 1))
        continue
      fi
    fi
    echo "$(date '+%F %T') WARN: $1 failed to start (http $code, tries $n)"
    return 0
  done
  return 0
}
