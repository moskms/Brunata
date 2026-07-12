from dataclasses import dataclass, field


@dataclass
class MeterReading:
    meter_id: int
    meter_no: str
    placement: str
    allocation_unit: str  # "W"=varmt vand, "K"=koldt vand, "O"=varme, "P"=puls
    unit: int             # 8=m³, 1=enheder, 7=kWh
    unit_label: str
    scale: float | None
    reading_value: float | None
    reading_date: str | None
    transmitting: bool


@dataclass
class ConsumptionData:
    heat_kwh: float | None
    hot_water_m3: float | None
    cold_water_m3: float | None
    last_updated: str | None
    raw_meters: list[MeterReading] = field(default_factory=list)
