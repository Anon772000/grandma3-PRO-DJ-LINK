# Arm and Disarm from executors

The examples assume the plugin is in **Plugin 8**. Substitute its actual slot. Open the plugin and start its listener before arming.

## Separate buttons

Create a macro with one line:

```text
Plugin 8 "arm"
```

Create a second macro for Disarm:

```text
Plugin 8 "disarm"
```

To assign either macro, press **Assign**, tap the macro in the pool, then press the desired executor button. Use its **Go+** key function.

## One button with two steps

This example uses **Macro 20**, with appearances named **Red** and **Grey**. Create these appearances first and replace 20 with your macro's actual pool number.

**Line 1 — Wait: Go**

```text
Plugin 8 "arm"; Label Macro 20 "ARMED"; Assign Appearance "Red" At Macro 20
```

**Line 2 — Wait: Follow**

```text
Plugin 8 "disarm"; Label Macro 20 "DISARMED"; Assign Appearance "Grey" At Macro 20
```

The first press arms and turns the label red. The `Go` wait pauses after line 1. The next press disarms, turns the label grey and completes the macro, ready for its next run.

These label/colour commands show the macro's last requested action. They are not live plugin-state feedback: stopping the listener, changing settings, a command failure or arming from the dashboard can leave the label out of date. Check the plugin's **OUTPUT ARMED / MONITOR ONLY** display for its actual state. The macro also labels its requested action even if arming was refused because no listener was running.

## Other supported arguments

| Command | Action |
| --- | --- |
| `Plugin 8 "start"` | Start real-CDJ reception and show the dashboard, disarmed. |
| `Plugin 8 "stop"` | Stop reception and disarm output. |
| `Plugin 8 "test"` | Start loopback Test mode and show the dashboard, disarmed. |
| `Plugin 8 "arm"` | Arm the running listener without opening the dashboard. |
| `Plugin 8 "disarm"` | Disarm without stopping reception. |

MA documentation: [plugin arguments](https://help.malighting.com/grandMA3/2.4/HTML/keyword_plugin.html), [macro waits](https://help.malighting.com/grandMA3/2.4/HTML/macros.html), [assigning macros](https://help.malighting.com/grandMA3/2.4/HTML/macro_assign.html), [appearances](https://help.malighting.com/grandMA3/2.4/HTML/keyword_appearance.html).
