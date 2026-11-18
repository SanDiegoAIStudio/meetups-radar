# lock.sh: the single-run lock of radar.sh. Sourced, never run. Runs under bash 3.2.
#
# A lock is a folder DIR that holds a file DIR/pid with the process id of the shell that took
# it. The owner refreshes the folder's modification time while it works, so a folder that was
# not touched for 45 minutes belongs to a crashed run and may be removed by the next one.
#
#   lock_take DIR     0 taken, 1 held by a run that refreshed it within 45 minutes,
#                     2 older than 45 minutes and could not be removed
#   lock_refresh DIR  sets the modification time to now, only when DIR/pid holds this shell's id
#   lock_release DIR  removes DIR/pid and DIR, only when DIR/pid holds this shell's id
#
# Every function returns 0 or the codes above, so a caller under set -e uses `|| rc=$?`.

_lock_owner() { # DIR: succeeds when DIR/pid holds this shell's process id
  local held=""
  if [ -f "$1/pid" ]; then read -r held < "$1/pid" 2>/dev/null || held=""; fi
  [ -n "$held" ] && [ "$held" = "$$" ]
}

lock_take() {
  local dir="$1"
  if ! mkdir "$dir" 2>/dev/null; then
    if [ -d "$dir" ] && find "$dir" -maxdepth 0 -mmin +45 2>/dev/null | grep -q .; then
      echo "$(date '+%F %T') reclaiming stale radar lock (older than 45 minutes)"
      rm -f "$dir/pid" 2>/dev/null || true
      if ! rmdir "$dir" 2>/dev/null; then return 2; fi
      if ! mkdir "$dir" 2>/dev/null; then return 1; fi
    else
      return 1
    fi
  fi
  printf '%s\n' "$$" > "$dir/pid"
  return 0
}

lock_refresh() {
  if _lock_owner "$1"; then perl -e 'utime undef, undef, $ARGV[0]' "$1" 2>/dev/null || true; fi
  return 0
}

lock_release() {
  if _lock_owner "$1"; then
    rm -f "$1/pid" 2>/dev/null || true
    rmdir "$1" 2>/dev/null || true
  fi
  return 0
}
