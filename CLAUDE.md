# Brunata Online — HA-integration

Dette repository bygger en Home Assistant-integration, der henter vand- og varmedata fra
Brunata Online (online.brunata.com) til en privat bruger, med en pæn UI-opsætning
(kun brugernavn/password) og overskuelig visning i HA's eget frontend.

## Læs disse filer FØRST, i denne rækkefølge, før du skriver noget kode

1. `copilot-instructions-del2.md` — den fulde arkitekturplan og trin-for-trin-opgaver for denne fase.
2. `docs/login-flow.md` — bekræftet, reverse-engineered login-flow (Keycloak, PKCE). Login går IKKE
   via Azure AD B2C, uanset hvad ældre kommentarer i `client.py` måtte antyde.
3. `docs/api-reference.md` — bekræftede data-endpoints (forbrug, målere, historik) med rigtige
   JSON-eksempler.

## Nuværende status (kort)

- `src/brunata_client/` — Python-klient. Token-udveksling (sidste trin af login) er allerede
  korrekt implementeret. `fetch_consumption_data()` er IKKE implementeret endnu.
- `appdaemon/` — ældre spor, der IKKE skal videreudvikles. Native `custom_components/brunata/`
  (endnu ikke oprettet) er vejen frem, jf. `copilot-instructions-del2.md`.
- Alle 10 eksisterende tests i `tests/` er grønne — hold dem sådan, og udvid dem i stedet for at
  slette dem.

## Vigtige spilleregler

- Gæt ALDRIG på API-endpoints, HTTP-headers, eller sessionsmekanismer, der ikke er dokumenteret i
  `docs/`. Hvis noget er ukendt (markeret som "skal testes empirisk" i docs), så sig det til
  brugeren og foreslå en konkret test, i stedet for at antage noget.
- Credentials (brugernavn/password) må aldrig havne i logs, tests, eller committed filer.
- Kør `pytest tests/ -v` efter enhver ændring i `src/brunata_client/` for at sikre intet knækker.
