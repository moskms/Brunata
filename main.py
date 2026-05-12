import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from src.brunata_client import BrunataClient
from src.brunata_client.exceptions import BrunataDataError

load_dotenv()


def _print_summary(data) -> None:
    print("=== Brunata forbrug ===")
    print(f"  Varme        : {data.heat_kwh} kWh-ækvivalent")
    print(f"  Varmt vand   : {data.hot_water_m3} m³")
    print(f"  Koldt vand   : {data.cold_water_m3} m³")
    print(f"  Opdateret    : {data.last_updated}")
    print(f"  Antal målere : {len(data.raw_meters)}")


def run_offline(file: Path, output: Path | None, summary: bool) -> None:
    try:
        data = BrunataClient.load_from_file(file)
    except BrunataDataError as e:
        print(f"Fejl: {e}")
        raise SystemExit(1)

    if summary:
        _print_summary(data)
    else:
        print(json.dumps(asdict(data), indent=2, ensure_ascii=False))

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(asdict(data), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nGemt til {output}")


async def run_live() -> None:
    # TODO: live mode — henter data direkte fra Brunata og gemmer til fil
    raise NotImplementedError(
        "Live mode er endnu ikke implementeret.\n"
        "Kør først: python main.py --file data/consumption.json"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Brunata Online klient — henter forbrugsdata",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--file",
        metavar="PATH",
        default="data/consumption.json",
        help="Indlæs data fra en lokal JSON-fil (offline/test mode). "
             "Standard: data/consumption.json",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="(TODO) Hent data direkte fra Brunata API med credentials fra .env",
    )
    p.add_argument(
        "--output",
        metavar="PATH",
        help="Gem JSON-output til denne fil",
    )
    p.add_argument(
        "--summary",
        action="store_true",
        help="Print en kort, læsbar opsummering i stedet for rå JSON",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    output = Path(args.output) if args.output else None

    if args.live:
        asyncio.run(run_live())
    else:
        run_offline(file=Path(args.file), output=output, summary=args.summary)


if __name__ == "__main__":
    main()
