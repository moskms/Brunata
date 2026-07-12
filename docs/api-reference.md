# Brunata Online — Reverse-engineered API (fra HAR-capture 2026-07-12)

Alle endpoints kræver en gyldig session fra login-flowet (se `login-flow-todo.md` — dette punkt
er IKKE færdigt afklaret endnu). Base-URL: `https://online.brunata.com/online-webservice/v2/rest`.

Ingen `Authorization: Bearer ...`-header blev observeret på disse kald i capturet — det tyder på at
sessionen holdes via cookies sat under OIDC-redirecten, ikke via en header, klienten selv skal sætte.
**Skal bekræftes** med den nye login-capture, inkl. hvilke(t) cookie-navn(e) der reelt bærer sessionen.

## GET /consumer
Stamdata for boligen/forbrugsstedet.
```json
{
  "buildingNo": 35199,
  "consumerName": "<navn>",
  "brunataBranchNo": "10",
  "location": {
    "propertyNo": "00269",
    "street": "<vejnavn>",
    "postalCode": "2200",
    "city": "København N",
    "address": "<fuld adresse>"
  }
}
```

## GET /user
Login-brugerens profil (`consumerId` bruges til at koble bruger ↔ forbrugssted).

## GET /consumer/meteroverview
**Nutidsoversigt — bruges til de tre live-sensorer.**
```json
[
  {
    "meterId": 8260600,
    "alloUnitType": "K",
    "unit": 8,
    "meterValue": 171.239,
    "telegramDate": "2026-07-12T19:42:00+02:00",
    "consumptionLast30Days": 1.842,
    "consumptionPriorYearSamePeriod": 1.794,
    "placement": "Entre",
    "meterNo": "60768439",
    "decimals": 3
  }
]
```
`alloUnitType`: `K` = koldt vand, `W` = varmt vand, `O` = varme. `unit`: `8` = m³, `1` = enheder/kWh
(kombineres med `decimals` for korrekt visning).

**VIGTIGT:** dette endpoint indeholder IKKE en `scale`-faktor. For varme (`O`, `unit: 1`, "enheder"/pulser)
skal `meterValue` ganges med en skaleringsfaktor for at give kWh — men den faktor findes IKKE her.
Den findes i stedet i `meter.scale` i `/consumer/consumption`-svaret (se nedenfor). Bekræftet i HAR:
meterId 16783917 (varmemåler) har `"scale": 2.217` i consumption-svaret. `heat_kwh = meterValue × scale`.
Vand-målerne (`K`/`W`, `unit: 8`, m³) har ikke brug for denne skalering — de er allerede i m³.

## GET /consumer/metersforconsumer
Stamdata for ALLE målere consumeren nogensinde har haft, inkl. afmonterede (`dismountedDate` sat).
Bruges til at bygge en komplet liste af `meterId`'er, man kan hente historik for.

**Indeholder `transmitting` (bool) pr. `meterId`** — dette felt findes IKKE i `/consumer/meteroverview`.
Hvis `MeterReading.transmitting` skal udfyldes med en ægte værdi (ikke en antagelse), slå den op her
og match på `meterId`. Bekræftet i HAR: alle 4 målere (inkl. én afmonteret) havde `transmitting: true`
i det ene datapunkt vi har — feltets adfærd ved fx en fejlende/offline måler er IKKE bekræftet endnu.

## GET /consumer/consumption
**Historik-endpointet — dette er nøglen til "data fra tidligere perioder".**

Query-parametre:
- `startdate`, `enddate` — ISO 8601 med tidszone, fx `2026-06-12T00:00:00.000+02:00`
- `interval` — observeret: `D` (dag). Andre værdier (`M`=måned, `Y`=år?) er ikke bekræftet endnu —
  test dette eksplicit i Del 2, gæt ikke.
- `allocationunit` — `K`, `W`, eller `O` (ét kald pr. målertype, ikke samlet)

```json
{
  "startDate": "2026-06-12T00:00:00+02:00",
  "endDate": "2026-07-12T00:00:00+02:00",
  "interval": "D",
  "consumptionLines": [
    {
      "meter": { "meterId": 8260600, "placement": "Entre", "allocationUnit": "K", "unit": "8" },
      "consumptionValues": [
        { "fromDate": "2026-06-12T00:00:00+02:00", "toDate": "2026-06-13T00:00:00+02:00", "consumption": 0.060 }
      ]
    }
  ]
}
```

