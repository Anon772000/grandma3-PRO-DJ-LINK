# Changelog

## 1.1.0-rc.1 — 2026-11-21

Release candidate; native verification of the new interface picker on grandMA3 compact 2.4.2.2 is pending.

- Added **Network interface** dropdown using `Root().Interfaces`, with one choice per IPv4 address and a Refresh action.
- Automatically fills interface IP, MAC, subnet mask and calculated broadcast; no operating-system commands or changes to console interface settings.
- Saves interface identity and refreshes an unambiguous DHCP address on listener startup. Missing/replaced interfaces and ambiguous address changes require reselection.
- Excludes loopback and internal imx6 interfaces. Disconnected/unaddressed ports are shown as unavailable.
- Keeps broadcast reception on wildcard sockets, filters received packets to the selected subnet, and binds the status socket and observer sender to the selected IP.
- Retains the original **Manual / Advanced** network form and existing manual preferences.
- Added `Plugin 8 "network"` to open network settings without starting a listener.
- Applying settings disarms output, restarts existing reception and reports restart failures with partial sockets closed.
- Added 30 interface and host regression tests; all 48 tests pass under Lua 5.4.

## 1.0.0 — 2026-11-21

First public release of **grandMA3 PRO DJ LINK**, previously named CDJ Link Sync.

- Native grandMA3 dashboard for players A–F / CDJs 1–6.
- Fixed-player or automatic tempo-master following.
- Song and Bump speed-master routing with independent multipliers and enables.
- Downbeat-only FastSync/ReSync, configurable bar interval, BPM-only mode and queued manual sync.
- Explicit Arm/Disarm, saved settings, detailed network setup and a loopback Test CDJ.
- Optional CDJ Stop All recovery plugin.
- Import-ready XML with embedded Lua, USB folder structure, readable source and checksums.
- 18 automated Lua regression tests and reproducible release builds.

Hardware test reported by the maintainer: **grandMA3 compact, software 2.4.2.2, four CDJ-3000 players — worked perfectly** after correcting a VLAN configuration issue. Other hardware/software combinations remain unverified.

The packet/timing engine is unchanged from the hardware-tested plugin. Dashboard branding and distribution packaging have been updated for this release; existing `CDJ Link Sync` preference keys are retained.
