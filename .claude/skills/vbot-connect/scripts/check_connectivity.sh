#!/usr/bin/env bash
# Read-only VBOT EDU network and SSH connectivity diagnostic.

set -u

MODE="auto"
EXPLICIT_HOST=""
SSH_USER="vbot"
CONNECT_TIMEOUT=4

usage() {
  cat <<'EOF'
Usage: check_connectivity.sh [--mode auto|wired|wifi] [--host IP] [--user USER]

Read-only checks for VBOT EDU network and SSH connectivity.
  auto    test 192.168.126.2 and 192.168.127.2 (default)
  wired   test 192.168.126.2
  wifi    test 192.168.127.2
EOF
}

while (($#)); do
  case "$1" in
    --mode)
      [[ $# -ge 2 ]] || { echo "ERROR: --mode requires a value" >&2; exit 2; }
      MODE="$2"; shift 2 ;;
    --host)
      [[ $# -ge 2 ]] || { echo "ERROR: --host requires a value" >&2; exit 2; }
      EXPLICIT_HOST="$2"; shift 2 ;;
    --user)
      [[ $# -ge 2 ]] || { echo "ERROR: --user requires a value" >&2; exit 2; }
      SSH_USER="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$MODE" in
  auto|wired|wifi) ;;
  *) echo "ERROR: mode must be auto, wired, or wifi" >&2; exit 2 ;;
esac

if [[ -n "$EXPLICIT_HOST" ]]; then
  HOSTS=("$EXPLICIT_HOST")
elif [[ "$MODE" == "wired" ]]; then
  HOSTS=("192.168.126.2")
elif [[ "$MODE" == "wifi" ]]; then
  HOSTS=("192.168.127.2")
else
  HOSTS=("192.168.126.2" "192.168.127.2")
fi

TMP_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t vbot-connect)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

section() { printf '\n== %s ==\n' "$1"; }
ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
has_cmd() { command -v "$1" >/dev/null 2>&1; }

list_ipv4() {
  if has_cmd ip; then
    ip -o -4 address show up 2>/dev/null | awk '{print $2 " " $4}'
  elif has_cmd ifconfig; then
    ifconfig 2>/dev/null | awk '
      /^[A-Za-z0-9]/ {gsub(":", "", $1); iface=$1}
      /inet / && $2 != "127.0.0.1" {print iface " " $2}'
  else
    return 1
  fi
}

route_to() {
  local host="$1"
  if has_cmd ip; then
    ip -4 route get "$host" 2>&1 | head -n 1
  elif has_cmd route; then
    route -n get "$host" 2>&1 | awk '/interface:|gateway:/{printf "%s%s", sep, $0; sep="; "} END{print ""}'
  else
    return 1
  fi
}

ping_host() {
  local host="$1"
  has_cmd ping || return 2
  if [[ "$(uname -s)" == "Darwin" ]]; then
    ping -c 1 -W 2000 "$host" >/dev/null 2>&1
  else
    ping -c 1 -W 2 "$host" >/dev/null 2>&1
  fi
}

tcp22_open() {
  local host="$1"
  if has_cmd nc; then
    nc -z -w "$CONNECT_TIMEOUT" "$host" 22 >/dev/null 2>&1
  elif has_cmd timeout; then
    timeout "$CONNECT_TIMEOUT" bash -c 'exec 3<>/dev/tcp/$1/22' bash "$host" >/dev/null 2>&1
  else
    return 2
  fi
}

host_key_state() {
  local host="$1"
  if ! has_cmd ssh-keygen; then
    echo "ssh-keygen-unavailable"
  elif ssh-keygen -F "$host" >/dev/null 2>&1; then
    echo "known"
  else
    echo "unknown"
  fi
}

ssh_probe() {
  local host="$1"
  local out="$TMP_DIR/ssh-${host//[^A-Za-z0-9]/_}.log"
  if ! has_cmd ssh; then
    echo "ssh-unavailable"
    return
  fi

  ssh -o BatchMode=yes -o ConnectTimeout="$CONNECT_TIMEOUT" \
    -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes \
    "$SSH_USER@$host" \
    'printf "VBOT_REMOTE_OK\n"; hostname; uname -sr; grep "^PRETTY_NAME=" /etc/os-release 2>/dev/null || true' \
    >"$out" 2>&1
  local status=$?

  if [[ $status -eq 0 ]] && grep -q '^VBOT_REMOTE_OK$' "$out"; then
    echo "success"
    sed -n '2,4p' "$out" >"$out.remote"
  elif grep -q 'REMOTE HOST IDENTIFICATION HAS CHANGED' "$out"; then
    echo "host-key-changed"
  elif grep -qiE 'No ED25519 host key is known|Host key verification failed|authenticity of host' "$out"; then
    echo "host-key-unknown"
  elif grep -qi 'Permission denied' "$out"; then
    echo "auth-failed"
  elif grep -qiE 'Connection timed out|No route to host|Network is unreachable' "$out"; then
    echo "network-failed"
  elif grep -qi 'Connection refused' "$out"; then
    echo "refused"
  else
    echo "failed"
  fi
}

section "Local machine"
printf 'OS: %s\n' "$(uname -srm)"
LOCAL_IPV4="$(list_ipv4 2>/dev/null || true)"
if [[ -n "$LOCAL_IPV4" ]]; then
  printf 'Active IPv4 addresses:\n%s\n' "$LOCAL_IPV4"
else
  warn "Could not enumerate active IPv4 addresses"
fi

if grep -Eq '(^|[[:space:]])192\.168\.126\.2(/|$)' <<<"$LOCAL_IPV4"; then
  fail "Local computer uses 192.168.126.2, conflicting with the VBOT wired address"
fi
if grep -Eq '(^|[[:space:]])192\.168\.127\.(2|10)(/|$)' <<<"$LOCAL_IPV4"; then
  fail "Local computer conflicts with a VBOT WiFi-side device address (.2 or .10)"
fi

ANY_REACHABLE=0
ANY_SSH=0

for host in "${HOSTS[@]}"; do
  section "Target $host"
  route="$(route_to "$host" 2>/dev/null || true)"
  [[ -n "$route" ]] && info "Route: $route" || warn "No route information available"

  if ping_host "$host"; then
    ok "Ping succeeded"
    ANY_REACHABLE=1
  else
    ping_status=$?
    [[ $ping_status -eq 2 ]] && warn "ping command unavailable" || warn "Ping failed or ICMP is filtered"
  fi

  if tcp22_open "$host"; then
    ok "TCP/22 is open"
    ANY_REACHABLE=1
  else
    tcp_status=$?
    if [[ $tcp_status -eq 2 ]]; then
      warn "Cannot test TCP/22: install nc/netcat or timeout"
    else
      fail "TCP/22 is closed or timed out"
      continue
    fi
  fi

  key_state="$(host_key_state "$host")"
  case "$key_state" in
    known) ok "Host Key exists in known_hosts" ;;
    unknown) warn "Host Key is not trusted yet; verify its fingerprint before accepting" ;;
    *) warn "Cannot inspect known_hosts because ssh-keygen is unavailable" ;;
  esac

  ssh_state="$(ssh_probe "$host")"
  case "$ssh_state" in
    success)
      ok "SSH key authentication and remote read-only self-check succeeded"
      sed 's/^/       /' "$TMP_DIR/ssh-${host//[^A-Za-z0-9]/_}.log.remote"
      ANY_SSH=1 ;;
    host-key-changed)
      fail "SSH Host Key changed; verify the physical device before removing the old key" ;;
    host-key-unknown)
      warn "SSH Host Key is unknown; inspect with: ssh-keyscan -T 5 -t ed25519 $host 2>/dev/null | ssh-keygen -lf -" ;;
    auth-failed)
      warn "Network and SSH service work, but non-interactive key authentication failed"
      info "After verifying the Host Key, connect once with: ssh $SSH_USER@$host" ;;
    refused) fail "Target is reachable but SSH service refused the connection" ;;
    network-failed) fail "SSH network connection failed" ;;
    ssh-unavailable) warn "ssh command unavailable" ;;
    *) warn "SSH probe failed; run ssh -v $SSH_USER@$host for details" ;;
  esac
done

section "Result"
if [[ $ANY_SSH -eq 1 ]]; then
  ok "VBOT network, SSH trust, key authentication, and remote self-check are ready"
  exit 0
elif [[ $ANY_REACHABLE -eq 1 ]]; then
  warn "VBOT network is reachable, but SSH setup is incomplete"
  exit 1
else
  fail "No VBOT candidate is reachable; check power, cable/AP, local IP, and route"
  exit 2
fi
