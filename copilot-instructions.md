# Brunata Online Klient – Del 1 (Standalone Python)

## Projektet

Du er en senior Python-udvikler med speciale i web scraping, async HTTP-klienter, reverse engineering af login-floews og web API'er.

### Opgave

Udvikl en **ren, separat Python-klient** der kan logge ind på Brunata Online for beboere (online.brunata.com) og hente forbrugsdata (varme, varmt vand, koldt vand).

Dette program skal løbe helt UAFHÆNGIGT af Home Assistant og AppDaemon. Det skal kun kunne lave ind på Brunata, logge ind, hente data og gemme/printe dem.

### Rammer og begrænsninger

#### Python-version

- Brug **Python 3.12** som hovedversion
- Koden skal kører stabilt med Python 3.12 og 3.13
- Brug features der er tilgængelige i Python 3.10+ (match statements, dataclass, type hints)
- Angiv version i `pyproject.toml` som `python = "^3.12"`

#### Tilladte biblioteker

Brug kun disse pakker i dette projekt:
- `httpx` - async HTTP requests med session og cookies
- `beautifulsoup4` - HTML parsing hvis data ligger i HTML
- `lxml` - hurtig HTML/XML parser (behander sammen med BeautifulSoup)
- `pydantic` - data-modeller (validering af JSON/HTML data)
- `python-dotenv` - til at læse credentials fra .env fil
- `pytest` - til test

BEMÆRK:
- Brug kun `httpx`. Brug requests kun hvis httpx viser sig at have problemer med cookies eller sessions.
- Undgå Playwright i denne fase. Denne fase skal kun bruge requests/httpx. Playwright er senere fase.
- Undgå kørsel af JavaScript. Vi forventer at data kan hentes via HTTP-kald alene.

#### Interne begrænsninger

- Der må IKKE være nogen forbindelse til Home Assistant i dette program
- Der må IKKE bruges `appdaemon`, `hassapi`, Home Assistant API, eller sensor-oprettelse
- Programmet skal være **100% isolation** fra HA
- Credentials skal læses fra `.env` eller config-fil, aldrig hardcode
- Ingen eksterne databeslag fra HA, ingen HA context

#### Mål for denne fase

Denne fase er færdig når:
1. Et separat Python-projekt kan køres i VS Code og hente data fra Brunata Online
2. Programmet kan logge ind med brugernavn og password
3. Programmet kan hente forbrugsdata efter login (varme, varmt vand, koldt vand)
4. Data kan gemmes som JSON eller udskrives struktureret i terminalen
5. Der er mindst én test der viser at klienten virker
6. Koden kan importeres uden fejl

### Projektstruktur

```bash
brunata_client/
├── copilot-instructions.md     # Denne fil
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
├── src/
│   └── brunata_client/
│       ├── __init__.py
│       ├── client.py
│       ├── parser.py
│       ├── models.py
│       └── exceptions.py
├── tests/
│   ├── __init__.py
│   ├── test_client.py
│   └── conftest.py
└── main.py
```

### Krav til koden

#### client.py

Lav en klasse `BrunataClient` med:
- `__init__(username: str, password: str)`
- `async def login()` - logger ind og vedligeholder session
- `async def fetch_consumption_data() -> dict` - henter forbrugsdata
- `async def close()` - lukker httpx-session
- Håndterer cookies, redirects, CSRF tokens
- Bruger async/await gennemgående

#### parser.py

Lav funktioner til:
- Parsing af HTML eller JSON svar fra Brunata
- Udtrækning af: varme (kWh), varmt vand (m³), koldt vand (m³)
- Returner datastrukturen som defineret i models.py

#### models.py

Lav dataclass kaldet `ConsumptionData`:
```python
from dataclasses import dataclass

@dataclass
class ConsumptionData:
    heat_kwh: float | None
    hot_water_m3: float | None
    cold_water_m3: float | None
    last_updated: str | None
```

#### exceptions.py

Opret:
- `BrunataLoginError`
- `BrunataDataError`
- `BrunataSessionError`

#### main.py

