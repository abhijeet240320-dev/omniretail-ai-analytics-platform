#!/usr/bin/env bash
# =====================================================================
# OmniRetail - project-local PostgreSQL control script
# =====================================================================
# WHY A LOCAL CLUSTER (and why it is NOT inside the project folder)
# -----------------------------------------------------------------
# 1. Reproducibility. The project needs a database whose version, port,
#    roles and schemas are known and disposable. Using whatever happens
#    to be installed globally means the project breaks on another machine.
#
# 2. OneDrive. A PostgreSQL data directory is a live, constantly-fsynced
#    tree of files that must be opened with exclusive OS-level sharing
#    semantics. OneDrive (and antivirus real-time scanning) holds handles
#    on those files, which produces exactly the failure we hit:
#
#        LOG: could not open file "./server.log": sharing violation
#        LOG: You might have antivirus, backup, or similar software
#             interfering with the database system.
#
#    The postmaster accepted TCP connections but could never fork a
#    backend, so every client hung and timed out. Putting the cluster on
#    the local disk outside the synced folder fixes it permanently.
#
# 4. THE pg_ctl TRAP (the second half of the same bug). On this machine,
#    starting the cluster via `pg_ctl start` produced:
#
#        LOG: client backend (PID n) was terminated by exception 0xC0000142
#
#    0xC0000142 is STATUS_DLL_INIT_FAILED. The postmaster came up and
#    listened on the port, but EVERY backend it forked - and every
#    autovacuum worker - died during DLL initialisation, so each client
#    hung until its connection timed out. A cluster started with the same
#    binaries but by launching postgres.exe DIRECTLY works perfectly.
#
#    So this script does not call `pg_ctl start`. It launches postgres.exe
#    itself, in the background, and then waits for the server to answer.
#    `pg_ctl stop` is still used for shutdown, because shutting down is
#    a signal to the running postmaster and does not involve forking.
#
# 3. Safety. The cluster directory is machine-specific state, not source
#    code. It must never be committed or synced.
#
# USAGE
# -----
#   bash backend/scripts/db/pg_ctl.sh init      # create the cluster (once)
#   bash backend/scripts/db/pg_ctl.sh start
#   bash backend/scripts/db/pg_ctl.sh stop
#   bash backend/scripts/db/pg_ctl.sh status
#   bash backend/scripts/db/pg_ctl.sh psql      # open a shell as superuser
# =====================================================================
set -euo pipefail

# --- Configuration ---------------------------------------------------
# Local-disk location, deliberately OUTSIDE the OneDrive-synced project.
PGDATA_DIR="${OMNIRETAIL_PGDATA:-$HOME/.omniretail/pgdata}"
PGPORT="${OMNIRETAIL_PGPORT:-5433}"
PGSUPERUSER="${OMNIRETAIL_PGSUPERUSER:-postgres}"
LOGFILE="$PGDATA_DIR/server.log"

# Find the PostgreSQL binaries. We prefer PATH, then fall back to the
# standard Windows install location.
find_pg_bin() {
  if command -v pg_ctl >/dev/null 2>&1; then
    dirname "$(command -v pg_ctl)"
    return
  fi
  for candidate in "/c/Program Files/PostgreSQL/18/bin" \
                   "/c/Program Files/PostgreSQL"/*/bin \
                   "/usr/lib/postgresql"/*/bin; do
    if [ -x "$candidate/pg_ctl" ] || [ -x "$candidate/pg_ctl.exe" ]; then
      echo "$candidate"
      return
    fi
  done
  return 1
}

PG_BIN="$(find_pg_bin)" || {
  echo "ERROR: could not find pg_ctl. Is PostgreSQL installed?" >&2
  exit 1
}
export PATH="$PG_BIN:$PATH"

log() { printf '  %s\n' "$*"; }

cmd_init() {
  if [ -f "$PGDATA_DIR/PG_VERSION" ]; then
    log "cluster already exists at $PGDATA_DIR (version $(cat "$PGDATA_DIR/PG_VERSION"))"
    return 0
  fi
  mkdir -p "$PGDATA_DIR"
  log "creating cluster at $PGDATA_DIR"
  # -A trust: local-only development cluster bound to 127.0.0.1, so no
  # password is needed. This is safe because nothing outside this machine
  # can reach it; it is NOT a pattern to copy for a deployed server.
  initdb -D "$PGDATA_DIR" -U "$PGSUPERUSER" -A trust -E UTF8 --locale=C >/dev/null
  write_hba
  log "cluster created (superuser: $PGSUPERUSER, port: $PGPORT)"
}

