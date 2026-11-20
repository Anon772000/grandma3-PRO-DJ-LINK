from pathlib import Path
import socket
import struct
import threading
import time
import unittest

from lupa.lua54 import LuaRuntime, LuaError
import test_network_interfaces as network_tests

ROOT = Path(__file__).resolve().parents[1]


def number(value, size=4):
    return bytes([{1: 15, 2: 16, 4: 17}[size]]) + value.to_bytes(size, 'big')


def blob(value):
    return b'\x14' + len(value).to_bytes(4, 'big') + value if value else b''


def text(value):
    raw = value.encode('utf-16-be') + b'\0\0'
    return b'\x26' + (len(raw) // 2).to_bytes(4, 'big') + raw


def message(tx, kind, arguments):
    tags = bytes(3 if item[:1] == b'\x14' or not item else
                 2 if item[:1] == b'\x26' else 6 for item in arguments)
    return (number(0x872349ae) + number(tx) + number(kind, 2) +
            number(len(arguments), 1) + blob(tags.ljust(12, b'\0')) + b''.join(arguments))


def menu_item(tx, kind, label='', value=0):
    return message(tx, 0x4101, [number(0), number(value), number(0), text(label),
                               number(0), text(''), number(kind), number(0), number(0), number(0)])


class TrackTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(encoding='latin-1', unpack_returned_tuples=True)
        self.tracks = self.lua.execute((ROOT / 'src/cdj-link-tracks.lua').read_text(encoding='utf-8'))
        self.lua.globals().Tracks = self.tracks
        self.lua.execute('''
            opts={enabled=true,observer=true,waveform=true,test=false,socket={tcp=function()
                local sock={}
                function sock:settimeout() end
                function sock:close() self.closed=true end
                function sock:connect() return nil,'timeout' end
                return sock
            end}}
            engine={players={},connected=function(self,p,t) return p and t-(p.lastSeen or 0)<5 end}
            player={id=1,ip='192.168.50.21',source=1,slot=3,trackType=1,trackId=101,
                    name='CDJ-3000',mode=3,lastSeen=0}
            engine.players[1]=player
            service=Tracks.new(opts); service:observe(player,0)
        ''')
        self.service = self.lua.globals().service
        self.engine = self.lua.globals().engine

    def decode(self, data):
        return self.tracks.readMessage(data.decode('latin-1'))

    def test_independent_wire_encoding_and_fragmented_reply(self):
        encoded = message(12, 0x4000, [number(0x2002), number(3)])
        lua_encoded = self.lua.eval('Tracks.message(12,0x4000,{Tracks.number(0x2002),Tracks.number(3)})')
        self.assertEqual(lua_encoded.encode('latin-1'), encoded)
        for end in range(len(encoded)):
            self.assertIsNone(self.decode(encoded[:end]), end)
        parsed, used = self.decode(encoded + b'extra')
        self.assertEqual((parsed.tx, parsed.kind, parsed.args[2], used), (12, 0x4000, 3, len(encoded)))

    def test_unicode_and_control_characters(self):
        value = 'Beyonc\u00e9 \U0001f3b5\nMix'
        parsed, _ = self.decode(message(1, 2, [text(value)]))
        self.assertEqual(parsed.args[1].encode('latin-1').decode('utf-8'), value.replace('\n', ' '))
        encoded = self.lua.eval("Tracks.text('Beyonc'..utf8.char(233)..' '..utf8.char(0x1f3b5)..' Mix').wire")
        self.assertEqual(encoded.encode('latin-1'), text(value.replace('\n', ' ')))

    def test_missing_zero_length_blob_does_not_consume_next_frame(self):
        first = message(4, 0x4402, [number(0x2004), number(0), number(0), b''])
        parsed, used = self.decode(first + message(5, 0x4000, []))
        self.assertEqual(parsed.args[4], '')
        self.assertEqual(used, len(first))

    def test_rejects_oversized_malformed_and_wrong_types(self):
        cases = [number(0) + message(1, 2, [])[5:],
                 message(1, 2, [])[:14] + number(13, 1) + blob(bytes(12)),
                 b'\xff' + bytes(40),
                 message(1, 2, [blob(b'x')])[:-6] + b'\x14' + struct.pack('>I', 2**30)]
        for data in cases:
            with self.assertRaises(LuaError):
                self.decode(data)

    def test_precise_position_offsets_and_truncation(self):
        data = self.tracks.testPosition(1, 90345, 180)
        self.assertEqual(len(data), 60)
        self.assertEqual(struct.unpack_from('>II', data.encode('latin-1'), 0x24), (180, 90345))
        parsed = self.tracks.position(data)
        self.assertEqual((parsed.id, parsed.position, parsed.duration, parsed.test), (1, 90345, 180, True))
        for end in range(len(data)):
            self.assertIsNone(self.tracks.position(data[:end]))

    def test_position_source_test_isolation_staleness_and_jumps(self):
        value = self.lua.eval('{id=1,position=10000,duration=180,test=false}')
        self.assertFalse(self.service.position(self.service, value, '192.168.50.99', 0, self.engine))
        value.test = True
        self.assertFalse(self.service.position(self.service, value, '192.168.50.21', 0, self.engine))
        value.test = False
        self.assertTrue(self.service.position(self.service, value, '192.168.50.21', 0, self.engine))
        self.assertEqual(self.service.view(self.service, 1, 0).remaining, 170000)
        value.position = 5000
        self.service.position(self.service, value, '192.168.50.21', .1, self.engine)
        self.assertEqual(self.service.view(self.service, 1, .1).position, 5000)
        self.assertIsNone(self.service.view(self.service, 1, 2).position)

    def test_track_change_and_unload_clear_titles_waveforms_and_position(self):
        self.lua.execute("service.players[1].title='old'; service.players[1].preview='old'; service.players[1].position=10; player.trackId=102; service:observe(player,0.1)")
        self.assertIsNone(self.service.players[1].title)
        self.assertIsNone(self.service.players[1].preview)
        self.assertIsNone(self.service.players[1].position)
        self.lua.execute("service.players[1].title='new'; player.mode=0; service:observe(player,0.2)")
        self.assertEqual(self.service.players[1].status, 'No track')
        self.assertIsNone(self.service.players[1].title)

    def test_waveform_peak_bins_and_outside_track_are_bounded(self):
        raw = bytes([0, 31, 2, 12, 3, 0]).decode('latin-1')
        bins = self.tracks.bins(raw, 3, 0, 1, True)
        self.assertEqual(list(bins.values()), [31, 12, 3])
        self.assertEqual(list(self.tracks.bins(raw, 3, 2, 3, True).values()), [0, 0, 0])
        self.assertEqual(list(self.tracks.bins('\x01\x05\x1f\x05', 2).values()), [1, 31])
        self.assertEqual(self.tracks.clock(90345), '1:30')
        self.assertEqual(self.tracks.clock(None), '--:--')

    def test_disabled_missing_observer_and_older_decks_make_no_queries(self):
        for config in ('opts.enabled=false', 'opts.observer=false', "player.name='CDJ-2000NXS2'"):
            self.lua.execute(config + '; service=Tracks.new(opts); service:observe(player,0); service:poll(0,engine)')
            self.assertIsNone(self.lua.globals().service.job)
            self.lua.execute("opts.enabled=true; opts.observer=true; player.name='CDJ-3000'")

    def test_four_decks_use_target_context_without_changing_observer(self):
        self.lua.execute('''
            for i=2,4 do engine.players[i]={id=i,ip='192.168.50.'..(20+i),source=1,slot=3,
                trackType=1,trackId=101,name='CDJ-3000',mode=3,lastSeen=0} end
            service:poll(0,engine)
        ''')
        self.assertEqual(self.service.job.asking, 1)
        self.assertEqual(self.service.job.target, 1)

    def test_cancel_and_timeout_close_socket_and_back_off(self):
        self.service.poll(self.service, 0, self.engine)
        job = self.service.job
        self.service.poll(self.service, 4, self.engine)
        self.assertTrue(job.socket.closed)
        self.assertIsNone(self.service.job)
        self.service.poll(self.service, 4.1, self.engine)
        self.assertIsNone(self.service.job)
        self.assertIn('timed out', self.service.players[1].status)

    def test_source_ip_change_closes_inflight_connection(self):
        self.service.poll(self.service, 0, self.engine)
        job = self.service.job
        self.lua.globals().player.ip = '192.168.50.22'
        self.service.poll(self.service, .1, self.engine)
        self.assertTrue(job.socket.closed)
        self.assertIsNone(self.service.job)

    def test_reconnected_player_cannot_reuse_stale_metadata(self):
        self.lua.execute('''
            service.players[1].title='stale title'
            local reconnected={}; for k,v in pairs(player) do reconnected[k]=v end
            engine.players[1]=reconnected; service:observe(reconnected,1)
        ''')
        self.assertIsNone(self.service.players[1].title)

    def test_wrong_transaction_is_rejected(self):
        self.service.poll(self.service, 0, self.engine)
        self.service.job.stage = 'setup'
        parsed, _ = self.decode(message(3, 0x4000, [number(0), number(1)]))
        with self.assertRaisesRegex(LuaError, 'transaction'):
            self.service.response(self.service, parsed, .1)


class TcpSocket:
    """Expose real nonblocking sockets with LuaSocket's return conventions."""
    def __init__(self):
        self.socket = socket.socket()
        self.socket.setblocking(False)
        self.started = False

    def settimeout(self, _):
        return 1

    def bind(self, ip, port):
        self.socket.bind((ip, port))
        return 1

    def connect(self, ip, port):
        if not self.started:
            self.socket.connect_ex((ip, port))
            self.started = True
        try:
            self.socket.getpeername()
            return 1
        except OSError:
            return None, 'timeout'

    def send(self, data, index):
        try:
            count = self.socket.send(data.encode('latin-1')[index - 1:])
            return index - 1 + count
        except BlockingIOError:
            return None, 'timeout', index - 1

    def receive(self, count):
        try:
            data = self.socket.recv(count)
            if not data:
                return None, 'closed', ''
            return None, 'timeout', data.decode('latin-1')
        except BlockingIOError:
            return None, 'timeout', ''

    def close(self):
        self.socket.close()


def recv_exact(sock, count):
    result = b''
    while len(result) < count:
        part = sock.recv(count - len(result))
        if not part:
            raise EOFError('Client closed')
        result += part
    return result


def recv_message(sock):
    head = recv_exact(sock, 32)
    if head[:5] != number(0x872349ae):
        raise AssertionError('Incorrect message magic')
    tx = int.from_bytes(head[6:10], 'big')
    kind = int.from_bytes(head[11:13], 'big')
    count = head[14]
    arguments = []
    for tag in head[20:20 + count]:
        if tag == 3 and arguments[-1] == 0:
            arguments.append(b'')
            continue
        field_tag = recv_exact(sock, 1)[0]
        if field_tag in (15, 16, 17):
            arguments.append(int.from_bytes(recv_exact(sock, {15: 1, 16: 2, 17: 4}[field_tag]), 'big'))
        else:
            length = int.from_bytes(recv_exact(sock, 4), 'big')
            arguments.append(recv_exact(sock, length * (2 if field_tag == 38 else 1)))
    return tx, kind, arguments


class TrackTcpIntegrationTests(unittest.TestCase):
    setUp = TrackTests.setUp
    def test_real_tcp_metadata_preview_and_detail_fragmented(self):
        query = socket.socket(); query.bind(('127.0.0.1', 0)); query.listen()
        database = socket.socket(); database.bind(('127.0.0.1', 0)); database.listen()
        failures = []
        requests = []
        preview = bytes([i % 32 for i in range(800)])
        detail = bytes([i % 32 for i in range(27000)])

        def serve():
            try:
                query.settimeout(5); database.settimeout(5)
                with query.accept()[0] as conn:
                    conn.settimeout(5)
                    self.assertEqual(recv_exact(conn, 19), b'\0\0\0\x0fRemoteDBServer\0')
                    conn.sendall(struct.pack('>H', database.getsockname()[1]))
                with database.accept()[0] as conn:
                    conn.settimeout(5)
                    self.assertEqual(recv_exact(conn, 5), number(1))
                    conn.sendall(number(1))
                    while True:
                        tx, kind, args = recv_message(conn); requests.append((kind, args))
                        if kind == 0:
                            reply = message(tx, 0x4000, [number(0), number(1)])
                        elif kind == 0x2002:
                            self.assertEqual(args, [0x01010301, 101])
                            reply = message(tx, 0x4000, [number(kind), number(3)])
                        elif kind == 0x3000:
                            reply = (message(tx, 0x4001, []) + menu_item(tx, 4, 'Integration track') +
                                     menu_item(tx, 7, 'Test artist') + menu_item(tx, 11, value=180) + message(tx, 0x4201, []))
                        elif kind in (0x2004, 0x2904):
                            waveform = preview if kind == 0x2004 else detail
                            if kind == 0x2004:
                                self.assertEqual(args, [0x01080301, 1, 101, 0])
                            reply = message(tx, 0x4402 if kind == 0x2004 else 0x4a02,
                                            [number(kind), number(0), number(len(waveform)), blob(waveform)])
                        else:
                            raise AssertionError(hex(kind))
                        # Deliberately split headers and fields, then chunk the large waveform.
                        for chunk in [reply[:1], reply[1:9], reply[9:31], reply[31:]]:
                            conn.sendall(chunk)
                        if kind == 0x2904:
                            break
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=serve, daemon=True); thread.start()
        self.lua.globals().make_tcp = TcpSocket
        self.lua.globals().query_port = query.getsockname()[1]
        self.lua.execute('''
            opts.queryPort=query_port; opts.localIP='127.0.0.1'; player.ip='127.0.0.1'
            opts.socket.tcp=function()
                local py=make_tcp(); local sock={}
                function sock:settimeout(n) return py.settimeout(n) end
                function sock:bind(ip,port) return py.bind(ip,port) end
                function sock:connect(ip,port) return py.connect(ip,port) end
                function sock:send(data,index) return py.send(data,index) end
                function sock:receive(n) return py.receive(n) end
                function sock:close() return py.close() end
                return sock
            end
            service=Tracks.new(opts); service:observe(player,0)
        ''')
        service = self.lua.globals().service
        try:
            started = time.monotonic()
            while time.monotonic() - started < 5:
                now = time.monotonic() - started
                self.lua.globals().player.lastSeen = now
                service.poll(service, now, self.engine)
                if service.players[1].done:
                    break
                time.sleep(.001)
            self.assertFalse(failures, failures)
            self.assertTrue(service.players[1].done, service.players[1].status)
            self.assertEqual(service.players[1].title, 'Integration track')
            self.assertEqual(service.players[1].artist, 'Test artist')
            self.assertEqual(service.players[1].metadataDuration, 180)
            self.assertEqual(service.players[1].preview.encode('latin-1'), preview)
            self.assertEqual(service.players[1].detail.encode('latin-1'), detail)
            self.assertEqual([r[0] for r in requests], [0, 0x2002, 0x3000, 0x2004, 0x2904])
        finally:
            service.stop(service); query.close(); database.close(); thread.join(timeout=6)


