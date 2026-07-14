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

## Krav

- Home Assistant 2024.x eller nyere (bruger `homeassistant.components.recorder.statistics` til
  historik-import).
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

Opret et nyt dashboard (Indstillinger → Dashboards → Tilføj dashboard → Ny dashboard fra bunden),
skift til YAML-tilstand, og indsæt:

```yaml
title: Brunata
path: brunata
cards:
  - type: markdown
    content: "# Forbrug"
  - type: markdown
    content: "**Varme** måles i enheder · **Varmt/Koldt vand** måles i m³"
  - type: custom:brunata-monthly-card
    meter_type: heat
    show_title: false
  - type: custom:brunata-monthly-card
    meter_type: hot_water
    show_title: false
  - type: custom:brunata-monthly-card
    meter_type: cold_water
    show_title: false
```

Udelad de af de tre `custom:brunata-monthly-card`-blokke, du ikke har en måler af typen for.

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
