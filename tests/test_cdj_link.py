from pathlib import Path
import struct
import unittest

from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
MAGIC = b'Qspt1WmJOL'


def packet(kind, size, player, name='CDJ-3000'):
    data = bytearray(size)
    data[:10] = MAGIC
    data[10] = kind
    start = 11 if kind == 0x28 else 12
    data[start:start + len(name)] = name.encode()
    data[0x21] = player
    return data


def beat(player=1, bpm=128, bar=1, pitch=0x100000, name='CDJ-3000'):
    data = packet(0x28, 96, player, name)
    struct.pack_into('>I', data, 0x54, pitch)
    struct.pack_into('>H', data, 0x5a, round(bpm * 100))
    data[0x5c] = bar
    data[0x5f] = player
    return bytes(data).decode('latin-1')


def status(player=1, playing=True, master=True, counter=1, name='CDJ-3000'):
    data = packet(0x0a, 0xd4, player, name)
    data[0x7b] = 3 if playing else 5
    data[0x89] = (0x40 if playing else 0) | (0x20 if master else 0)
    struct.pack_into('>I', data, 0xc8, counter)
    return bytes(data).decode('latin-1')


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(encoding='latin-1', unpack_returned_tuples=True)
        self.core = self.lua.execute((ROOT / 'src/cdj-link-core.lua').read_text())
        self.commands = []
        self.cfg = self.lua.eval("{player=1,sync='FastSync',every=1,timeout=5,input='Live',song={master='3.1',ratio=1,enabled=true},bump={master='3.3',ratio=0.5,enabled=true}}")
        self.env = self.lua.table()
        self.env.command = lambda text: (self.commands.append(text) is None, 'OK')
        self.engine = self.core.new(self.cfg, self.env)
        self.engine.arm(self.engine, True)

    def ingest(self, data, port=50001, time=0, ip='127.0.0.1'):
        return self.engine.ingest(self.engine, data, port, ip, time)

    def sync_commands(self):
        return [c for c in self.commands if c.startswith(('FastSync', 'ReSync'))]

    def test_startup_unset_ma_variables_return_zero_values(self):
        self.lua.execute("PluginVars=function()return {}end; GetVar=function()return end; ObjectList=function()return {{}}end")
        self.lua.execute((ROOT / 'dist/grandma3-pro-dj-link.lua').read_text())
        self.assertEqual(self.lua.globals().CDJLINK.cfg.player, 1)

    def test_correct_masters_and_downbeat_only(self):
        self.ingest(beat(bar=2))
        self.assertEqual(self.commands, ['Master 3.1 At BPM 128.00', 'Master 3.3 At BPM 64.00'])
        self.ingest(beat(bar=3), time=.5)
        self.ingest(beat(bar=4), time=1)
        self.ingest(beat(bar=1), time=1.5)
        self.assertEqual(self.sync_commands(), ['FastSync Master 3.1', 'FastSync Master 3.3'])
        self.assertTrue(all(v.beat == 1 for v in self.engine.events.values() if 'Sync' in v.text))

    def test_effective_bpm_includes_pitch(self):
        self.ingest(beat(bpm=120, pitch=0x180000))
        self.assertIn('Master 3.1 At BPM 180.00', self.commands)

    def test_duplicates_do_not_retrigger(self):
        self.ingest(beat())
        self.ingest(beat(), time=.01)
        self.assertEqual(len(self.sync_commands()), 2)
        self.assertEqual(self.engine.duplicates, 1)

    def test_paused_status_gates_beat_commands(self):
        self.ingest(status(playing=False), 50002)
        self.ingest(beat(), time=.1)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.engine.state(self.engine, self.engine.players[1], .1), 'PAUSED / STOPPED')

    def test_wrong_player_does_not_drive_masters(self):
        self.ingest(beat(player=2))
        self.assertEqual(self.commands, [])

    def test_auto_master_handoff(self):
        self.cfg.player = 0
        self.ingest(status(player=1), 50002)
        self.ingest(beat(player=2, bpm=130), time=.1)
        self.assertEqual(self.commands, [])
        self.ingest(beat(player=1), time=.2)
        self.ingest(status(player=1, master=False, counter=2), 50002, time=.4)
        self.ingest(status(player=2), 50002, time=.4)
        self.ingest(beat(player=2, bpm=130), time=.7)
        self.assertEqual(self.engine.active, 2)
        self.assertIn('Master 3.1 At BPM 130.00', self.commands)

    def test_ambiguous_or_stale_master_waits(self):
        self.cfg.player = 0
        self.ingest(status(player=1), 50002)
        self.ingest(status(player=2), 50002)
        self.ingest(beat(), time=.1)
        self.assertEqual(self.commands, [])
        self.ingest(beat(), time=2)
        self.assertEqual(self.commands, [])

    def test_reordered_status_cannot_reverse_play_state(self):
        self.ingest(status(playing=False, counter=20), 50002)
        self.ingest(status(playing=True, counter=19), 50002, time=.1)
        self.ingest(beat(), time=.2)
        self.assertEqual(self.commands, [])

    def test_on_change_mode_and_manual_sync_request(self):
        self.cfg.every = 0
        self.ingest(beat())
        self.ingest(beat(), time=2)
        self.assertEqual(len(self.sync_commands()), 2)
        self.engine.pending = True
        self.ingest(beat(bar=2), time=2.5)
        self.assertEqual(len(self.sync_commands()), 2)
        self.ingest(beat(), time=4)
        self.assertEqual(len(self.sync_commands()), 4)

    def test_resync_and_bpm_only(self):
        self.cfg.sync = 'ReSync'
        self.ingest(beat())
        self.assertEqual(self.sync_commands(), ['ReSync Master 3.1', 'ReSync Master 3.3'])
        self.cfg.sync = 'Off'
        self.ingest(beat(bpm=130), time=2)
        self.assertEqual(len(self.sync_commands()), 2)

    def test_monitor_previews_and_arm_resets_state(self):
        self.engine.arm(self.engine, False)
        self.ingest(beat())
        self.assertEqual(self.commands, [])
        self.assertEqual(self.engine.previews, 1)
        self.engine.arm(self.engine, True)
        self.ingest(beat(bar=2), time=.5)
        self.assertEqual(len(self.commands), 2)
        self.ingest(beat(), time=2)
        self.assertEqual(len(self.sync_commands()), 2)

    def test_disconnect_and_no_false_paused_inference(self):
        self.ingest(beat())
        p = self.engine.players[1]
        self.assertEqual(self.engine.state(self.engine, p, 3), 'CONNECTED / NO BEATS')
        self.assertEqual(self.engine.state(self.engine, p, 6), 'OFFLINE')
        self.assertEqual(len(self.sync_commands()), 2)  # no synthetic catch-up on loss

    def test_player_number_collision_holds_output(self):
        self.ingest(beat(), ip='10.1.1.1')
        self.ingest(beat(), time=.5, ip='10.1.1.2')
        self.ingest(beat(), time=1, ip='10.1.1.1')
        self.assertEqual(len(self.sync_commands()), 2)

    def test_simulator_cannot_drive_live_mode(self):
        self.ingest(beat(name='CDJLINK TEST 1'))
        self.assertEqual(self.commands, [])
        self.cfg.input = 'Test'
        self.ingest(beat())
        self.ingest(beat(name='CDJLINK TEST 1'), ip='10.1.1.1')
        self.assertEqual(self.commands, [])
        self.ingest(beat(name='CDJLINK TEST 1'))
        self.assertEqual(len(self.sync_commands()), 2)

    def test_malformed_truncated_unknown_and_out_of_range(self):
        valid = beat()
        for length in range(96):
            self.assertIsNone(self.core.parse(valid[:length], 50001))
        for data in ['', 'bad' * 50, beat(bar=0), beat(bar=5), beat(player=7), beat(pitch=0), beat(bpm=655.35), beat(bpm=19), beat(bpm=301)]:
            self.assertIsNone(self.core.parse(data, 50001))
        self.assertEqual(self.commands, [])

    def test_out_of_range_target_holds_that_channel(self):
        self.cfg.bump.ratio = 2
        self.ingest(beat(bpm=128))
        self.assertFalse(any('3.3' in c for c in self.commands))
        self.assertIn('Master 3.1 At BPM 128.00', self.commands)

    def test_command_failure_disarms_service(self):
        self.env.command = lambda text: (False, 'test failure')
        self.ingest(beat())
        self.assertFalse(self.engine.armed)


if __name__ == '__main__':
    unittest.main(verbosity=2)
