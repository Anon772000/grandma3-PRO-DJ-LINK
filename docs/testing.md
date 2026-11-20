# Testing

## Confirmed hardware

| Item | Tested setup |
| --- | --- |
| Console | grandMA3 compact |
| Software | 2.4.2.2 |
| Players | Four CDJ-3000s |
| Result | Maintainer reported: **worked perfectly** |
| Network finding | The initial connection problem was a VLAN configuration issue; operation worked after correction. |

This is a field report supplied by the maintainer, not a claim of certification or universal compatibility. CDJ firmware versions, switch model and measured fixture/downbeat latency were not recorded. Other console/software versions and player models are unverified.

## Automated regressions

Run with Python 3.10+ and Lupa's **Lua 5.4** runtime, matching the language generation used by grandMA3 2.4:

```sh
python -m pip install -r requirements-dev.txt
python tools/build-release.py
python -m unittest discover -s tests -v
python tools/build-release.py --check
```

The 18 tests cover:

- Unset MA variables returning zero Lua values at startup.
- Destination masters, effective BPM with pitch and beat-1-only sync.
- Duplicate beat suppression, recent paused status and selected-player gating.
- Automatic master handoff, conflicting/stale master status and reordered status counters.
- On-change cadence, manual requests, ReSync and BPM-only modes.
- Monitor previews and resetting output state on arming.
- Disconnect state, player-number collisions and separation of Test/Live input.
- Malformed, truncated, out-of-range and unknown packets.
- Target BPM limits and disarming after a failed MA command.

The build script verifies embedded XML round trips, ZIP contents and checksums. `--check` confirms that committed distribution files match a fresh build. Neither test execution nor building requires a console, and the build never installs files into a local MA library.

## Native onPC development checks

Development on grandMA3 onPC 2.4.2.2 included UDP loopback reception, 128→130 BPM master readback, accepted native FastSync and ReSync commands on received beat 1, pause/disconnect gating and an independent Python sender exercising A→B master handoff at 128/132 BPM. The masters' original values were restored after those tests.

These checks validate packet handling and accepted console commands. They do not measure physical fixture phase or end-to-end audio/light timing.

## Independent loopback sender

Open the plugin in Test mode. Its built-in simulator should be disconnected with **Test: Disconnect** when using the independent sender, so two simulators do not compete for the same player number. Keep output disarmed for monitoring, then run:

```sh
python tools/simulate-cdj.py --scenario
```

The 15-second scenario sends presence/status/beat packets for players A/B, changes the master, pauses playback, sends duplicate beats and finally stops traffic. It uses `127.0.0.1` only. It does not emulate a hardware player's network join sequence or prove hardware compatibility.