Lav en CLI funktion:
```python
async def main():
    client = BrunataClient(username, password)
    await client.login()
    data = await client.fetch_consumption_data()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
```

Implementér aflæsning fra `.env` med `python-dotenv`.

#### credentials og konfiguration

Brug `.env` fil med følgende:
```env
BRUNATA_USERNAME=dit@email.dk
BRUNATA_PASSWORD=din_hemmelig_kode
```

Læses ind med `python-dotenv`. Aldrig hardcode credentials i kode.

### Reverse engineering guidance

For at kunne logge ind og hente data skal du bruge browserens DevTools:

1. Åbn https://online.brunata.com i Chrome/Edge/Firefox
2. Åbn DevTools (F12) → Network fanen
3. Log ind manuelt på siden
4. Kig efter:
   - POST request til login (ofte /login, /auth, /saml, eller Azure B2C endpoint)
   - Cookies der sættes efter login
   - Eventuelle CSRF tokens fra login-siden
   - Redirect URLs efter login
   - API kald som henter forbrugsdata (ofte JSON)
   - Headers der er nødvendige (User-Agent, Referer, Origin, Authorization)
5. Åbn den side/endpoint hvor forbrug vises
6. Kig efter API kald eller JSON data på siden

BEMÆRK: Brunata kan bruge Azure AD B2C eller lignende SSO-loginflow. Hvis det er tilfældet, skal du finde det endelige API kald efter SSO-redirection, ikke selve SSO-flowet.

### Acceptkriterier

Dette projekt er FÆRDIGT for denne fase når:

- [ ] `python -m src.brunata_client` kan køres fra terminal og printe JSON eller struktureret data
- [ ] Client kan logge ind med rigtige credentials
- [ ] Client kan hente forbrugsdata
- [ ] Data kan printes som struktureret JSON/Python-dict
- [ ] Der er mindst én test i tests/test_client.py
- [ ] README indeholder installations- og kørselsinstruktion
- [ ] Projektet kan importeres uden fejl: `from brunata_client import BrunataClient`

### Dokumentation

README.md skal indeholde:
- Overskrift og formål
- Installations- og opsætningsinstruktion
- `pip install -e .`
- Opsætning af `.env`
- Kørsel: `python main.py` eller `python -m src.brunata_client`
- Eksempel på output
- Note om at dette er en uofficiel klient og kan bryde ved portalændringer

### Fase 1 arbejdsproces

Du skal arbejde i **trin**. Efter hvert trin STOPPER du og afventer feedback.

**Trin 1**: Projektstruktur og pyproject.toml
- Opret mappesstruktur
- `pyproject.toml` med afhængige
- `.env.example`, `.gitignore`

**Trin 2**: Models og exceptions
- `models.py` med ConsumptionData
- `exceptions.py` med 3 exceptions

**Trin 3**: BrunataClient skelet
- `client.py` med klienten
- TODO comments hvor endpoints mangler

**Trin 4**: main.py og CLI
- CLI med dotenv
- Mock-data eller testdata

**Trin 5**: Parser og dataudtræk
- HTML/JSON parsing logic
- TODO hvis endpoint ukendt

**Trin 6**: Test og dokumentation
- En simpel test
- README.md

Efter hvert trin:
1. Vis alle nye filer med fuldt indhold
2. Forklar hvordan jeg tester trinnet
3. Giv Status-blok:
   - Trin: [navn]
   - Status: ✅ FÆRDIG / ⏳ I GANG / ❌ BLOKERET
   - Test: [hvad jeg skal køre]
   - Næste: [trin]

STOP efter Trin 6 og afvent godkendelse før næste fase.

### Krav til kommunikation

- Brug **dansk** i udtalelser og forklaringer
- Brug **engelsk** i kode, filnavne, variable, klasser
- Kommenter kun hvis det tilfører reel værdi
- Marker TODOs tydeligt
- Vis **hele filen** hver gang (ikke kun diff)
- Angiv kommandoer til terminal tydeligt

### Start

Start nu med **Trin 1: Projektstruktur og pyproject.toml**.