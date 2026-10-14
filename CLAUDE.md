# Brunata Online — HA-integration

Dette repository indeholder en Home Assistant custom integration, der henter vand- og varmedata fra
Brunata Online (online.brunata.com) for en privat bruger, med UI-opsætning (kun brugernavn/password)
og et eget dashboard i HA's frontend.

## Læs disse filer FØRST, før du skriver noget kode

1. `README.md` — den aktuelle, brugerrettede beskrivelse: funktioner, installation, dashboard,
   datavalidering, hul-heling og versionshistorik (aktuel version står i
   `custom_components/brunata/manifest.json`, fortsat skema `1.XX`).
2. `docs/login-flow.md` — bekræftet, reverse-engineered login-flow (Keycloak, PKCE). Login går IKKE
   via Azure AD B2C, uanset hvad ældre kommentarer i `client.py` måtte antyde.
3. `docs/api-reference.md` — bekræftede data-endpoints (forbrug, målere, historik) med rigtige
   JSON-eksempler og kendte grænser.
4. `copilot-instructions*.md` — historiske opgavebeskrivelser fra de enkelte udviklingsfaser (Del 1-3).
   Læs dem kun for baggrund; de er ikke længere den gældende status.

## Nuværende status (kort)

- **Native integration er færdig og i drift:** `custom_components/brunata/` med config flow
  (brugernavn/password, inkl. reconfigure), dynamisk måler-opdagelse, sensorer, historik-import
  til HA's langtidsstatistik, adaptiv polling, debug-/reparationsservices og knapperne
  "Opdatér nu" og "Tjek for huller".
- **Dashboard-kort:** `custom_components/brunata/www/brunata-monthly-card.js` (måned/dag-visning,
  "Sidste 30 dage") via WebSocket API i `websocket_api.py`. Dashboardet "Brunata" oprettes og
  fjernes automatisk af `dashboard.py`.
- **Dataintegritet:** integrationen har sin egen forbrugs-ledger (`ledger.py`, `src/brunata_client/ledger.py`)
  og validering/hul-heling (`src/brunata_client/aggregation.py`, `reconcile.py`) — se README.
- **Python-klienten** i `src/brunata_client/` (login, `fetch_consumption_data()`,
  `fetch_meters_for_consumer()`, `fetch_meter_values()`, historik-chunking) er implementeret og
  vendored ind i `custom_components/brunata/brunata_client/`, fordi pakken ikke ligger på PyPI.
- `appdaemon/` er et ældre, udfaset spor, der IKKE skal videreudvikles.
- HACS-understøttelse (`hacs.json`, releases) er endnu ikke lavet. Installation er manuel.

## Vigtige spilleregler

- Gæt ALDRIG på API-endpoints, HTTP-headers, sessionsmekanismer eller HA-interne funktioner, der
  ikke er dokumenteret i `docs/` eller bekræftet i koden/HA's kildekode. Er noget ukendt, så sig
  det til brugeren og foreslå en konkret test, i stedet for at antage.
- Credentials (brugernavn/password) må aldrig havne i logs, tests, committed filer eller chatten.
- Personlige forbrugsdata (debug-eksporter, ledger-filer, HAR-captures) må ikke committes — se
  `.gitignore`.
- Kør `pytest tests/ -v` efter enhver ændring i `src/brunata_client/`, så intet knækker. Udvid
  testene i stedet for at slette dem.
- Når `custom_components/brunata/` ændres, så opdatér versionen i `manifest.json` og README'ens
  versionshistorik, hvis ændringen er en afsluttet milepæl.
- Hold README-afsnittet "Installation og opsætning med hjælp fra Claude" i sync med
  opsætningsflowet, når det ændres (nye felter, knapper, services eller krav).
- Brugeren deployer selv (kopierer `custom_components/brunata/` til HA og genstarter). Spørg ikke
  gentagne gange, om det er gjort; vent på, at brugeren skriver det.
