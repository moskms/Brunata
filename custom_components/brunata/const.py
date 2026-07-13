from collections import defaultdict
from datetime import timedelta

from homeassistant.const import Platform
from homeassistant.util import slugify

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


def build_meter_naming(active_meters: list[dict]) -> dict[int, tuple[str, str]]:
    """meterId -> (entity object_id, display name), for one active meter per entry.

    Used identically by sensor.py (to set the entity's real entity_id) and
    coordinator.py (to predict that same entity_id ahead of time for the
    one-time statistics backfill) so the two can never drift apart. Meters
    are grouped by (allocationUnit, placement); a real collision (e.g. two
    heat meters both placed "Stue") gets meterNo appended to disambiguate,
    per copilot-instructions-del3.md.
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for meter in active_meters:
        groups[(meter["allocationUnit"], meter.get("placement") or "")].append(meter)

    naming: dict[int, tuple[str, str]] = {}
    for (allocation_unit, placement), meters in groups.items():
        type_slug = ALLOCATION_UNIT_SLUGS[allocation_unit]
        type_name = ALLOCATION_UNIT_NAMES[allocation_unit]
        collision = len(meters) > 1
        for meter in meters:
            if placement:
                object_id = f"{type_slug}_{slugify(placement)}"
                name = f"{type_name} {placement}"
            else:
                object_id = type_slug
                name = type_name
            if collision:
                object_id = f"{object_id}_{slugify(str(meter['meterNo']))}"
                name = f"{name} ({meter['meterNo']})"
            naming[meter["meterId"]] = (object_id, name)

    return naming
