# Brunata Online — Home Assistant-integration

En Home Assistant custom integration, der henter vand- og varmeforbrug fra
[Brunata Online](https://online.brunata.com) for private forbrugere, og gør dataene tilgængelige
som sensorer i Home Assistant — inklusive automatisk import af historisk forbrug ved opsætning.

> Uofficiel integration. Ikke tilknyttet eller understøttet af Brunata A/S. Bygget ved at
> reverse-engineere den offentligt tilgængelige Brunata Online-portal — se
> [`docs/login-flow.md`](docs/login-flow.md) og [`docs/api-reference.md`](docs/api-reference.md)
> for detaljer om, hvordan det er gjort.

## Funktioner

- **Kun brugernavn og password** — opsætning foregår helt gennem Home Assistants UI
  (Indstillinger → Enheder & tjenester), ingen YAML eller manuel konfiguration nødvendig.
- **Tre sensorer**: koldt vand (m³), varmt vand (m³), varme (kWh), alle med korrekt `device_class`
  og `state_class: total_increasing`, så de virker direkte i HA's Energidashboard.
- **Automatisk historik-import** ved første opsætning — henter så meget historik, som Brunata har
  tilgængeligt for den enkelte måler (typisk flere år tilbage), og lægger det ind som Home
  Assistant long-term statistics, så graferne er fyldt med det samme i stedet for at starte fra nul.
- **Timelig opdatering** af de aktuelle værdier efter opsætning (Brunata-målere rapporterer typisk
  ikke oftere end det alligevel).
- **Måned/dag-forbrugskort** (`custom:brunata-monthly-card`) med år-til-år-sammenligning pr. måned,
  klik-for-at-udvide dagligt forbrug, og en **"Sidste 30 dage"-oversigt** øverst for hver måler —
  summeret forbrug for de seneste 30 hele dage (dags dato selv tælles ikke med, da den dag endnu
  ikke er afsluttet), plus forskellen i forhold til samme periode sidste år. Tallene er beregnet
  til at kunne krydstjekkes direkte mod de tilsvarende "Sidste 30 dage"-kort på Brunatas egen
  portal. Se afsnittet ["Dashboard"](#dashboard) nedenfor for opsætning.

## Krav

- Home Assistant 2024.9 eller nyere (bruger `homeassistant.components.recorder.statistics` til
  historik-import, samt "sections"-visningsstrategien og `type: heading`-kortet til det
  automatisk oprettede dashboard — se ["Dashboard"](#dashboard) nedenfor).
- En aktiv Brunata Online-konto med adgang til `online.brunata.com`.
- Internetadgang fra din Home Assistant-installation (`iot_class: cloud_polling`).

## Installation

### Manuel installation (indtil HACS-understøttelse er på plads)

1. Kopiér hele mappen `custom_components/brunata/` fra dette repository ind i din Home
   Assistant-konfiguration, så den ender som:
   ```
   config/custom_components/brunata/
   ```
2. Genstart Home Assistant (Indstillinger → System → Genstart).
3. Gå til **Indstillinger → Enheder & tjenester → + Tilføj integration**, og søg efter
   **"Brunata Online"**.
4. Indtast dit brugernavn (email) og password til Brunata Online, og klik **Send**.

Integrationen validerer dit login med det samme og opretter herefter automatisk en enhed med de
tre sensorer, samt starter historik-importen i baggrunden.

### Via HACS

Ikke understøttet endnu — pakning til HACS (`hacs.json`, versionering, releases) er planlagt, men
ikke lavet endnu. Følg punktet ovenfor indtil videre.

### Opgraderer du fra den gamle AppDaemon-baserede version?

Den ældre, nu udfasede `appdaemon/`-baserede Brunata-app oprettede sine egne sensorer (typisk med
engelske navne som "Brunata Varme"/"Brunata Varmt vand"). Efter du har fjernet AppDaemon-appen og
er skiftet til denne integration, bliver disse gamle entiteter stående i Home Assistants
entitetsregister som **"Ugrupperet"** (ingen tilknyttet integration) — de modtager ikke længere
data og kan roligt slettes. Gå til **Indstillinger → Enheder og tjenester → Entiteter**, find de
gamle entiteter (ingen integration angivet), og fjern dem via **Fjern**-knappen. Dette påvirker
ikke de aktive entiteter fra denne integration (som står under enheden "Brunata" med et rigtigt
område tilknyttet), da de to er fuldstændig uafhængige registreringer.

## Konfiguration

Der er ingen yderligere konfiguration ud over brugernavn/password ved opsætning. Skal du opdatere
dit password senere (fx efter du selv har skiftet det på Brunatas hjemmeside), bruges
integrationens **Reconfigure**-funktion (klik på de tre prikker ud for integrationen under
Enheder & tjenester).

### Debug-eksport af rå målerhistorik

Ved den allerførste opsætning (den engangs-historiske backfill) skriver integrationen én JSON-fil
pr. aktiv måler til `config/brunata_debug/{meter_id}.json` i din Home Assistant-instans, med de RÅ
værdier Brunata returnerede — før nogen omregning, skalering eller reset-kompensation — så du kan
sammenligne dem direkte med Brunatas egen "Aflæsninger og målere"-side. **Denne mappe indeholder
personlige forbrugsdata og bør ikke committes til git eller deles** — den er allerede tilføjet til
`.gitignore`, men ligger uden for selve repoet (i din HA-instans' `config/`-mappe), så tjek selv at
den ikke havner i en backup eller et andet repo, du deler.

### Dashboard

Integrationen forsøger selv at oprette et "Brunata"-dashboard i sidebjælken ved første opsætning
(kun hvis det ikke allerede findes — den rører aldrig et eksisterende "brunata"-dashboard igen,
heller ikke hvis du selv har redigeret det). Da der ikke findes en officiel API til dette, skrives
dashboardet direkte til HA's interne lagerformat — det virker, men **dashboardet dukker først op i
sidebjælken efter din næste genstart af Home Assistant**, ikke øjeblikkeligt. Hvis det af en eller
anden grund fejler (fx efter en HA-opdatering der ændrer det interne format), får du en
persistent notification om det, og kan i stedet indsætte det manuelt:

**Automatisk oprydning ved afinstallation:** Fjerner du din sidste Brunata-konto (config entry) fra
Home Assistant, slettes "Brunata"-dashboardet automatisk igen — **også hvis du selv har redigeret
det manuelt** (tilføjet egne kort, ændret titlen osv.). Automatisk oprydning har med vilje højere
prioritet end at bevare manuelle tilpasninger her, så du ikke efterlades med et dødt dashboard, der
peger på en integration, der ikke længere er installeret. Har du **flere** Brunata-konti opsat,
bliver dashboardet stående indtil du fjerner den allersidste af dem.

**Opdaterer du fra en ældre version af integrationen?** Da dashboardet aldrig genskrives, når det
først findes (se ovenfor), beholder et allerede oprettet "Brunata"-dashboard sit gamle layout, selv
efter du opdaterer selve integrationen — herunder den ældre, flade `cards:`-struktur fra før
"sections"-visningen blev indført (se nedenfor), hvor titlen kunne ende i samme kolonne som en af
målersektionerne og gøre netop den kolonne højere end de andre to. For at få det nye layout skal du
enten slette det eksisterende "Brunata"-dashboard (Indstillinger → Dashboards → "⋮" → Slet) og
genindlæse integrationen (Indstillinger → Enheder og tjenester → Brunata → "⋮" → Genindlæs — **ikke**
fjern/geninstallér, det udløser en unødvendig fuld historik-genimport), eller blot indsætte den
nye YAML fra afsnittet nedenfor direkte i dit eksisterende dashboards YAML-redigering.

Opret et nyt dashboard (Indstillinger → Dashboards → Tilføj dashboard → Ny dashboard fra bunden),
skift til YAML-tilstand, og indsæt:

```yaml
title: Brunata
path: brunata
type: sections
max_columns: 3
sections:
  - type: grid
    column_span: 3
    cards:
      - type: heading
        heading: Forbrug
      - type: markdown
        content: "**Varme** måles i enheder · **Varmt/Koldt vand** måles i m³"
  - type: grid
    cards:
      - type: custom:brunata-monthly-card
        meter_type: heat
        show_title: false
  - type: grid
    cards:
      - type: custom:brunata-monthly-card
        meter_type: hot_water
        show_title: false
  - type: grid
    cards:
      - type: custom:brunata-monthly-card
        meter_type: cold_water
        show_title: false
```

Udelad de af de tre meter-sektioner, du ikke har en måler af typen for — husk samtidig at sænke
`max_columns` og `column_span` (øverst) med 1 for hver du udelader, så titlen fortsat spænder hele
bredden. Denne "sections"-baserede struktur (i stedet for en flad `cards`-liste) er bevidst valgt
frem for den ældre, klassiske masonry-visning: masonry balancerer kort i kolonner efter højde, hvad
der ellers får titlen til at blande sig ind i én tilfældig målerkolonne og gøre netop den kolonne
højere end de andre to — "sections" giver titlen sin egen, ægte fuld-bredde række for sig selv, så
de tre målersektioners bund altid flugter.

Hver måler får automatisk en **"Sidste 30 dage"**-boks øverst i sin egen kolonne, over
månedstabellen — et rullende 30-dages forbrugstal (i stedet for et kalendermånedstal) plus
forskellen til samme periode sidste år, ligesom Brunatas egen portals forside. Vandmålernes tal
vises her med 3 decimaler (i stedet for månedstabellens 2), så de er nemme at sammenligne 1:1 med
tallene på Brunatas portal.

## Kendte begrænsninger

- Dette er en **uofficiel, reverse-engineered integration**. Brunata kan til enhver tid ændre deres
  interne API uden varsel, hvilket kan få integrationen til at holde op med at virke.
- Kun testet mod private forbrugerkonti (`online.brunata.com`) — erhvervskonti eller andre
  Brunata-portaler (fx den tyske Brunata München-portal) er ikke understøttet.
- Varme-sensorens konvertering fra pulser til kWh er baseret på en `scale`-faktor hentet fra
  Brunatas eget API — se `docs/api-reference.md` for detaljer, hvis tallene skulle se forkerte ud
  for din opsætning.
- **Automatisk datavalidering af måler-aflæsninger** — se afsnittet
  ["Håndtering af huller/spring i datastrømmen"](#håndtering-af-hullerspring-i-datastrømmen)
  nedenfor.

### Håndtering af huller/spring i datastrømmen

Under udviklingen af denne integration observerede vi (den 13. juli 2026, via den
indbyggede debug-eksportfunktion) et konkret tilfælde, hvor Home Assistants egen
recorder fik en intern diskontinuitet i den langtidsstatistik, dashboard-kortet
normalt læser: den akkumulerede "sum" for én af varmtvandsmålerne faldt pludselig
og kraftigt fra én dag til den næste — selvom målerens rå aflæsning ("state") i
samme periode fortsatte helt normalt og problemfrit, uden noget faktisk fald.
Home Assistant markerede ikke selv dette som en registreret reset (feltet
`last_reset` var tomt). Dette er ikke en fejl i selve Brunata-integrationen, men en
begrænsning i hvordan HA's recorder i sjældne tilfælde kompilerer langtidsstatistik
— og noget der i princippet kan ske igen, af årsager der endnu ikke er set (interne
HA-artefakter, netværksfejl under indhentning, eller andre uforudsete
datakvalitetsproblemer).

For at gøre integrationen robust mod denne klasse af problemer, uanset årsag,
bygger måned/dag-visningen derfor ikke længere på HA's egen "sum"-kolonne, men
beregner selv forbruget ud fra målerens rå aflæsninger ("state") — kombineret med
en indbygget valideringsregel: da forbrugsmålere (bortset fra en kendt undtagelse,
se nedenfor) altid akkumulerer monotont opad, betragtes enhver ny aflæsning, der er
LAVERE end den senest kendte gyldige værdi for samme måler, automatisk som ugyldig
data — ikke som et reelt forbrugsfald. Når det sker, bruges den senest kendte
gyldige værdi i stedet for den ugyldige aflæsning. I praksis betyder det, at den
pågældende periode (dag eller måned) vises med **0 forbrug**, fremfor et misvisende
hul i grafen eller et forkert, negativt tal. Den næste, korrekte aflæsning
sammenlignes fortsat mod den rigtige, senest kendte gyldige værdi — ikke mod den
afviste — så der heller ikke opstår et kunstigt "hop" i forbruget bagefter.

**Dette er bevidst to adskilte mekanismer, der arbejder sammen:**

1. **Fysisk reset-kompensation** (kun varme-/radiatormålere): et stort fald i en
   varmemålers akkumulerede tæller kan være en ægte, forventet hændelse (fx ved
   udskiftning af måler) — det HAR målere af denne type lov til. Denne del af
   logikken registrerer et sådant fald og starter en ny akkumuleringscyklus fra
   den nye værdi, så det historiske forbrug bevares korrekt på tværs af resettet.
2. **Generel datavalidering** (alle måler-typer, inkl. vand): vandmålere (koldt og
   varmt vand) kan aldrig legitimt falde — et fald i deres data er derfor per
   definition en datafejl, aldrig et reelt fysisk reset, og bliver altid afvist
   efter reglen beskrevet ovenfor. For varmemåleren gælder begge mekanismer
   samtidigt: et stort, brat fald behandles som et ægte reset (mekanisme 1),
   mens en anden, uforklarlig datafejl af samme art som 13. juli-hændelsen
   stadig fanges og afvises af den generelle validering (mekanisme 2).

Afviste aflæsninger logges tydeligt i `home-assistant.log` med markøren
`[BRUNATA DATA VALIDATION]`, inklusive hvilken måler, hvilket tidspunkt, og hvilken
værdi der blev afvist. Hvis du oplever hyppige afvisninger for én bestemt måler,
er det værd at kigge nærmere på loggen — det kan indikere et reelt, tilbagevendende
problem (fx med selve måleren eller din netværksforbindelse), i modsætning til en
enkeltstående, forbigående hændelse som den her beskrevet.

## Udvikling

Se [`copilot-instructions-del2.md`](copilot-instructions-del2.md) for den fulde arkitekturplan, og
`docs/`-mappen for al reverse-engineering-dokumentation (login-flow, API-endpoints, bekræftede
grænser). Kildekoden til selve Brunata-klienten (login, datahentning, historik-chunking) ligger i
`src/brunata_client/` med et selvstændigt testsuite (`pytest tests/ -v`, kører fuldt offline mod
fixtures — ingen netværkskald).

## Ansvarsfraskrivelse

Dette projekt er ikke tilknyttet, godkendt af, eller understøttet af Brunata A/S. Brug på eget
ansvar. Login-flowet er reverse-engineered fra den offentligt tilgængelige webportal og kan ophøre
med at virke, hvis Brunata ændrer deres systemer.

## Versionshistorik

Version følger skemaet `1.XX`, hvor `XX` er det samlede antal større, afsluttede
ændringer/milepæle i projektets historie (se `custom_components/brunata/manifest.json`).

- **1.01** — Første Brunata Online Python-klient + AppDaemon-baseret HA-integration (offline
  `load_from_file`, første pytest-suite).
- **1.02** — Bekræftet, reverse-engineered login-flow (Keycloak/PKCE) og live
  `fetch_consumption_data` mod det rigtige API.
- **1.03** — Native Home Assistant-integration oprettet (`custom_components/brunata/`):
  config flow, sensorer, engangs-historik-import.
- **1.04** — Generalisering til vilkårligt antal målere pr. type, og korrekt kompensation for
  ægte fysiske varmemåler-resets.
- **1.05** — Måned/dag-forbrugskort (`brunata-monthly-card.js`) og WebSocket API med
  år-til-år-sammenligning.
- **1.06** — Automatisk "Brunata"-dashboard-oprettelse og -fjernelse ved installation/afinstallation.
- **1.07** — Permanent debug-eksport-service (`services.py`) til fejlsøgning af rå målerdata.
- **1.08** — Håndtering af det først observerede "hul i data" (13. juli-diskontinuiteten) i
  måned/dag-visningen.
- **1.09** — Generel, uafhængig datavaliderings-/selvhelbredende algoritme (monotoni-tjek,
  adskilt fra den fysiske reset-kompensation).
- **1.10** — Aktivitetslog: poll-succes/fejl vises nu i enhedens "Aktivitet"-fane.
- **1.11** — "Sidste 30 dage"-forbrugsoversigt med år-til-år-sammenligning.
- **1.12** — Dashboard-layout rettet til "sections"-visning (fuld-bredde titel, ensrettet
  sektionsbund) samt kort-æstetik.
- **1.13** — WebSocket-subscription til robusthed mod midlertidige forbindelsesfejl ved
  dashboard-åbning.
