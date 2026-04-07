# Beckhoff ADS Multi-PLC – Home Assistant Custom Integration

Integraatio mahdollistaa useiden Beckhoff TwinCAT PLC -logiikoiden liittämisen
Home Assistantiin samanaikaisesti. Jokainen PLC on oma laitekokonaisuutensa
omine entiteetteineen.

## Vaatimukset

- Home Assistant 2023.1+
- `pyads >= 3.3.9` (asennetaan automaattisesti)
- TwinCAT ADS reitti konfiguroitu PLC:llä ja HA-palvelimella
- ADS-portit auki palomuurissa (oletuksena TCP 48898)

## Asennus

1. Kopioi `custom_components/ads_multi/` hakemistoon
   `<config>/custom_components/ads_multi/`
2. Käynnistä Home Assistant uudelleen
3. Mene **Asetukset → Integraatiot → + Lisää integraatio**
4. Hae "Beckhoff ADS"
5. Lisää jokainen PLC erikseen omana integraationa

## Konfigurointi

### PLC:n yhteystiedot

| Kenttä | Esimerkki | Kuvaus |
|--------|-----------|--------|
| PLC:n nimi | `Lämmitys PLC` | Näyttönimi HA:ssa |
| AMS Net ID | `192.168.1.100.1.1` | Löytyy TwinCAT System Managerista |
| IP-osoite | `192.168.1.100` | PLC:n IP-osoite |
| ADS-portti | `851` | Oletuksena 851 (TwinCAT 3) |

### Muuttujien konfigurointi

Muuttujat lisätään config flowin toisessa vaiheessa tai jälkikäteen
integraation asetusten kautta.

| Kenttä | Pakollinen | Kuvaus |
|--------|-----------|--------|
| Nimi | Kyllä | PLC-muuttujan täydellinen nimi, esim. `MAIN.rLampotila` |
| Tyyppi | Kyllä | `BOOL`, `INT`, `DINT`, `REAL`, `LREAL`, `STRING` jne. |
| Näyttönimi | Ei | Luettava nimi HA:ssa |
| Yksikkö | Ei | `°C`, `kW`, `bar` jne. |
| Device class | Ei | `temperature`, `power`, `pressure` jne. |
| Kirjoitettava | Ei | `true` → switch tai number-entiteetti |

### Esimerkki muuttujista

```
MAIN.rUlkolampotila   REAL    read-only → sensor
MAIN.rHuonelampotila  REAL    read-only → sensor
MAIN.bLampoPaalla     BOOL    read-only → binary_sensor
MAIN.bPumppiOhjaus    BOOL    writable  → switch
MAIN.rLampoAsetusarvo REAL    writable  → number
```

## Entiteettityypit

| PLC-tyyppi | Kirjoitettava | HA-entiteetti |
|-----------|--------------|---------------|
| BOOL | Ei | `binary_sensor` |
| BOOL | Kyllä | `switch` |
| INT/DINT/REAL/LREAL/BYTE/WORD/DWORD | Ei | `sensor` |
| INT/DINT/REAL/LREAL/BYTE/WORD/DWORD | Kyllä | `number` |
| STRING | Ei | `sensor` |

## ADS-reitin konfigurointi

PLC:llä (TwinCAT System Manager → Routes) täytyy lisätä reitti
Home Assistant -palvelimen IP-osoitteelle. Palvelimen AMS Net ID
muodostetaan automaattisesti muodossa `<IP>.1.1`.

## Päivitysväli

Oletuspäivitysväli on 30 sekuntia. Muuttaaksesi sitä, muokkaa
`const.py`-tiedoston `DEFAULT_UPDATE_INTERVAL`-arvoa.

## Virheenetsintä

Ota debug-loggaus käyttöön `configuration.yaml`:ssa:

```yaml
logger:
  default: warning
  logs:
    custom_components.ads_multi: debug
```