class TrackHostTests(unittest.TestCase):
    def setUp(self):
        network_tests.HostNetworkTests.setUp(self)

    def test_pc_player_connects_live_loopback_and_disarms_output(self):
        self.assertTrue(self.service.start('Test')[0])
        self.service.arm(True)
        self.service.connectPCPlayer()
        self.assertTrue(self.service.running)
        self.assertEqual(self.service.cfg.input, 'Live')
        self.assertEqual(self.service.cfg.bind, '127.0.0.1')
        self.assertFalse(self.service.cfg.observer)
        self.assertTrue(self.service.cfg.metadata)
        self.assertTrue(self.service.cfg.waveform)
        self.assertFalse(self.service.engine.armed)
        self.assertEqual(self.lua.globals().saved.bind, '127.0.0.1')

    def test_metadata_off_by_default_and_persisted_when_changed(self):
        self.assertFalse(self.service.cfg.metadata)
        self.service.cfg.metadata = True
        self.service.configure(self.service.cfg)
        self.assertEqual(self.lua.globals().saved.metadata, 1)

    def test_track_overlay_builds_with_two_waveforms_and_closes(self):
        self.lua.execute('''
            function uiNode(parent)
                local node={parent=parent,children={}}
                function node:Append(kind) local child=uiNode(self); child.kind=kind; self.children[#self.children+1]=child; return child end
                function node:Parent() return self.parent end
                function node:Index() return 1 end
                function node:Remove() self.removed=true end
                setmetatable(node,{__index=function(self,key) if type(key)=='number' then local child=uiNode(self); rawset(self,key,child); return child end end})
                return node
            end
            IsObjectValid=function(o) return o~=nil and not o.removed end
            overlay=uiNode(); GetFocusDisplay=function() return {ScreenOverlay=overlay} end
        ''')
        self.service.trackView()
        ui = self.service.trackUI
        self.assertEqual(len(ui.preview), 96)
        self.assertEqual(len(ui.wave), 96)
        self.assertIn('No connected player', ui.title.Text)

    def test_beat_and_position_queue_coalescing_keep_both(self):
        self.assertTrue(self.service.start('Test')[0])
        self.lua.execute('Time=function() return .2 end')
        # Send independent packets rather than relying on the mock sendto implementation.
        self.lua.globals().TestCore = self.lua.execute((ROOT / 'src/cdj-link-core.lua').read_text())
        self.lua.globals().TestTracks = self.lua.execute((ROOT / 'src/cdj-link-tracks.lua').read_text())
        self.lua.execute('''
            local s=CDJLINK
            s.sockets[50002].queue={{TestCore.status(1,128,1,true,true,1),'127.0.0.1'}}
            s.sockets[50001].queue={{TestCore.beat(1,128,1),'127.0.0.1'},
                {TestTracks.testPosition(1,5000,180),'127.0.0.1'}}
            timers[#timers]()
        ''')
        self.assertEqual(self.service.engine.players[1].beat, 1)
        self.assertEqual(self.service.engine.previews, 1)
        self.assertEqual(self.service.tracks.view(self.service.tracks, 1, .2).position, 5000)
        self.assertFalse(self.service.engine.armed)

    def test_failed_metadata_connection_does_not_stop_or_disarm_beats(self):
        self.lua.globals().TestCore = self.lua.execute((ROOT / 'src/cdj-link-core.lua').read_text())
        self.lua.execute('''
            local s=CDJLINK
            s.cfg.metadata=true; s.cfg.observer=true; s.cfg.observerIP='169.254.50.10'
            s.cfg.broadcast='169.254.255.255'; s.cfg.mac='02:aa:bb:cc:dd:ee'
            require('socket').tcp=function() error('simulated TCP failure') end
            assert(s.start('Live'))
            local p={id=1,ip='169.254.50.21',source=1,slot=3,trackType=1,trackId=101,
                     name='CDJ-3000',mode=3,playing=true,lastStatus=0,lastSeen=0}
            s.engine.players[1]=p; s.tracks:observe(p,0); s.arm(true)
            s.tracks:poll(0,s.engine)
            Time=function() return .1 end
            local raw=TestCore.beat(1,128,1)
            raw=raw:sub(1,11)..'CDJ-3000'..string.rep(string.char(0),12)..raw:sub(32)
            s.sockets[50001].queue={{raw,p.ip}}
            timers[#timers]()
        ''')
        self.assertTrue(self.service.running)
        self.assertTrue(self.service.engine.armed)
        self.assertEqual(self.service.engine.syncs, 1)
        self.assertIn('simulated TCP failure', self.service.tracks.players[1].status)


if __name__ == '__main__':
    unittest.main()
