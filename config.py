import os

try:
    import netifaces  # type: ignore
except ImportError:  # netifaces is optional; fall back to a socket-based lookup
    netifaces = None

PI_1_IP = os.environ.get('SOLARVILLE_PI_1_IP', '10.126.46.162')  # IP of Pi 1
PI_2_IP = os.environ.get('SOLARVILLE_PI_2_IP', '10.126.50.50')  # IP of Pi 2


def _ip_from_netifaces():
    for interface in netifaces.interfaces():
        if interface == 'lo':
            continue
        addrs = netifaces.ifaddresses(interface)
        if netifaces.AF_INET in addrs:
            ip = addrs[netifaces.AF_INET][0]['addr']
            if ip != '127.0.0.1':
                return ip
    return None


def _ip_from_socket():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))  # no packets are actually sent
        return s.getsockname()[0]
    finally:
        s.close()


def get_network_ip():
    """Get the non-loopback IP address of the machine."""
    try:
        ip = _ip_from_netifaces() if netifaces else _ip_from_socket()
        if ip:
            return ip
    except Exception as e:
        print(f"Error getting network IP: {e}")
    return None


def get_local_and_peer_ip():
    """Return (local_ip, peer_ip).

    LOCAL_IP / PEER_IP environment variables take priority, which allows
    running on machines other than the two known Pis. Otherwise the machine's
    IP is matched against PI_1_IP / PI_2_IP. If it matches neither, the
    simulation runs standalone against localhost.
    """
    env_local = os.environ.get('LOCAL_IP')
    env_peer = os.environ.get('PEER_IP')
    if env_local and env_peer:
        return env_local, env_peer

    local_ip = get_network_ip()
    if local_ip == PI_1_IP:
        return PI_1_IP, PI_2_IP
    if local_ip == PI_2_IP:
        return PI_2_IP, PI_1_IP

    print(f"Local IP {local_ip} is not a known Pi; running standalone with no peer. "
          "Set LOCAL_IP and PEER_IP to override.")
    return '127.0.0.1', None


LOCAL_IP, PEER_IP = get_local_and_peer_ip()

print(f"Local IP: {LOCAL_IP}")
print(f"Peer IP: {PEER_IP}")

SOLAR_SCALE_FACTOR = 1000  # Adjust this value as needed
