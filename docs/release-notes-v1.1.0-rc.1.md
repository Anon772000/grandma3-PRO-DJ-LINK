# grandMA3 PRO DJ LINK v1.1.0-rc.1

Select the DJ network interface from a dropdown and let the plugin fill in its address details.

![Native network interface dropdown with adapter list and populated address details](https://raw.githubusercontent.com/Anon772000/grandma3-PRO-DJ-LINK/feat/network-interface-dropdown/docs/images/network-interface-dropdown.png)

*Maintainer-supplied grandMA3 onPC capture. The dashboard is in Test mode with a simulated player and output disarmed. The selected WSL adapter illustrates the address display; select the interface connected to your DJ network.*

## What changed

- **Network interface** selector for the station's external interfaces, with one option per IPv4 address and a Refresh button.
- Automatic interface IP, MAC, subnet mask and broadcast calculation.
- Saved interface identity, startup recheck and unambiguous DHCP address refresh.
- Original network fields retained as **Manual / Advanced**; existing manual settings are preserved.
- Disconnected ports are marked unavailable; internal imx6 and loopback interfaces are excluded.
- Broadcast reception stays enabled, packets are filtered to the chosen subnet, and status/observer sockets use its IP.
- Failed restarts report their error and release partial sockets. Applying settings disarms output.
- `Plugin 8 "network"` opens settings without starting a listener.

## Install and use

Download **grandma3-pro-dj-link-v1.1.0-rc.1.zip** and copy its **grandMA3** folder to the root of your USB, then import **grandma3-pro-dj-link.xml** into a Plugin pool slot. The standalone XML asset embeds all required Lua. Stop the previous listener before replacing it; run one copy.

Open **Network → Network interface**, choose the DJ port, enable **Announce observer** for detailed player status and tap **Apply**. Verify the incoming player data before arming. No console interface settings are changed.

## Validation and scope

**48 automated tests pass** under Lua 5.4: the existing 18 timing/startup tests plus 30 interface and host regressions. Embedded XML, package integrity and checksums are checked. The musical timing engine is unchanged.

**This is a release candidate.** The maintainer's onPC screenshot confirms the native dropdown, adapter enumeration and populated address details. Live CDJ traffic through the selected interface and operation on the grandMA3 compact running 2.4.2.2 still need verification. Manual / Advanced remains available if the interface list or MAC is not exposed.

The maintainer's successful **compact 2.4.2.2 + four CDJ-3000s** report applies to **v1.0.0** and is not a new hardware test of this release. v1.0.0 remains the latest stable release.

See the [network guide](https://github.com/Anon772000/grandma3-PRO-DJ-LINK/blob/v1.1.0-rc.1/docs/network-setup.md) and [testing notes](https://github.com/Anon772000/grandma3-PRO-DJ-LINK/blob/v1.1.0-rc.1/docs/testing.md).
