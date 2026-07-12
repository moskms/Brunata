# Brunata Online — Del 2 (Native Home Assistant Custom Component)

## Kontekst — hvad der allerede er lavet (Del 1)

`src/brunata_client/` indeholder en fungerende, isoleret Python-klient:

- `client.py` — `BrunataClient` med Azure AD B2C PKCE-login (`login()`, `refresh_login()`).
  Login-flowet er implementeret og bruger de rigtige endpoints:
  - `_AUTHORIZE_URL` → `brunatab2cprod.b2clogin.com/.../oauth2/v2.0/authorize`
  - `_SELFASSERTED_URL`, `_CONFIRMED_URL` → B2C sign-in flow
  - `_TOKEN_URL` → `online.brunata.com/online-auth-webservice/v1/rest/oauth/token`
  - Client ID og scope er allerede udfyldt korrekt.
- `models.py` / `parser.py` — `ConsumptionData` / `MeterReading` dataklasser, matcher et rigtigt
  Brunata-payload (se `data/consumption.json` for eksempel — denne fil ligger IKKE i git,
  bed brugeren om at genskabe den lokalt, eller brug testfixturen i `tests/test_client.py`).
- `fetch_consumption_data()` er **ikke implementeret** — kaster `NotImplementedError`.
  Dette er det eneste manglende stykke fra Del 1, og skal færdiggøres i denne fase (se Trin 1 nedenfor).

`appdaemon/` indeholder en AppDaemon-baseret HA-integration. **Denne fase erstatter AppDaemon-sporet
med en native custom_component.** AppDaemon-mappen må gerne blive liggende som reference, men skal
IKKE videreudvikles — marker den som deprecated i en kommentar øverst i `brunata_app.py`, og nævn i
hoved-README'en at `custom_components/brunata/` er den understøttede vej fremad.

---

## Formål med Del 2

Byg en **HACS-installerbar native Home Assistant custom_component**, hvor:

1. Brugeren installerer via HACS (custom repository: `https://github.com/moskms/Brunata`).
2. Brugeren tilføjer integrationen via HA's UI (**Indstillinger → Enheder & tjenester → Tilføj integration**)
   og indtaster **kun brugernavn og password** — intet YAML, ingen `.env`, ingen AppDaemon-addon krævet.
3. Ved opstart/tilføjelse henter integrationen automatisk **historiske perioder** Brunata stiller til
   rådighed, og importerer dem som HA long-term statistics (så historikken er der med det samme,
   ikke kun "fra nu og frem").
4. Løbende opdatering (fx hver time — Brunata-data opdateres ikke i realtid) henter nye målinger.
5. Data vises overskueligt i HA's eget frontend via et medfølgende dashboard-eksempel — dette ER
   "web-brugerfladen". Der skal IKKE bygges en selvstændig webserver/webapp ved siden af HA.

---

## ⚠️ Kritisk forudsætning — data-endpoint er ikke reverse-engineered endnu

Login-flowet er på plads, men **det konkrete endpoint der returnerer forbrugsdata (målere +
historiske perioder) er ikke fundet endnu**. `_API_BASE` i `client.py` peger på
`online.brunata.com/online-webservice/v2/rest`, men de faktiske sti-segmenter, query-parametre og
JSON-struktur for hhv. "hent målerliste" og "hent historiske perioder" kendes ikke.

**Før du (agenten) forsøger at implementere `fetch_consumption_data()` og historik-hentning:**

1. Spørg brugeren om de kan levere en HAR-fil (Chrome/Firefox DevTools → Netværk → højreklik →
   "Save all as HAR") fra en session hvor de er logget ind på https://online.brunata.com og har
   navigeret rundt til forbrugs-/historik-visningerne. Bed dem lægge den i `docs/api-capture.har`
   (opret `docs/`-mappen, og tilføj `*.har` til `.gitignore` — den indeholder access tokens).
2. Hvis en HAR-fil findes, parse den (Python `json`, HAR er JSON) og udled:
   - Endpoint(er) for målerliste/installation (fx noget i stil med `/meters` eller `/installations/{id}/meters`)
   - Endpoint for periodisk/historisk forbrug (fx `/consumption?period=...` eller lignende)
   - Hvilke HTTP-headers der kræves ud over `Authorization: Bearer <token>`
3. Hvis ingen HAR-fil findes, IMPLEMENTÉR IKKE gæt-baserede endpoints. Stop og bed brugeren om at
   levere den, evt. med en kort vejledning i `docs/how-to-capture-har.md` som du selv skriver.
4. Skriv `tests/fixtures/` med anonymiserede eksempel-JSON-svar (fjern kontonumre/adresser), så
   fremtidige tests kan køre offline uden en HAR-fil.

Dette er ikke valgfrit — gæt på API-strukturer fører til kode der ser færdig ud, men fejler i
produktion. Login-delen af projektet blev lavet rigtigt netop ved at basere sig på observerede,
virkelige kald — historik/forbrugs-delen skal have samme behandling.

---

## Trin 1 — Færdiggør `brunata_client` (live data)

- Implementér `fetch_consumption_data()` i `client.py` baseret på det reverse-engineerede endpoint.
- Tilføj `fetch_historical_periods(months_back: int = 24) -> list[ConsumptionData]` (eller lignende),
  der henter så langt tilbage Brunata tillader (typisk 12–24 måneder, afhænger af boligforening/målertype).