# Two-tier local authentication:
#   * the superuser keeps 'trust' so administration and bootstrap work
#     without a password on a loopback-only development cluster;
#   * every OTHER role must present a real password (scram-sha-256).
#
# This matters because the application roles are exactly the ones whose
# separation we care about. If 127.0.0.1 were 'trust' for everyone, then
# the read-only role's password would be decorative, and a mistake in the
# AI layer could not be caught by authentication - only by privileges.
write_hba() {
  cat > "$PGDATA_DIR/pg_hba.conf" <<EOF
# OmniRetail local development cluster - loopback only.
# Managed by backend/scripts/db/pg_ctl.sh ; edit that script, not this file.
# TYPE  DATABASE  USER            ADDRESS       METHOD
trust  ADMIN     $PGSUPERUSER    -             -  # placeholder replaced below
EOF
  # Written as a heredoc without the bogus line so the file stays valid.
  cat > "$PGDATA_DIR/pg_hba.conf" <<EOF
# OmniRetail local development cluster - loopback only.
# Managed by backend/scripts/db/pg_ctl.sh ; edit that script, not this file.
#
# TYPE  DATABASE        USER                  ADDRESS         METHOD
local   all             $PGSUPERUSER                          trust
host    all             $PGSUPERUSER          127.0.0.1/32    trust
host    all             $PGSUPERUSER          ::1/128         trust
local   all             all                                   scram-sha-256
host    all             all                   127.0.0.1/32    scram-sha-256
host    all             all                   ::1/128         scram-sha-256
local   replication     all                                   trust
host    replication     all                   127.0.0.1/32    trust
host    replication     all                   ::1/128         trust
EOF
  log "wrote pg_hba.conf (superuser=trust, app roles=scram-sha-256)"
}

cmd_hba() {
  write_hba
  pg_ctl -D "$PGDATA_DIR" reload >/dev/null 2>&1 || true
  log "reloaded authentication configuration"
}

cmd_start() {
  if pg_isready -h 127.0.0.1 -p "$PGPORT" -q 2>/dev/null; then
    log "already running on 127.0.0.1:$PGPORT"
    return 0
  fi

  # Refuse to start a SECOND postmaster on a data directory already in use;
  # that is how you corrupt a cluster.
  if [ -f "$PGDATA_DIR/postmaster.pid" ] && kill -0 \
       "$(head -1 "$PGDATA_DIR/postmaster.pid")" 2>/dev/null; then
    echo "ERROR: a postmaster is already running for $PGDATA_DIR" >&2
    return 1
  fi

  # Log OUTSIDE the cluster dir: on Windows the postmaster can fail to
  # reopen its own log file inside the data directory.
  LOGFILE="${TMPDIR:-/tmp}/omniretail-pg-$PGPORT.log"
  : > "$LOGFILE"

  # Any stale pid file from an unclean shutdown would block startup.
  rm -f "$PGDATA_DIR/postmaster.pid"

  log "launching postgres.exe directly on 127.0.0.1:$PGPORT"
  log "log: $LOGFILE"

  # Listen on BOTH loopback families. On Windows, "localhost" resolves to
  # ::1 first, so a server bound only to 127.0.0.1 makes every client burn
  # the full connect timeout waiting on IPv6 before falling back to IPv4.
  # That looked exactly like a hang. Binding both removes the whole class of
  # problem.
  nohup postgres -D "$PGDATA_DIR" \
        -p "$PGPORT" \
        -c "listen_addresses=127.0.0.1,::1" \
        > "$LOGFILE" 2>&1 &

  # Wait for readiness by polling the server itself rather than trusting a
  # fixed sleep. pg_isready asks the postmaster for a reply, so it only
  # succeeds once the server genuinely accepts connections.
  for _ in $(seq 1 60); do
    if pg_isready -h 127.0.0.1 -p "$PGPORT" -q 2>/dev/null; then
      log "ready"
      return 0
    fi
    sleep 0.5
  done

  echo "ERROR: server did not become ready within 30s. Last log lines:" >&2
  tail -20 "$LOGFILE" >&2 || true
  return 1
}

cmd_stop() {
  # Shutdown only signals the running postmaster, so pg_ctl is safe here.
  # Fall back to the postmaster pid if pg_ctl refuses (e.g. a stale pid file).
  if ! pg_ctl -D "$PGDATA_DIR" -m fast -w -t 60 stop 2>/dev/null; then
    if [ -f "$PGDATA_DIR/postmaster.pid" ]; then
      local pid
      pid="$(head -1 "$PGDATA_DIR/postmaster.pid")"
      log "pg_ctl could not stop; killing pid $pid"
      kill "$pid" 2>/dev/null || true
      sleep 2
    fi
  fi
}

cmd_status() {
  if pg_isready -h 127.0.0.1 -p "$PGPORT" 2>/dev/null; then
    echo "  cluster is accepting connections on 127.0.0.1:$PGPORT"
    echo "  data directory: $PGDATA_DIR"
  else
    echo "  cluster is NOT running (data directory: $PGDATA_DIR)"
    return 1
  fi
}

cmd_psql() {
  exec psql -h 127.0.0.1 -p "$PGPORT" -U "$PGSUPERUSER" -d postgres
}

case "${1:-}" in
  init)   cmd_init ;;
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  status) cmd_status ;;
  hba)    cmd_hba ;;
  psql)   cmd_psql ;;
  *)
    echo "usage: $0 {init|start|stop|status|hba|psql}" >&2
    exit 2
    ;;
esac