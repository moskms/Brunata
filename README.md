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

## Konfiguration

Der er ingen yderligere konfiguration ud over brugernavn/password ved opsætning. Skal du opdatere
dit password senere (fx efter du selv har skiftet det på Brunatas hjemmeside), bruges
integrationens **Reconfigure**-funktion (klik på de tre prikker ud for integrationen under
Enheder & tjenester).

## Kendte begrænsninger

- Dette er en **uofficiel, reverse-engineered integration**. Brunata kan til enhver tid ændre deres
  interne API uden varsel, hvilket kan få integrationen til at holde op med at virke.
- Kun testet mod private forbrugerkonti (`online.brunata.com`) — erhvervskonti eller andre
  Brunata-portaler (fx den tyske Brunata München-portal) er ikke understøttet.
- Varme-sensorens konvertering fra pulser til kWh er baseret på en `scale`-faktor hentet fra
  Brunatas eget API — se `docs/api-reference.md` for detaljer, hvis tallene skulle se forkerte ud
  for din opsætning.

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
