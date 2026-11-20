# Experimental track display — v1.2.0-alpha.1

The stable interface-dropdown release is [v1.1.0](https://github.com/Anon772000/grandma3-PRO-DJ-LINK/releases/tag/v1.1.0). Track display is a separate development build. It has not been verified on a physical compact or with real decks yet.

## Try it

1. Stop the previous listener. Import **grandma3-pro-dj-link-track-preview.xml** from the local MA library into an empty Plugin pool slot. Run one listener at a time.
2. Open the plugin and choose **Track view**. **Test CDJ** on the main dashboard supplies a clearly labelled synthetic title, artist, 3-minute track and waveform. Cue buttons jump the simulated playhead by 15 seconds.
3. For real CDJ-3000s, select the DJ network interface and enable **Announce observer**. Under **Track view → Track settings**, enable **Fetch track metadata** and **Download waveforms**, then Apply. Applying settings disarms output.
4. Choose **Use real CDJs**. Load a track and check its title, artist, elapsed and remaining track time, full-track waveform and scrolling detail. Choose a fixed display player or follow the sync source. Detail window size is adjustable from 4 to 120 seconds.
5. Check paused playback, a cue jump, a loop, a track change, another player's USB as the track source, and loss of network data. Keep output disarmed while checking the new display.

## What is implemented

- CDJ-3000 precise-position datagrams, separate from beat coalescing. A position packet cannot replace a pending beat packet. Position goes unknown after one second without updates.
- Track title, artist and duration through read-only dbserver metadata requests to the player holding the source media.
- Whole-track blue-waveform analysis and detailed analysis at 150 samples per track second, rendered as 96 peak-preserving columns with a highlighted playhead. Rendering uses one display colour; RGB/three-band colouring is not implemented.
- Elapsed and remaining **track time** and whole-track progress. These are not a prediction of the DJ's next transition or wall-clock time until playback ends; pitch changes, loops and live mixing affect that.
- Test-mode synthetic title/waveform and cue jumps. No audio is downloaded or played.
- Metadata downloads default off and are saved with the show. Timers can work without metadata downloads if CDJ-3000 position packets are arriving.

## Networking and scope

The UDP observer stays on device 7. For CDJ-3000 metadata, the TCP query context uses the target source deck's number, following Beat Link's treatment of metadata-flexible players. It does not announce a duplicate deck number. Metadata from older source players is currently reported as unsupported instead of borrowing another deck's number.

The plugin queries TCP **12523** for the remote database port, then uses the returned TCP port (commonly 1051). The connection binds the chosen DJ interface IP. Queries are nonblocking and serialized, with bounded responses, stage timeouts, retry backoff and socket cleanup. Metadata/display errors do not send MA commands or stop the beat listener. Missing waveforms are retried after 30 seconds, up to three successful metadata fetches per loaded track. A new track clears old titles, waveforms and position.

Streaming and non-rekordbox metadata depend on what the source player exposes. The current implementation supports title/artist when returned by the source; artwork, cue annotations and phrase analysis are not included.

## Verification

The original 48 regressions still pass. New checks exercise binary parsing, fragmented replies, omitted empty blobs, Unicode, malformed and oversized fields, source matching, stale positions, track changes, four-player query context, timeouts, socket cleanup, waveform bins and host beat/position coalescing. An independent Python TCP fixture supplies metadata, preview and detailed waveform data over real local sockets to the Lua client through a LuaSocket-compatible adapter. The native MA UI is checked structurally with mocked UI objects; its actual rendering and the console's LuaSocket TCP behaviour still require a native test.

Windows Computer Use was unavailable during development (`Computer Use native pipe is unavailable`, OS error 2), so no native visual or physical-console success is claimed.

## Protocol references

- [Precise position packets](https://djl-analysis.deepsymmetry.org/djl-analysis/beats.html#_absolute_position_packets)
- [Track metadata and waveform messages](https://djl-analysis.deepsymmetry.org/djl-analysis/track_metadata.html)
- [Beat Link CDJ-3000 query-context handling](https://github.com/Deep-Symmetry/beat-link/blob/main/src/main/java/org/deepsymmetry/beatlink/dbserver/ConnectionManager.java)
- [Beat Link precise-position offsets](https://github.com/Deep-Symmetry/beat-link/blob/main/src/main/java/org/deepsymmetry/beatlink/PrecisePosition.java)

Protocol implementation is original Lua, based on the documented wire formats; no Beat Link code or runtime dependency is bundled.
