# Referenzwerte erneuern

`tests/fixtures/parity_cases.json` enthält Ergebnisse, die von der
**TypeScript-Implementierung der Karte** erzeugt wurden. `tests/test_parity.py`
prüft die Python-Portierung dagegen — das ist der Nachweis, dass Anzeige und
Automatik dieselben Zahlen liefern.

Erneuern ist nur nötig, wenn sich die Formel in der Karte ändert:

```bash
# Aus dem Kartenprojekt heraus ausführen
cd ../EnergyManagerCard
mkdir -p test/_gen
cp ../EnergyManagerIntegration/tools/gen_parity_cases.ts test/_gen/gen-cases.test.ts
sed -i '' "s|'\.\./\.\./\.\./\.\./\.\./Users/koehler/Documents/Privat/HA-Addons/EnergyManagerCard/src/|'../../src/|g" \
  test/_gen/gen-cases.test.ts

CASES_OUT=../EnergyManagerIntegration/tests/fixtures/parity_cases.json \
  npx vitest run test/_gen/gen-cases.test.ts

rm -rf test/_gen        # gehört nicht ins Kartenprojekt
```

Danach im Integrationsprojekt:

```bash
.venv/bin/python -m pytest tests/test_parity.py -q
```

Schlägt etwas fehl, ist entweder die Portierung fehlerhaft **oder** die
Kartenformel hat sich bewusst geändert. Im zweiten Fall gehört die Änderung
auch nach `surplus.py` — und in `docs/integration-contract.md` der Karte.

Das Skript ist bewusst eine `.test.ts`-Datei: so lässt es sich ohne
zusätzlichen TypeScript-Runner über das vorhandene vitest ausführen.
