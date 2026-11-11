# Fortsätt här

**Viktigt:** Kör `bash fortsatt.sh` i den här mappen. Starta sedan Claude Code här och
skriv: **Läs FORTSATT.md och gör uppgifterna under "Till Claude Code".**

## Läget (11 november 2026)

Klart:

- clipsmith är klonat (commit `c98bffc`) till den här mappen.
- Kampanjlagret är inlagt: `campaign/`, `profiles/`, `config.timmy.yaml`, `docs/klipp/`.
- Prompterna i `src/clipsmith/llm/prompts.py` är utbytta mot engelska, för korta klipp.
- Delen `cloud:` med originalets inloggningsuppgift är borttagen ur `config.yaml`.

Kvar:

- Ladda upp till `github.com/robinhel/ai-clip-creator`.
- Bygga Python-miljön och köra testerna.
- Fylla i nycklarna i `.env`.
- Första körningen på en riktig VOD.

## Att göra (Robin)

1. Kör `bash fortsatt.sh`. Det kopplar repot, laddar upp, bygger miljön och kör testerna.
2. Öppna `.env` och fyll i `ANTHROPIC_API_KEY`, `TWITCH_CLIENT_ID` och `TWITCH_CLIENT_SECRET`.
3. Sätt en utgiftsgräns i Claude Console.
4. Kör `claude` i den här mappen och ge instruktionen överst på sidan.

## Till Claude Code

Du tar över bygget av Robins klippverktyg. Läs först avsnittet "Robins kampanjlager" i
`CLAUDE.md`, sedan `docs/klipp/BYGGPLAN.md` och `docs/klipp/OMBYGGNAD.md`.

Viktigt att veta: den som skrev planen har **läst** clipsmiths kod men aldrig **kört** den.
Lita därför på vad du ser när du kör, inte på planen, och säg till när de skiljer sig.

Gör stegen i ordning. Rapportera kort till Robin efter varje steg.

1. **Kontrollera miljön.** Finns `.venv`? Annars kör `bash fortsatt.sh`. Kör
   `.venv/bin/python -m pytest tests_campaign -q` (33 tester ska gå igenom) och
   `.venv/bin/python -m pytest tests/unit -q`. Lista vilka tester som faller och varför.
2. **Kör `.venv/bin/python -m campaign check-setup`.** Rapportera vad som saknas.
   Robin fyller själv i nycklarna. Läs aldrig ut eller skriv ut nyckelvärden från `.env`.
3. **Ombyggnad 1:** rätta testet som väntar sig ordet "Spanish".
4. **Ombyggnad 2:** koppla clipsmiths Claude-anrop till budgetspärren
   (`campaign.budget.Budget`). `BudgetExceeded` ska stoppa körningen.
5. **Ombyggnad 4:** gör `min_picks` till en inställning med standardvärdet 0.
   (Ombyggnad 3 är redan gjord.)
6. **Första körningen**, när Robin har valt en VOD och lagt overlayen i `overlays/`:
   - `clipsmith run-vod <VOD-id> --config config.timmy.yaml`
   - Titta på ett klipp med Robin: sitter facecam rätt, är klippen 15–45 sekunder,
     är rubrikerna på engelska?
   - `python -m campaign finish <VOD-id> --game "<spelets namn>"`
   - `python -m campaign facit facit/<fil>.yaml` om Robin har skrivit ett facit.
7. Fortsätt sedan med fas 2 i `docs/klipp/BYGGPLAN.md`.

**Stoppregel:** går clipsmith inte att få igång på en kväll (testerna faller brett, eller
`run-vod` ger inga klipp), sluta laga och säg det till Robin. Reservplanen är att byta
bas till OpenShorts (`github.com/mutonby/openshorts`). Det mesta i `campaign/` fungerar
på vilket 9:16-klipp som helst.

**Arbetssätt:**

- En uppgift i taget. Kör testerna efter varje ändring.
- Gör en commit per uppgift med ett tydligt meddelande. Pusha när testerna är gröna.
- Lägg egen kod i `campaign/` när det går.
- Svara Robin på enkel svenska, med **Viktigt:** först och numrerade steg under **Att göra:**.
