---
date: 2026-11-05
---

# A standard account reads the sandbox firewall rules through the firewall's COM interface

`Get-NetFirewallRule` answers "Access is denied" without administrator rights, so on a standard account `aicad sandbox-setup --status`, the installer, and the check after setup said the block was missing when it was in place. The firewall's COM interface lets any account read the rules, but it names a rule by its display name, so the check requires every field that could narrow the block to be open: enabled, block, both directions, every protocol, port, address, profile, and interface, no program or service, and this container's package. The rules are still checked, and a run still decides from the 8 filters and the proof inside the container.
