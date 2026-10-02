#!/usr/bin/env bash
# Run a prosumer (pi1) and a consumer (pi2) as two processes on this machine and verify that they trade.
#
#   scripts/two_nodes.sh [start_date] [simulation_speed] [pi2_start_delay_seconds]
#   scripts/two_nodes.sh 2013-07-08 900 4    # defaults: a very sunny day, ~2s per half hour, pi2 started 4s late
#
# Extra flags for both nodes via NODE_ARGS, e.g. NODE_ARGS=--standalone to switch the start barrier off.
# pi2 is deliberately started late: the start barrier makes both nodes begin together anyway.
#
# Each node listens on its own loopback address (127.0.0.1 and 127.0.0.2) so both can use the same port (PORT, default 5050).
# On macOS only 127.0.0.1 exists by default; add the second address once with:
#   sudo ifconfig lo0 alias 127.0.0.2
# The default port is 5050 because macOS's AirPlay Receiver holds 5000. Change it with PORT=5051 scripts/two_nodes.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DATE="${1:-2013-07-08}"
SPEED="${2:-900}"
LATE="${3:-4}"
PORT="${PORT:-5050}"
PY="${PYTHON:-python3}"

# Check each address and the port separately, so the message says what is actually wrong.
"$PY" - "$PORT" <<'PYEOF' || exit 1
import socket, sys
port = int(sys.argv[1])
problems = []
for ip in ("127.0.0.1", "127.0.0.2"):
    s = socket.socket()
    try:
        s.bind((ip, port))
    except OSError as e:
        problems.append((ip, e))
    finally:
        s.close()
if problems:
    print(f"Cannot use port {port}:")
    for ip, e in problems:
        print(f"  {ip}:{port}  {e}")
    if all(ip == "127.0.0.2" for ip, _ in problems):
        print("Only 127.0.0.2 failed: that address does not exist yet. On macOS run once:")
        print("  sudo ifconfig lo0 alias 127.0.0.2")
    else:
        print(f"Something is already listening on port {port}. See what with:  lsof -nP -iTCP:{port} -sTCP:LISTEN")
        print("Use a different port with:  PORT=5051 scripts/two_nodes.sh")
        if port == 5000:
            print("(On macOS, port 5000 is used by the AirPlay Receiver service.)")
    sys.exit(1)
PYEOF

WORK="$(mktemp -d)"
mkdir -p "$WORK/cfg"
cat > "$WORK/cfg/network_topology.yml" <<YML
port: $PORT
devices:
  pi1: {name: pi1, ip_address: 127.0.0.1, is_prosumer: true,  hostname: prosumer-pi-1}
  pi2: {name: pi2, ip_address: 127.0.0.2, is_prosumer: false, hostname: consumer-pi-1}
YML
sed -E "s/start_date: .*/start_date: $DATE/; s/simulation_speed: [0-9]+/simulation_speed: $SPEED/" \
    config/simulation.yml > "$WORK/cfg/simulation.yml"

echo "Running pi1 (prosumer) and pi2 (consumer) for $DATE at ${SPEED}x; logs in $WORK"
"$PY" -u core/main.py --mock --device pi1 --bind 127.0.0.1 ${NODE_ARGS:-} --no-plot --config "$WORK/cfg" --plot-dir "$WORK/out" > "$WORK/pi1.log" 2>&1 &
P1=$!
sleep "$LATE"
"$PY" -u core/main.py --mock --device pi2 --bind 127.0.0.2 ${NODE_ARGS:-} --no-plot --config "$WORK/cfg" --plot-dir "$WORK/out" > "$WORK/pi2.log" 2>&1 &
P2=$!
trap 'kill $P1 $P2 2>/dev/null || true' INT TERM
wait $P1 $P2 || true

echo
"$PY" scripts/verify_two_nodes.py "$WORK"
