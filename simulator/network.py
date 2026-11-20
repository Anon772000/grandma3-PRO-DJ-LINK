"""Unicast test packets and a bounded, read-only track metadata server."""
import ipaddress
import socket
import socketserver
import struct
import threading
import time

from . import protocol as wire


class BoundedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, address, handler, service):
        self.service = service
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, handler, bind_and_activate=False)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_bind()
            self.server_activate()
        except Exception:
            self.server_close()
            raise

    def verify_request(self, request, client_address):
        if self.slots.acquire(blocking=False):
            return True
        return False

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        self.service.errors += 1


class PortQuery(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        try:
            if wire.receive_exact(self.request, 19) == b'\0\0\0\x0fRemoteDBServer\0':
                self.request.sendall(struct.pack('>H', self.server.service.database.server_address[1]))
        except (OSError, EOFError, ValueError):
            pass


class Database(socketserver.BaseRequestHandler):
    def handle(self):
        service = self.server.service
        self.request.settimeout(3)
        current = None
        try:
            if wire.receive_exact(self.request, 5) != wire.number(1):
                return
            self.request.sendall(wire.number(1))
            ready = False
            for _ in range(64):
                tx, kind, args = wire.receive_message(self.request)
                if kind == 0 and len(args) == 1 and isinstance(args[0], int) and 1 <= args[0] <= 7:
                    ready = True
                    reply = wire.message(tx, 0x4000, [wire.number(0), wire.number(service.player_id)])
                elif not ready:
                    return
                elif kind == 0x100:
                    return
                elif kind in (0x2002, 0x2202) and len(args) == 2:
                    current = service.player.metadata(args[1])
                    current_id = args[1]
                    reply = wire.message(tx, 0x4000, [wire.number(kind), wire.number(3)]) if current else wire.message(tx, 0x4003, [wire.number(kind)])
                elif kind == 0x3000 and len(args) == 6 and current and service.player.metadata(current_id) is current:
                    if args[1] != 0 or args[2] < 3:
                        reply = wire.message(tx, 0x4003, [wire.number(kind)])
                    else:
                        reply = (wire.message(tx, 0x4001) +
                                 wire.menu_item(tx, 4, current.title, current_id) +
                                 wire.menu_item(tx, 7, current.artist) +
                                 wire.menu_item(tx, 11, value=max(1, int(current.duration))) + wire.message(tx, 0x4201))
                elif kind in (0x2004, 0x2904) and len(args) >= 2:
                    track_id = args[2] if kind == 0x2004 and len(args) >= 4 else args[1]
                    track = service.player.metadata(track_id)
                    data = (track.preview if kind == 0x2004 else track.detail) if track else b''
                    reply = wire.message(tx, 0x4402 if kind == 0x2004 else 0x4a02,
                                         [wire.number(kind), wire.number(0), wire.number(len(data)), wire.blob(data)])
                else:
                    reply = wire.message(tx, 0x4003, [wire.number(kind)])
                self.request.sendall(reply)
                service.queries += 1
        except (OSError, EOFError, ValueError, TypeError, OverflowError):
            pass


class Network:
    def __init__(self, player):
        self.player = player
        self.player_id = 1
        self.stop_event = threading.Event()
        self.udp = self.query = self.database = None
        self.threads = []
        self.queries = self.packets = self.errors = 0
        self.error = ''
        self.bind_ip = self.receiver_ip = ''

    @property
    def running(self):
        return self.udp is not None and not self.stop_event.is_set()

    def start(self, bind_ip='127.0.0.2', receiver_ip='127.0.0.1', player_id=1, query_port=12523):
        local = ipaddress.IPv4Address(bind_ip)
        receiver = ipaddress.IPv4Address(receiver_ip)
        if local.is_unspecified or local.is_multicast or receiver.is_unspecified or receiver.is_multicast:
            raise ValueError('Choose ordinary local and receiver IPv4 addresses.')
        if not 1 <= player_id <= 6:
            raise ValueError('Player number must be 1–6.')
        if local == receiver:
            raise ValueError('The player and receiver need different addresses. For onPC use 127.0.0.2 → 127.0.0.1.')
        self.stop()
        self.player_id = player_id
        self.bind_ip, self.receiver_ip = str(local), str(receiver)
        self.stop_event.clear()
        self.error = ''
        self.queries = self.packets = self.errors = 0
        try:
            self.database = BoundedServer((self.bind_ip, 0), Database, self)
            self.query = BoundedServer((self.bind_ip, query_port), PortQuery, self)
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.bind((self.bind_ip, 0))
            for server in (self.database, self.query):
                thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
                thread.start()
                server.started = True
                self.threads.append(thread)
            thread = threading.Thread(target=self._transmit, daemon=True)
            thread.start()
            self.threads.append(thread)
        except Exception:
            self.stop()
            raise

    def stop(self):
        self.stop_event.set()
        for server in (self.query, self.database):
            if server is not None:
                if getattr(server, 'started', False):
                    server.shutdown()
                server.server_close()
        if self.udp is not None:
            self.udp.close()
        for thread in self.threads:
            if thread is not threading.current_thread():
                thread.join(timeout=.3)
        self.threads = []
        self.query = self.database = self.udp = None

    def _transmit(self):
        next_presence = next_status = next_position = 0
        last_beat = last_identity = None
        counter = 0
        while not self.stop_event.is_set():
            now = time.monotonic()
            state = self.player.snapshot()
            try:
                for port, packet, due in (
                    (50000, wire.presence(self.player_id, self.bind_ip, bytes([2, 77, 65, 51, 0, self.player_id])), now >= next_presence),
                    (50002, wire.status(self.player_id, state, counter), now >= next_status),
                    (50001, wire.position(self.player_id, state), state['loaded'] and now >= next_position)):
                    if due:
                        self.udp.sendto(packet, (self.receiver_ip, port)); self.packets += 1
                if now >= next_presence:
                    next_presence = now + 1
                if now >= next_status:
                    counter = (counter + 1) % 2**32
                    next_status = now + .2
                if now >= next_position:
                    next_position = now + .03
                identity = (state['track_id'], state['revision'])
                if identity != last_identity:
                    last_beat = None
                    last_identity = identity
                if state['playing'] and state['beat_index'] >= 0 and state['beat_index'] != last_beat:
                    distance = (state['position'] - state['origin']) * state['bpm'] / 60 - state['beat_index']
                    # Never replay a beat already well behind the audible playhead.
                    if distance < .15:
                        self.udp.sendto(wire.beat(self.player_id, state), (self.receiver_ip, 50001)); self.packets += 1
                    last_beat = state['beat_index']
            except OSError as exc:
                if not self.stop_event.is_set():
                    self.error = str(exc)
                    self.errors += 1
            self.stop_event.wait(.004)