**Ukendt endnu:** hvor langt tilbage `startdate` kan sættes (jeg har kun set ~1 måned i capturet).
Test i Del 2 med fx `startdate` 12/24 måneder tilbage og se om Brunata svarer med data eller en fejl/tomt array.

## GET /consumer/meters/{meterId}/metervalues
Rå tidsserie for én måler (bruges til detaljeret graf, ikke nødvendig for MVP-sensorerne).
```json
{
  "meterValues": [
    { "readingDate": "2026-07-12T19:42:00+02:00", "value": 171.239, "unit": 8 }
  ],
  "limited": false
}
```

**Bekræftet grænse (HAR-capture med bredt datointerval):** dette endpoint har INGEN paginering
(intet `page`/`offset`-parameter). Hvis det angivne `startdate`–`enddate`-interval indeholder mere
end **600 datapunkter**, returnerer API'et kun de **600 nyeste**, sorteret nyest-først, og sætter
`"limited": true`. De ældste punkter i intervallet udelades simpelthen — der er ingen måde at hente
"næste side" af de resterende punkter for det samme kald.

**Bekræftet for alle tre målertyper** (varme `16783917`, koldt vand `8260600`, varmt vand `8260593`)
— samme opførsel, samme grænse, ingen forskel mellem målertyper.

**mountingDate pr. måler (bekræftet):**
- Varme (`16783917`): 2025-03-25 (~16 måneders historik tilgængelig)
- Koldt vand (`8260600`) og varmt vand (`8260593`): 2019-09-18 (~7 års historik tilgængelig)

**Vigtig indsigt om datatæthed:** ældre perioder har MEGET færre datapunkter end nyere. Fx gav et
2-3 måneders vindue fra 2019/2020 kun 2-15 aflæsninger, mens den seneste uge (juli 2026) gav 49-52
aflæsninger. En fast bid-størrelse på "3-4 uger" er derfor unødvendigt lille for de ældste år (spild
af kald), men passende for den seneste periode. Overvej en adaptiv strategi: start med et stort
interval (fx et helt år) for ældre perioder, og kun formindsk intervallet, hvis `limited: true`
rent faktisk kommer tilbage — i stedet for altid at bruge samme faste bidstørrelse.

**Konsekvens for historik-import:** hent ALDRIG et helt måler-livsforløb i ét kald. Start med et
stort interval (fx 12 måneder), tjek `limited` i svaret, og hvis `true`: halvér intervallet og prøv
igen, indtil `limited: false`. Ryk derefter bidden fremad i tid, indtil hele perioden fra
`mountingDate` til nu er dækket. Denne "halvér ved behov"-strategi er mere effektiv end en fast
bidstørrelse, i lyset af hvor ujævnt fordelt datapunkterne er over tid.


## GET /consumer/superallocationunits
Grupperer allocationUnits: `{"superAllocationUnit": 1, "allocationUnits": ["O"]}` (varme),
`{"superAllocationUnit": 2, "allocationUnits": ["W", "K"]}` (vand). Nyttig til at gruppere
sensorer under "varme" vs. "vand" i UI, men ikke kritisk for MVP.

---

## Åbne spørgsmål til Del 2 (må IKKE gættes, skal testes/bekræftes)

1. **Sessions-mekanisme**: cookie-baseret eller er der en header, der ikke blev fanget i capturet?
   → Afvent ny login-capture.
2. **Login-issuer er `online.brunata.com/iam/realms/online-prod`** (Keycloak-mønster), IKKE
   `brunatab2cprod.b2clogin.com` (Azure B2C) som `client.py` i øjeblikket antager. Login-koden fra
   Del 1 skal sandsynligvis omskrives til en Keycloak-kompatibel flow (typisk: GET til
   `/iam/realms/online-prod/protocol/openid-connect/auth` med PKCE-parametre → login-form POST til
   samme realm → redirect med `code` → token-exchange, som allerede er set i capturet).
3. **`interval`-parameterens gyldige værdier** for `/consumption` (kun `D` bekræftet).
4. **Hvor langt tilbage historik kan hentes** — test grænsen empirisk.
