# Brunata Online — Del 3 (2026-07-14 08:00)

Generalisering af målerhåndtering til vilkårlige ejendomme, samt månedlig/daglig forbrugsvisning
med år-til-år-sammenligning.

## Del 3a — Generalisér til vilkårligt antal målere (skal laves FØRST)

### Baggrund / problem

Den nuværende implementering (fra Del 2) antager præcis 3 faste målere — én pr. allocationUnit
(K/W/O), baseret på `ALLOCATION_UNIT_SLUGS`/`NAMES` i `const.py`. Det virker for brugerens egen
ejendom (kun 1 radiator), men er ikke korrekt generelt: en ejendom kan have flere målere af samme
type (fx "Varme Stue" og "Varme Værelse"), og integrationen skal virke for enhver Brunata-konto,
ikke kun brugerens egen.

### Krav

1. Ved opsætning (og ved hver almindelig opdatering) hentes `/consumer/metersforconsumer` for at få
   den fulde, faktiske liste af målere på kontoen.
2. Filtrér til kun **aktive** målere (`dismountedDate` er `null`/ikke sat).
3. Opret **én sensor-entity pr. fysisk måler** (matchet på `meterId`), ikke pr. allocationUnit:
   - `unique_id`: baseret på `meterId` (fx `f"{entry_id}_{meter_id}"`), ALDRIG på allocationUnit
     eller placement — de kan begge ændre sig eller være ens for flere målere, men `meterId` er
     stabilt og unikt.
   - Sensorens `name`: `f"{ALLOCATION_UNIT_NAMES[allocationUnit]} {placement}"`, fx
     `"Varme Stue"`, `"Koldt vand Entré"`. Hvis flere målere har samme allocationUnit+placement
     (usandsynligt, men kan ske), tilføj målerens `meterNo` i parentes for at undgå dubletter.
   - `device_class`/`state_class`/`unit` afhænger stadig kun af `allocationUnit`, som hidtil.
4. `coordinator.py`s interne datastruktur skal nøgles på `meter_id`, ikke på allocationUnit-type.
   `/consumer/meteroverview` returnerer allerede data pr. `meterId` — brug det direkte til at
   opdatere de rigtige sensorer efter `meterId`-match, i stedet for en fast K/W/O-mapping.
5. `history.py`s eksisterende funktioner tager allerede `meter_id` som parameter — ingen ændring
   nødvendig der, kald dem bare for hver aktiv måler fra listen i stedet for 3 hardcodede ID'er.
6. Varme-sensorens `scale`-cache (fra Del 2) skal nøgles på `meter_id`, så flere varmemålere med
   forskellige skaleringsfaktorer håndteres korrekt hver for sig.

### Konsekvens der skal kommunikeres til brugeren

Denne ændring skifter `unique_id`-skema, hvilket får HA til at betragte de nuværende sensorer som
"væk" og oprette nye. Det betyder, at den allerede importerede historik (fra Del 2) ikke automatisk
følger med til de nye entities — brugeren skal fjerne og gentilføje integrationen (eller acceptere
at historikken importeres igen under de nye entity-ID'er). Dette er acceptabelt nu i udviklingsfasen,
men bør IKKE ske igen efter en "rigtig" udgivelse — så dette er den sidste gang, vi tillader os at
ændre `unique_id`-skemaet uden en migrationssti.

### Definition of done for Del 3a

- [ ] Fjern testet mod en konto med kun 1 måler pr. type (brugerens egen) — resultatet skal se
      identisk ud som før (bortset fra evt. nye "Placering" i sensornavnet).
- [ ] Kodegennemgang bekræfter, at INGEN sted i `sensor.py`/`coordinator.py` antager et fast antal
      af 3 sensorer eller en fast K/W/O-liste — alt skal udledes af den hentede målerliste.
- [ ] `pytest tests/ -v` og `ast.parse`-tjek af alle `custom_components/brunata/`-filer er grønt.

---

## Del 3b — Månedlig forbrugsoversigt med år-til-år-sammenligning

### Ønsket visning (fra brugeren)

Tre lodrette søjler i UI'et: **Varme** / **Koldt vand** / **Varmt vand** (én søjle pr. aktiv måler
efter Del 3a — hvis en ejendom har 2 varmemålere, bliver det 2 varme-søjler, ikke slået sammen).
Hver søjle viser måned for måned, nyeste øverst:

```
Januar       3,24 m³     12%
Februar      2,12 m³      1%
```

Klik på en måned → udvider til dagsforbrug for netop den måned.

### Datakilde: HA's egen langtidsstatistik, IKKE nye Brunata-kald

Al nødvendig data ligger allerede i HA's `recorder`-database fra Del 2's historik-backfill (rå,
cumulative målerværdier importeret som statistics). Brug
`homeassistant.components.recorder.statistics.statistics_during_period()` til at aggregere:

- Månedsforbrug: `sum`/differens for `period="month"`.
- Dagsforbrug (ved udfoldning): samme funktion med `period="day"`, afgrænset til den valgte måned.
- År-til-år-%: hent samme måned sidste år på samme måde, beregn
  `(dette_år - sidste_år) / sidste_år * 100`. Hvis sidste års data ikke findes (måleren er for ny —
  fx brugerens varmemåler, monteret marts 2025), vis "—" i stedet for et %-tal. Gæt eller
  ekstrapolér IKKE en værdi, når data mangler.

Implementér dette som nye **WebSocket API-kommandoer** i integrationen (standard HA-mønster for
frontend↔backend-kommunikation ud over almindelige entity-states):

- `brunata/monthly_summary` — input: `meter_id`. Output: liste af `{year, month, consumption,
  yoy_percent | null}` for de seneste 12-24 måneder (så meget som findes).
- `brunata/daily_breakdown` — input: `meter_id`, `year`, `month`. Output: liste af
  `{day, consumption}` for hver dag i den måned.

### Frontend: ét custom Lovelace-kort

Byg ét nyt custom card (JS/TS, leveres med integrationen som en frontend-ressource, registreres
via `frontend.add_extra_js_url` eller HA's `www`/`hacs`-frontend-mønster). Kortet:

1. Ved indlæsning: kald `brunata/monthly_summary` for hver aktiv måler (fra `hass.states` eller en
   dedikeret "liste målere"-kommando), og tegn én lodret søjle pr. måler, grupperet visuelt efter
   allocationUnit (Varme / Koldt vand / Varmt vand) hvis der er flere af samme type.
2. Hver månedslinje er klikbar. Ved klik: kald `brunata/daily_breakdown` for den måned, og fold en
   lille dagsliste ud under linjen (ingen sideskift, ingen ny dialog — inline-udfoldning).
3. %-tallet farves (fx grønt ved fald, rødt ved stigning) — almindelig konvention for forbrugsdata,
   men afklar med brugeren om det ønskes, før det implementeres, i stedet for at antage det.

### Definition of done for Del 3b

- [ ] `brunata/monthly_summary` og `brunata/daily_breakdown` virker og er dækket af mindst én test
      med en fake/mock recorder-statistik (offline, ingen ægte HA-instans krævet for selve testen
      af aggregeringslogikken — men den fulde visning skal stadig testes manuelt i en rigtig HA,
      ligesom Del 2).
- [ ] Kortet virker for en måler uden sidste-års-data (fx varme) — viser "—", ikke en fejl eller et
      forkert 0%/negativt tal.
- [ ] Manuelt testet i brugerens rigtige HA-instans efter Del 3a er på plads.
