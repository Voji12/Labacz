# DU01 — LLM a volání nástrojů

## Zadání

Napiš Python skript, který zavolá LLM API, použije nástroj (např. výpočetní funkci) a vrátí odpověď zpět LLM.

## Co příklad demonstruje

Python komunikuje s modelem na Runpod Serverless přes HTTP. Model dostane otázku a popisy dvou nástrojů: `add` (součet) a `multiply` (součin). Při `tool_choice="auto"` sám rozhodne, zda a který nástroj použije. Python nevykonává libovolný kód od modelu: může zavolat jen funkce z `available_functions`.

1. Python pošle strukturované `messages`, definice `tools` a `tool_choice="auto"`.
2. LLM vrátí zprávu `assistant` s `tool_calls`, názvem funkce, argumenty a ID volání.
3. Python vybere odpovídající funkci a provede výpočet.
4. Do historie vloží původní zprávu asistenta a výsledek jako `role="tool"`. Pole `tool_call_id` propojí výsledek s původním voláním.
5. Celou historii pošle znovu LLM, které výsledek převede do přirozeného jazyka.

Jde o nativní tool calling API, nikoli o simulaci pomocí vlastního JSONu v textovém promptu. Samotné Python funkce běží lokálně, model běží na Runpodu. Smyčka dovoluje nejvýše pět volání modelu. Pokud model rovnou odpoví bez nástroje, skript to v logu ukáže; výběr je ponechán modelu.

Výchozí otázka je **„Kolik je 123 + 456?“**. Ověřený průchod vybral `add`, Python vrátil `579` a model odpověděl **„123 + 456 je 579.“** Otázku lze změnit v seznamu `messages` ve funkci `main()`.

## Obsah adresáře

| Soubor | Účel |
| --- | --- |
| `main.py` | Kompletní příklad, obsluha Runpod API, Python nástroje a logování |
| `.env` | Lokální ID endpointu a API klíč; nepatří do Gitu |
| `.env.example` | Šablona konfigurace bez přihlašovacích údajů |
| `pyproject.toml`, `uv.lock` | Závislosti a jejich uzamčené verze |
| `.python-version` | Požadovaný Python 3.12 |
| `logs/` | Automaticky uložené logy jednotlivých běhů; nepatří do Gitu |

## Instalace a spuštění

V terminálu přejdi do tohoto adresáře `DU01`. Je potřeba Python 3.12 nebo novější a nástroj `uv`.

```bash
uv sync
```

Soubor `.env` není součástí repozitáře. Vytvoř konfiguraci ze šablony a doplň vlastní hodnoty:

```bash
cp .env.example .env
```

```dotenv
RUNPOD_ENDPOINT_ID=YOUR_ENDPOINT_ID
RUNPOD_API_KEY=YOUR_RUNPOD_API_KEY
```

Pak spusť:

```bash
uv run main.py
```

Alternativně po instalaci závislostí:

```bash
.venv/bin/python main.py
```

Ve VS Code vyber **Python: Select Interpreter** a Python z `DU01/.venv/bin/python`. Systémový Python může skončit chybou `No module named 'requests'`, protože má jiné nainstalované knihovny. `.env` se načítá podle umístění skriptu, takže nezávisí na aktuálním adresáři terminálu.

## Runpod endpoint

Endpoint musí podporovat OpenAI kompatibilní `/v1/chat/completions` s nativním tool callingem a automatickým výběrem nástroje. Příklad používá Runpod obálku `input.openai_route` a `input.openai_input` a čte odpověď z `output[0].choices[0].message`.

Lokálně byl ověřen model `QuantTrio/Qwen3-VL-30B-A3B-Instruct-AWQ`. Jiný typ Runpod workeru může mít jiný vstup a výstup. Samotné ID endpointu proto nestačí k zaměnitelnosti různých workerů.

Maximum workerů musí být alespoň 1. Minimum může zůstat 0; první požadavek pak může čekat na spuštění a načtení modelu. Požadavky využívají placený Runpod výpočet. Skript čeká na HTTP odpověď nejvýše 300 sekund a při rozpracované úloze následně kontroluje její stav po dvou sekundách dalších nejvýše 300 sekund. Lokální timeout nebo přerušení skriptu automaticky neruší vzdálenou úlohu.

## Logování a debugging

Každá událost se vypíše do stdout a zároveň uloží do pole `log_events`. Záznam je snímek dat v daném okamžiku: další úpravy historie konverzace starší záznam nepřepíšou.

Na konci běhu, i při zachycené chybě nebo přerušení přes Ctrl+C, vzniknou dva soubory se společným názvem obsahujícím datum, čas a krátké unikátní ID:

```text
logs/20261022-183746-2b6d9252.log
logs/20261022-183746-2b6d9252.json
```

- **`.log`** obsahuje shrnutí a čitelnou časovou osu.
- **`.json`** obsahuje objekty `summary` a `events` pro další zpracování.

Každá událost má pořadí, čas včetně časové zóny, čas od začátku běhu, úroveň, číslo kroku, zprávu a data. Shrnutí obsahuje stav `SUCCESS`, `ERROR` nebo `INTERRUPTED`, délku běhu, verzi Pythonu, ID úloh a workerů, model, čekání ve frontě, dobu inference, tokeny a volání Python funkcí s argumenty a výsledky. Chyby obsahují i traceback.

API klíč se v ukládaných a vypisovaných událostech maskuje. Logy ale obsahují celé otázky, historii a odpovědi modelu, proto před jejich sdílením zkontroluj obsah. `.env`, `.venv/` a `logs/` jsou v `.gitignore`.

### Jak poznat správný průchod

V logu postupně najdi:

```text
KROK 1/5 — volání modelu (tool_choice=auto)
LLM vybralo nástroje — skutečné API tool_calls
Python spouští add
Výsledek Python funkce add
Výsledek vracím LLM jako skutečnou zprávu role=tool
KROK 2/5 — volání modelu (tool_choice=auto)
HOTOVO — lidsky čitelná odpověď LLM
Konec běhu
```

Pokud se neobjeví `tool_calls`, model nástroj nevybral. Pokud odpověď nemá ani `content`, ani `tool_calls`, zkontroluj nastavení parserů tool callingu a reasoning na endpointu. HTTP chyby a odpověď serveru jsou zaznamenány v logu.

## Veřejné ukázky logů

Adresář `example-logs/` obsahuje dva skutečné úspěšné běhy s anonymizovanými identifikátory endpointu, úloh, workeru a tool callů. Náhradní ID zůstávají konzistentní, takže lze sledovat vazbu `tool_calls` → `tool_call_id`. API klíč ve zdrojových logách nalezen nebyl. Otázky, odpovědi, časování, model a tokeny zůstaly zachované.

Nové lokální logy v `logs/` se automaticky nezveřejňují. Před sdílením je potřeba zkontrolovat také obsah promptů a odpovědí; maskování API klíče samo o sobě neanonymizuje celý log.
