from .models import ConsumptionData, MeterReading


def _meter_from_dict(m: dict) -> MeterReading:
    return MeterReading(
        meter_id=m["meter_id"],
        meter_no=m["meter_no"],
        placement=m["placement"],
        allocation_unit=m["allocation_unit"],
        unit=m["unit"],
        unit_label=m["unit_label"],
        scale=m.get("scale"),
        reading_value=m.get("reading_value"),
        reading_date=m.get("reading_date"),
        transmitting=m.get("transmitting", False),
    )


def parse_consumption_payload(payload: dict) -> ConsumptionData:
    """Parse a consumption.json payload dict into a ConsumptionData object.

    Expected top-level keys:
        heat_kwh, hot_water_m3, cold_water_m3, last_updated, raw_meters
    Each entry in raw_meters must have:
        meter_id, meter_no, placement, allocation_unit, unit, unit_label,
        scale, reading_value, reading_date, transmitting
    """
    return ConsumptionData(
        heat_kwh=payload.get("heat_kwh"),
        hot_water_m3=payload.get("hot_water_m3"),
        cold_water_m3=payload.get("cold_water_m3"),
        last_updated=payload.get("last_updated"),
        raw_meters=[_meter_from_dict(m) for m in payload.get("raw_meters", [])],
    )
