# Brunata Online — Login-flow (bekræftet fra HAR-capture 2026-07-12, frisk inkognito-login)

Dette erstatter og annullerer alle tidligere antagelser om Azure AD B2C (`brunatab2cprod.b2clogin.com`)
i `client.py`. Login foregår mod en **Keycloak-instans**, realm `online-prod`, hostet på
`online.brunata.com/iam/`.

## Bekræftet flow (Authorization Code + PKCE)

```
1. GET  https://online.brunata.com/online-auth-webservice/v1/rest/authorize
        ?client_id=82770188-c92e-4d16-927d-a15c472eda55
        &redirect_uri=https://online.brunata.com/auth-redirect
        &scope=openid+profile+email
        &response_type=code
        &code_challenge=<PKCE S256 challenge, base64url(sha256(code_verifier))>
        &code_challenge_method=S256
   → 307 redirect (Location-header giver næste URL, følg den — indeholder samme query-params)

2. GET  https://online.brunata.com/iam/realms/online-prod/protocol/openid-connect/auth?<samme params>
   → 200, HTML-loginside.
   PARSE denne HTML for <form ... action="...">  — actionen indeholder dynamiske,
   engangs-værdier: session_code, execution, tab_id, client_data. De skal bruges PRÆCIS som de står;
   kan IKKE genbruges på tværs af sessioner eller konstrueres selv.

3. POST <form-action fra trin 2, dvs. .../login-actions/authenticate?session_code=...&execution=...
        &client_id=82770188-c92e-4d16-927d-a15c472eda55&tab_id=...&client_data=...>
   Content-Type: application/x-www-form-urlencoded
   Body: username=<email>&password=<password>&credentialId=
   → 302 redirect ved korrekt login. Location-header:
     https://online.brunata.com/auth-redirect?session_state=...&iss=.../iam/realms/online-prod&code=<code>
   → Ved forkert login: 200 med loginsiden igen og en fejlbesked i HTML'en (skal håndteres som
     BrunataLoginError, ikke antages at være en exception/statuskode).

4. GET  <Location fra trin 3> (auth-redirect med ?code=...)
   → 200. `code`-query-parametren her er authorization code'n til trin 5.

5. POST https://online.brunata.com/online-auth-webservice/v1/rest/oauth/token
   Content-Type: application/x-www-form-urlencoded
   Body: client_id=...&redirect_uri=https://online.brunata.com/auth-redirect
         &scope=openid+profile+email&code=<code fra trin 4>
         &grant_type=authorization_code&code_verifier=<PKCE code_verifier fra trin 1>
   → 200, JSON med access_token/refresh_token/expires_in (dette trin var allerede korrekt
     implementeret i client.py — bevar denne del).
```

## Kritisk implementeringsdetalje: brug ÉN vedvarende HTTP-session

Der er **ingen synlige cookies eller Authorization-headers** i HAR-filen — det er fordi Chrome
fjerner disse værdier fra eksporterede HAR-filer af sikkerhedshensyn, IKKE fordi der ingen session
er. Implementér derfor login-kæden med ét enkelt `httpx.Client()`/`httpx.AsyncClient()`-objekt med
`follow_redirects` styret manuelt trin-for-trin (så vi kan læse `code` ud undervejs) og med
**cookie-jar aktiveret og delt på tværs af alle 5 trin ovenfor og alle efterfølgende API-kald**.

Konkret: opret ÉT client-objekt i `BrunataClient.__init__`, brug det til trin 1-5 i `login()`, og
genbrug SAMME objekt (ikke et nyt) til `fetch_consumption_data()` og alle andre kald. Antag ikke at
en Bearer-token i en Authorization-header er nødvendig — test uden først. Hvis API-kald fejler med
401 selvom cookien er der, forsøg dernæst at tilføje `Authorization: Bearer <access_token>` fra
trin 5 som fallback.

## Hvad der SKAL testes empirisk i Del 2 (ikke antages)

- Om `execution`/`session_code`/`tab_id` udløber hurtigt (typisk Keycloak-default er få minutter) —
  login-kæden bør derfor gennemføres uden unødige forsinkelser mellem trin 2 og 3.
- Om et 401 på et senere API-kald skyldes udløbet cookie-session eller udløbet access_token, og om
  `refresh_token`-grant (`grant_type=refresh_token` mod samme token-endpoint) er nok, eller om et
  helt nyt login (trin 1-5) er nødvendigt.
