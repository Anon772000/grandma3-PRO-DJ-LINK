# Capturing the interface

[`images/dashboard-test-mode.png`](images/dashboard-test-mode.png) is an actual dashboard capture from the original CDJ LINK build. It shows a simulated loopback player at 128 BPM with output disarmed. The release uses the grandMA3 PRO DJ LINK name; its controls are the same.

Use real captures from grandMA3 2.4.2.2. A loopback demonstration should be captioned **Test mode / simulated player** so it is not mistaken for the four-deck hardware test.

Recommended captures:

1. **Dashboard:** connected players, IP/transport/BPM/beat columns, with output state visible.
2. **Sync settings:** selected player, FastSync/ReSync mode, interval and multipliers.
3. **Network settings:** the observer fields. Remove private network values before publishing if needed.

Press **F11** or **Print Screen** in grandMA3. With a USB drive selected, captures are stored in:

```text
grandMA3/gma3_library/media/images/
```

MA's generated filenames use `YYYYMMDD_hhmmss_displayX.png`. Copy captures to `docs/images/`, use descriptive names such as `dashboard.png` and `sync-settings.png`, and link them in the README with accurate captions. See [MA's screenshot instructions](https://help.malighting.com/grandMA3/2.4/HTML/screenshot.html).
