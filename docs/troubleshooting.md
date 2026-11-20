# Troubleshooting

## Players are offline

Check the cable/link lights, addresses, subnet masks, player numbers and VLAN membership first. The console interface and decks must be able to exchange Pro DJ Link packets. In the confirmed installation, correcting the VLAN resolved the connection problem.

Set **Listen IPv4** to `*` or the console's own DJ interface address. Ensure the plugin is in real-CDJ mode, with its listener started. Player IP addresses are discovered from packets, so entering a deck's IP as the listening address will not connect to it.

## Beats arrive but play/pause or master status is missing

Enable **Announce observer** and check its interface IP, MAC and subnet broadcast. All three must describe the same DJ interface. UDP 50002 must be available and status traffic must reach the console. Without status, the UI explicitly reports inferred playback and automatic tempo-master following cannot select a source reliably.

## UDP 50000 / 50001 / 50002 already in use

1. Use **Stop listener** in the running dashboard. Closing it with X does not stop reception.
2. Stop other copies and older CDJ Link Test / DirectSync plugins.
3. Optionally run **CDJ Stop All** from a separate slot. It stops the known service, requests older listeners to stop and checks all three ports. It cannot forcibly close an unrelated or unregistered socket.
4. If a port remains busy on a console, save the show and restart the console normally. Start only one listener afterward.

On onPC, another application may own the port. Identify the owner before stopping it. Do not force-terminate grandMA3 with an unsaved show.

## BPM is visible but phasers do not follow

Verify **OUTPUT ARMED**, the selected player, each channel's enable/multiplier and the destination under **Sync settings → Masters...**. The phasers must use that speed master. Verify the relevant synchronization settings in grandMA3.

**BPM only** deliberately sends no FastSync/ReSync commands. **Sync next beat 1** waits for the next selected player's received beat 1. Values over 225 BPM are held for that channel; reduce the multiplier.

## Patterns restart too often

Increase **Sync every bars**, or set it to 0 for changes/manual requests. The CDJ's beat-grid downbeat is not a musical phrase detector. ReSync adjusts phase gradually; FastSync restarts immediately on the received downbeat.

## Report an issue

Include the console/onPC model, exact MA version, CDJ models, unique player numbers, the displayed error, input mode, sync mode and whether observer announcements are enabled. Describe the triggering steps and VLAN topology. Remove private addresses and unrelated show details from any public captures.