- Håndter token-udløb: kald `refresh_login()` automatisk ved 401, og re-login (fuldt `login()`) hvis
  refresh også fejler.
- Udvid `tests/test_client.py` med mockede HTTP-svar (`respx` til at mocke `httpx`, tilføj som dev-dependency)
  — ingen tests må lave rigtige kald til Brunata.

## Trin 2 — `custom_components/brunata/`

Opret standard HA-integrationsstruktur:

```
custom_components/brunata/
├── __init__.py          # async_setup_entry, async_unload_entry
├── config_flow.py       # UI-flow: brugernavn + password, validerer via client.login()
├── const.py             # DOMAIN, opdateringsinterval, m.m.
├── coordinator.py        # DataUpdateCoordinator, wrapper om BrunataClient
├── sensor.py             # SensorEntity pr. målertype (varme/varmt vand/koldt vand)
├── statistics.py          # Historik-import via homeassistant.components.recorder.statistics.async_import_statistics
├── manifest.json          # domain, requirements (brunata_client som pip-dependency eller vendored)
├── strings.json + translations/en.json, da.json
```

Krav til `config_flow.py`:
- Ét step: felter `username` (text) og `password` (password-type, maskeret i UI).
- Validér ved at kalde `BrunataClient(username, password).login()` inde i flowet; vis fejlbesked
  ("Forkert brugernavn eller password") hvis `BrunataLoginError` opstår — kast ikke rå exception til brugeren.
- Gem credentials i `ConfigEntry.data` (HA krypterer/beskytter dette automatisk, skriv IKKE til YAML).
- Understøt "Reconfigure"-flow, så brugeren kan opdatere password uden at fjerne/gentilføje integrationen.

Krav til `coordinator.py`:
- `update_interval` default: 1 time (Brunata-målere rapporterer ikke i realtid — undgå unødig polling).
- Ved `async_config_entry_first_refresh()`: hent også historiske perioder (Trin 1) og send til
  `statistics.py` for import, så brugeren ser historik med det samme efter opsætning — dette er
  det, brugeren specifikt har bedt om ("efter opstart hente data fra tidligere perioder").
- Historik-import skal kun ske ÉN gang (ved første opsætning / hvis der mangler statistik-data),
  ikke ved hver almindelig opdatering — brug en flag/marker i `ConfigEntry` eller tjek eksisterende
  statistik-ID'er før import for at undgå dubletter.

Krav til `sensor.py`:
- Mindst: `sensor.brunata_varme` (kWh, device_class=energy, state_class=total_increasing),
  `sensor.brunata_varmt_vand` (m³, device_class=water, state_class=total_increasing),
  `sensor.brunata_koldt_vand` (m³, device_class=water, state_class=total_increasing).
- `unique_id` skal være stabilt (baseret på `meter_id` fra Brunata, ikke på entity-navn), så
  entiteter overlever navneændringer og genstarter korrekt i Energy Dashboard.
- Tilføj `device_info` så alle sensorer grupperes under én "Brunata"-enhed i HA's enhedsoversigt.

## Trin 3 — Dashboard ("web-brugerfladen")

- Lav `docs/dashboard-example.yaml` — en færdig Lovelace-view-konfiguration brugeren kan importere
  direkte, med: forsidekort med de tre aktuelle værdier, historik-graf (statistics-graph-card)
  for hver målertype, og evt. sammenligning måned-for-måned.
- Nævn i README hvordan brugeren:
  1. Går til Indstillinger → Automatiseringer & scener → Dashboards → Rediger i YAML-tilstand → indsæt.
  2. Tilføjer varme/vand-sensorerne til det indbyggede Energy Dashboard (Indstillinger → Energidashboard),
     som automatisk giver måned/år-oversigt uden yderligere arbejde.
- Dette dashboard-YAML er MVP'et for "web-brugerfladen" — byg ikke en separat webapp.

## Trin 4 — HACS-pakning

- `hacs.json` i repo-roden.
- `manifest.json` med korrekt `domain`, `codeowners`, `version`, `requirements` (peg på
  `brunata_client` enten som git-dependency eller vendor'et direkte ind i
  `custom_components/brunata/brunata_client/`).
- README opdateres til at beskrive installation via HACS (custom repository-URL) + opsætning via UI,
  ikke længere AppDaemon-trinene som primær vej.

## Trin 5 — Definition of done

- [ ] `fetch_consumption_data()` og historik-hentning virker mod ægte reverse-engineered endpoints
      (baseret på HAR-capture, ikke gæt).
- [ ] Config flow lader bruger indtaste kun brugernavn/password og validerer login.
- [ ] Efter første opsætning er historiske perioder synlige som statistik/graf i HA.
- [ ] Sensorer for varme/varmt vand/koldt vand opdateres periodisk og virker i Energy Dashboard.
- [ ] Dashboard-YAML findes i `docs/` og er testet ved manuel import.
- [ ] `pytest` kører fuldt offline (ingen netværkskald), alle Del 1 + Del 2 tests er grønne.
- [ ] HACS-installation er testet (mindst via "custom repository" i en test-HA-instans).