# Network setup

## Use the DJ interface

Connect the console's chosen Ethernet port and the CDJs to the same DJ network. On a managed switch, verify that their ports are in the same VLAN and that the switch is passing both broadcast and device-to-device traffic. An IP address in the right subnet is not enough if VLANs separate the devices.

The confirmed four-CDJ-3000 installation initially failed because of its VLAN configuration. The maintainer reported normal operation after correcting it.

Keep the existing lighting-network interface configured for its lighting role. Configure the DJ interface separately as appropriate for the venue. On a console, interface settings are in **Menu → Network → My Interfaces**. On Windows onPC, configure the adapter's IP address in Windows.

## Plugin fields

| Field | What to enter |
| --- | --- |
| Listen IPv4 | `*` for all interfaces, or the console/onPC DJ interface's own IP. Do not enter a CDJ's address here. |
| Observer interface IPv4 | The console/onPC interface on the DJ subnet. |
| Subnet broadcast IPv4 | That subnet's broadcast address. |
| Interface MAC | The MAC of that same console/onPC interface, formatted `aa:bb:cc:dd:ee:ff`. |
| Announce observer | Enable to request detailed status through device announcements. |
| Disconnect seconds | How long without packets before a player is marked offline; default 5. |

For example, on a `192.168.50.0/24` network, a console might use `192.168.50.10`, players might use other unique addresses in that subnet, and the broadcast is `192.168.50.255`. On `169.254.0.0/16`, the broadcast is `169.254.255.255`. Use the actual values for your interface.

**Observer device number is 7.** The plugin displays player numbers 1–6, so give each deck a unique supported number. Announcements are sent every 1.5 seconds. The observer sends no transport commands, audio or track-download requests.

## Ports and status

| UDP port | Used for |
| --- | --- |
| 50000 | Presence / discovery announcements |
| 50001 | Beat timing and effective BPM |
| 50002 | Detailed player status |

Passive reception can show presence and infer playback from beats. Detailed status is needed to distinguish play/pause reliably and follow the tempo master automatically. The UI says **BEATS (play inferred)** or **CONNECTED / NO BEATS** when detailed status is missing, rather than claiming a confirmed pause state.

The plugin learns player IP addresses from incoming packets. There is no list of deck IPs to enter manually.

## Change settings

Use **Stop listener**, edit **Network**, apply the values, then start the listener. Applying settings disarms output. Verify the incoming player data before arming again.

If a port remains busy, see [troubleshooting](troubleshooting.md). Do not run the older Link Test or DirectSync listener alongside this plugin.
