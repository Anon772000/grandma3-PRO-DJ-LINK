# Changelog

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
