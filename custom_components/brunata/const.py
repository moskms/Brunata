from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "brunata"
PLATFORMS = [Platform.SENSOR]
UPDATE_INTERVAL = timedelta(hours=1)

# ConfigEntry.data flag: has the one-time historical backfill (mountingDate ->
# first setup) already run? Must only ever happen once — see coordinator.py.
CONF_HISTORY_IMPORTED = "history_imported"

# allocationUnit -> (entity_id slug, display name). These produce exactly the
# entity IDs required by copilot-instructions-del2.md (sensor.brunata_varme,
# sensor.brunata_varmt_vand, sensor.brunata_koldt_vand). sensor.py sets
# entity_id explicitly from this table so coordinator.py can predict the same
# entity_id ahead of time when handing history off to statistics.py.
ALLOCATION_UNIT_SLUGS = {"O": "varme", "W": "varmt_vand", "K": "koldt_vand"}
ALLOCATION_UNIT_NAMES = {"O": "Varme", "W": "Varmt vand", "K": "Koldt vand"}
