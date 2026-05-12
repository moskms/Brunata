#!/usr/bin/env python3
"""
ha_cleanup.py — kør i HA-terminalen.
Fjerner alt Brunata- og AppDaemon-relateret der ikke hører til HA's kernesystem.

Kørsel: python /homeassistant/ha_cleanup.py
"""
import shutil
import sys
from pathlib import Path

AD_APPS    = Path("/addon_configs/a0d7b954_appdaemon/apps")
HA_APPS    = Path("/homeassistant/apps")
APPS_YAML  = AD_APPS / "apps.yaml"

removed = []
kept    = []

def remove(p: Path) -> None:
    if p.exists():
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        removed.append(str(p))
    else:
        kept.append(f"(fandtes ikke) {p}")

print("\n=== HA Brunata/AppDaemon oprydning ===\n")

# --- AppDaemon: brunata app-mappe ---
remove(AD_APPS / "brunata")

# --- AppDaemon: apps.yaml — fjern brunata-sektion, bevar resten ---
if APPS_YAML.exists():
    lines = APPS_YAML.read_text(encoding="utf-8").splitlines(keepends=True)
    out, skip = [], False
    for line in lines:
        if line.startswith("brunata:"):
            skip = True
        elif skip and (line.startswith(" ") or line.startswith("\t") or line.strip() == ""):
            continue
        else:
            skip = False
            out.append(line)
    APPS_YAML.write_text("".join(out), encoding="utf-8")
    print(f"  OK  apps.yaml: brunata-sektion fjernet")
else:
    print(f"  --  apps.yaml fandtes ikke")

# --- /homeassistant/apps: ryd det vi ved en fejl lagde der ---
for name in ["brunata_app.py", "brunata_client", "data", "apps.yaml"]:
    remove(HA_APPS / name)

# --- deploy.py hvis den ligger i /homeassistant ---
remove(Path("/homeassistant/deploy.py"))

# --- Resultat ---
print()
if removed:
    print("Fjernet:")
    for r in removed:
        print(f"  - {r}")
else:
    print("  Intet at fjerne.")

print()
print("=== Oprydning færdig ===")
print()
print("AppDaemon apps-mappe nu:")
if AD_APPS.exists():
    for p in sorted(AD_APPS.rglob("*")):
        indent = "  " * len(p.relative_to(AD_APPS).parts)
        print(f"{indent}{p.name}{'/' if p.is_dir() else ''}")
print()
print("Genstart AppDaemon for at bekræfte ren tilstand:")
print("  ha apps restart a0d7b954_appdaemon")
