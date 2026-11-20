from pathlib import Path
import socket
import struct
import tempfile
import time
import unittest

from lupa.lua54 import LuaRuntime
import numpy as np
import soundfile as sf

from simulator import protocol as wire
from simulator.network import Network
from simulator.player import Player, Track, load_track
from test_track_display import TcpSocket

ROOT = Path(__file__).resolve().parents[1]


def track(seconds=4, sample_rate=48000):
    sample = np.sin(np.arange(int(seconds * sample_rate)) * .1).astype(np.float32) * .4
    audio = np.column_stack((sample, sample))
    return Track('fixture.wav', 'Independent fixture', 'Test artist', audio, sample_rate, 120, False,
                 bytes([12, 5]) * 400, bytes([16]) * round(seconds * 150))


class StandalonePlayerTests(unittest.TestCase):
    def setUp(self):
        self.player = Player()
        self.player.load(track())

    def test_decode_real_wave_and_create_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'My track.wav'
            sf.write(path, track().samples, 48000)
            value = load_track(path)
            self.assertEqual(value.title, 'My track')
            self.assertEqual(len(value.preview), 800)
            self.assertEqual(len(value.detail), 600)
            self.assertEqual(value.samples.shape, (192000, 2))
            self.assertLessEqual(max(value.detail), 31)
            self.assertEqual(value.duration, 4)

    def test_audio_render_advances_playhead_and_pause_holds(self):
        self.player.playing = True
        output = self.player.render(4800)
        self.assertTrue(np.any(output))
        self.assertAlmostEqual(self.player.snapshot()['position'], .1)
        self.player.playing = False
        self.assertFalse(np.any(self.player.render(4800)))
        self.assertAlmostEqual(self.player.snapshot()['position'], .1)

    def test_seek_pitch_and_end_of_track(self):
        self.player.seek(2)
        self.player.set_rate(1.1)
        self.player.playing = True
        self.player.render(4800)
        self.assertAlmostEqual(self.player.snapshot()['position'], 2.11)
        self.player.seek(3.99)
        self.player.render(4800)
        self.assertEqual(self.player.snapshot()['position'], 4)
        self.assertFalse(self.player.playing)
        self.player.seek(-4)
        self.assertEqual(self.player.snapshot()['position'], 0)

    def test_grid_validation_and_downbeat_offset(self):
        self.player.configure_grid(120, .5)
        self.player.seek(.5)
        self.assertEqual(self.player.snapshot()['beat_index'], 0)
        self.player.seek(2)
        self.assertEqual(self.player.snapshot()['beat_index'], 3)
        for bpm, origin in ((0, 0), (float('nan'), 0), (128, -1), (128, float('inf'))):
            with self.assertRaises(ValueError):
                self.player.configure_grid(bpm, origin)

    def test_new_track_invalidates_previous_metadata_identity(self):
        previous = self.player.track_id
        self.player.load(track())
        self.assertIsNone(self.player.metadata(previous))
        self.assertIsNotNone(self.player.metadata(self.player.track_id))

    def test_pcm_sample_rate_conversion_and_volume(self):
        self.player.output_rate = 24000
        self.player.volume = .25
        self.player.playing = True
        output = self.player.render(2400)
        self.assertAlmostEqual(self.player.snapshot()['position'], .1)
        self.assertLessEqual(float(np.max(np.abs(output))), .101)


class StandaloneNetworkTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(encoding='latin-1', unpack_returned_tuples=True)
        self.core = self.lua.execute((ROOT / 'src/cdj-link-core.lua').read_text(encoding='utf-8'))
        self.tracks = self.lua.execute((ROOT / 'src/cdj-link-tracks.lua').read_text(encoding='utf-8'))
        self.player = Player(); self.player.load(track())
        self.network = Network(self.player)

    def tearDown(self):
        self.network.stop()

    def test_packets_roundtrip_with_actual_lua_parser(self):
        self.player.seek(1.5)
        self.player.set_rate(1.1)
        self.player.playing = True
        state = self.player.snapshot()
        beat = self.core.parse(wire.beat(1, state).decode('latin-1'), 50001)
        self.assertAlmostEqual(beat.bpm, 132, places=3)
        self.assertEqual(beat.beat, 4)
        status = self.core.parse(wire.status(1, state, 10).decode('latin-1'), 50002)
        self.assertEqual((status.trackId, status.source, status.slot, status.trackType), (1, 1, 3, 1))
        self.assertEqual(status.name, 'CDJ-3000 SIM')
        self.assertFalse(status.test)
        position = self.tracks.position(wire.position(1, state).decode('latin-1'))
        self.assertEqual((position.position, position.duration), (1500, 4))

    def test_actual_udp_sends_each_required_packet_type(self):
        receivers = []
        try:
            for port in (50000, 50001, 50002):
                receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                receiver.bind(('127.0.0.3', port)); receiver.settimeout(1)
                receivers.append(receiver)
            self.player.playing = True
            self.network.start('127.0.0.2', '127.0.0.3', query_port=0)
            self.assertEqual(receivers[0].recvfrom(2048)[0][10], 6)
            kinds = {receivers[1].recvfrom(2048)[0][10], receivers[1].recvfrom(2048)[0][10]}
            self.assertEqual(kinds, {0x28, 0x0b})
            self.assertEqual(receivers[2].recvfrom(2048)[0][10], 0x0a)
        finally:
            for receiver in receivers:
                receiver.close()

    def test_full_lua_client_fetches_metadata_and_both_waveforms(self):
        self.network.start('127.0.0.2', '127.0.0.3', query_port=0)
        self.lua.globals().Tracks = self.tracks
        self.lua.globals().make_tcp = TcpSocket
        self.lua.globals().query_port = self.network.query.server_address[1]
        self.lua.execute('''
            local socket={tcp=function()
                local py=make_tcp(); local sock={}
                function sock:settimeout(n) return py.settimeout(n) end
                function sock:bind(ip,port) return py.bind(ip,port) end
                function sock:connect(ip,port) return py.connect(ip,port) end
                function sock:send(data,index) return py.send(data,index) end
                function sock:receive(n) return py.receive(n) end
                function sock:close() return py.close() end
                return sock
            end}
            source={id=1,ip='127.0.0.2',source=1,slot=3,trackType=1,trackId=1,
                    mode=5,name='CDJ-3000 SIM',lastSeen=0}
            engine={players={[1]=source},connected=function(self,p) return p~=nil end}
            receiver=Tracks.new({socket=socket,enabled=true,waveform=true,observer=false,
                localIP='127.0.0.1',test=false,queryPort=query_port})
            receiver:observe(source,0)
        ''')
        receiver = self.lua.globals().receiver
        try:
            start = time.monotonic()
            while time.monotonic() - start < 4:
                receiver.poll(receiver, time.monotonic() - start, self.lua.globals().engine)
                if receiver.players[1].done:
                    break
                time.sleep(.002)
            value = receiver.players[1]
            self.assertTrue(value.done, value.status)
            self.assertEqual(value.title, self.player.track.title)
            self.assertEqual(value.artist, self.player.track.artist)
            self.assertEqual(value.preview.encode('latin-1'), self.player.track.preview)
            self.assertEqual(value.detail.encode('latin-1'), self.player.track.detail)
            self.assertGreaterEqual(self.network.queries, 5)
        finally:
            receiver.stop(receiver)

    def test_failed_bind_closes_partial_server(self):
        blocker = socket.socket(); blocker.bind(('127.0.0.2', 0)); blocker.listen()
        try:
            with self.assertRaises(OSError):
                self.network.start('127.0.0.2', '127.0.0.3', query_port=blocker.getsockname()[1])
            self.assertFalse(self.network.running)
            self.assertIsNone(self.network.database)
        finally:
            blocker.close()

    def test_stop_releases_metadata_ports_and_restart_works(self):
        self.network.start('127.0.0.2', '127.0.0.3', query_port=0)
        port = self.network.query.server_address[1]
        self.network.stop()
        self.network.start('127.0.0.2', '127.0.0.3', query_port=port)
        self.assertTrue(self.network.running)

    def test_new_track_request_cannot_return_old_title(self):
        self.network.start('127.0.0.2', '127.0.0.3', query_port=0)
        with socket.create_connection(self.network.database.server_address, timeout=2) as client:
            client.sendall(wire.number(1)); self.assertEqual(wire.receive_exact(client, 5), wire.number(1))
            client.sendall(wire.message(1, 0, [wire.number(1)])); wire.receive_message(client)
            client.sendall(wire.message(2, 0x2002, [wire.number(0x01010301), wire.number(1)]))
            self.assertEqual(wire.receive_message(client)[1], 0x4000)
            self.player.load(track())
            client.sendall(wire.message(3, 0x3000, [wire.number(0x01010301), wire.number(0), wire.number(3),
                                                 wire.number(0), wire.number(3), wire.number(0)]))
            self.assertEqual(wire.receive_message(client)[1], 0x4003)

    def test_bad_and_oversized_tcp_requests_are_closed(self):
        self.network.start('127.0.0.2', '127.0.0.3', query_port=0)
        with socket.create_connection(self.network.database.server_address, timeout=2) as client:
            client.sendall(wire.number(1)); wire.receive_exact(client, 5)
            header = wire.message(1, 0, [wire.blob(b'a')])[:32]
            client.sendall(header + b'\x14' + struct.pack('>I', 2**30))
            self.assertEqual(client.recv(1), b'')


if __name__ == '__main__':
    unittest.main()
