# Ombyggnad av clipsmith

**Viktigt:** Det här är listan över vad som ska byggas om i clipsmith, med fil och
funktion. Den bygger på att jag läste källkoden 11 november 2026 (commit `c98bffc`).
Jag har **inte kört** clipsmith. Börja därför med att köra dess egna tester.

Ge en uppgift i taget till Claude Code, till exempel:
"Gör uppgift 2 i docs/klipp/OMBYGGNAD.md."

## Redan gjort av setup_klipp.py

- `src/clipsmith/llm/prompts.py` är utbytt mot en engelsk version för korta klipp
  (15–45 sekunder). Originalet var för en spansk streamer och klipp på 150 sekunder.

## Fas 1 – direkt efter första körningen

### 1. Rätta testet som väntar sig spanska
- **Fil:** `tests/unit/selection/test_selector.py`, testet
  `test_build_stream_context_contains_channel`.
- **Varför:** det kontrollerar att ordet "Spanish" finns i prompten. Nu står det "English".

### 2. Koppla clipsmiths Claude-anrop till budgetspärren
- **Fil:** `src/clipsmith/llm/anthropic_provider.py`, metoden `pick`.
- **Nu:** token-användningen loggas bara (`usage = response.usage`).
- **Gör:** anropa `Budget.check()` före `messages.create` och `Budget.record(...)` efter,
  med syftet `"valja"`. Bygg `Budget` som i `campaign/cli.py::_budget`.
- **Se upp:** metoden fångar alla fel med `except Exception` och returnerar `None`.
  `BudgetExceeded` måste släppas igenom, annars fortsätter körningen tyst.

### 3. Ta bort den incheckade inloggningsuppgiften
- **Fil:** `config.yaml`, delen `cloud:` (fältet `acr_password`).
- **Gör:** ta bort hela `cloud:`-delen i forken. Du använder `config.timmy.yaml`.

### 4. Sluta tvinga fram klipp som Claude sa nej till
- **Fil:** `src/clipsmith/selection/selector.py`, funktionen `select_clips`.
- **Nu:** om Claude godkänner färre än `min_picks=2` kandidater tvingas de bästa
  igenom ändå, med rubriken "Momento destacado".
- **Gör:** gör `min_picks` till en inställning i `ClipConfig` med standardvärdet 0.

## Fas 2 – kampanjklara klipp i ett pass

### 5. Format och höjdpunkt i valet
- **Filer:** `src/clipsmith/llm/base.py` (`ClipPick`), `src/clipsmith/llm/prompts.py`
  (schemat och prompten).
- **Gör:** lägg till fälten `format`, `payoff_start_s` och `payoff_end_s`. Byt namn på
  `title_es` till `title`, men läs fortfarande `title_es` från gamla filer.
  Formaten ska komma från kampanjprofilen (`campaign.profile.Profile.formats`).
- **Sedan:** `campaign/workflow.py::load_picks` läser redan `format` om det finns.

### 6. Overlay och text i clipsmiths egen rendering
- **Fil:** `src/clipsmith/rendering/clipper.py`, funktionerna `_stacked_filter_complex`
  och `_stacked_encode_args`.
- **Nu:** `python -m campaign finish` kodar om klippet en extra gång för att lägga på
  overlay och text. Det kostar lite bildkvalitet.
- **Gör:** lägg till overlay-bilden som en extra ingång och texten med `drawtext`,
  på samma sätt som `campaign/finish.py::build_filter`. Låt sedan `finish` hoppa
  över omkodningen när klippet redan har allt.
- **Samtidigt:** kodaren är hårdkodad till `libx264 -preset fast -crf 23`. Gör den
  valbar (`h264_nvenc` på datorn med RTX 2060) och höj kvaliteten.

### 7. Undertexter i säker zon
- **Fil:** `src/clipsmith/rendering/captions.py`, funktionen `_write_ass`.
- **Gör:** placera undertexterna i skarven mellan facecam och gameplay i staplat läge,
  och ovanför `safe_zone.bottom` annars. Mät zonen mot en skärmbild från TikTok.

### 8. MCP-server så att Claude Code kan styra verktyget
- **Ny fil:** `campaign/mcp_server.py`.
- **Verktyg:** `status`, `queue`, `decide` (ja eller nej), `clip_frames` (bildrutor ur
  ett klipp), `candidates` och `transcript` (läser `work/<VOD>/candidates.json` och
  `transcript.json`), `cut` (kör `clipsmith clip`).
- **Se upp:** Python-paketet `mcp` version 2 har bytt namn på `FastMCP` till `MCPServer`.
  Läs paketets dokumentation innan du skriver koden.

## Fas 3 – bättre ögonblick

### 9. Höjdpunkten först
- **Fil:** `src/clipsmith/rendering/clipper.py`, funktionen `_build_ffmpeg_cmd`.
- **Nu:** ett klipp är ett sammanhängande stycke (`-ss` och `-t`).
- **Gör:** när `payoff_start_s` finns, lägg 2–3 sekunder av höjdpunkten först och
  sedan hela klippet (två ingångar och `concat`). Undertexternas tider i
  `captions.py` måste flyttas lika mycket.

### 10. Chatten reagerar efter ögonblicket
- **Fil:** `src/clipsmith/candidates/math.py`, funktionen `compute_density_scores`.
- **Gör:** ny inställning `chat_lag_s` i `CandidatesConfig`. Dra den från chattoppens
  tid. Testa 5, 8 och 12 sekunder mot facit.

### 11. Claude läser hela transkriptet
- **Nu:** `selector.py` skickar ett anrop per kandidat med ±60 sekunder text.
  Ögonblick utan signal blir aldrig kandidater.
- **Gör:** nytt läge i en ny fil, `src/clipsmith/selection/full_transcript.py`: ett
  anrop med hela transkriptet plus kandidatlistan, svar med en lista av klipp.
  Jämför de två lägena mot facit och behåll det bästa.
- **Kostnad:** uppskattningsvis 0,10–0,40 dollar per VOD med Sonnet 5.5.

### 12. Facecam-rutan
- **Fil:** `src/clipsmith/rendering/detect.py` (ansiktsdetektering med OpenCV).
- **Gör:** kontrollera resultatet i `work/<VOD>/webcam_rect.json` mot en skärmbild.
  Stämmer det inte, skriv in rutan för hand i `config.timmy.yaml`
  (`reframe.webcam_rect`). OpenShorts (`camera_inset.py`, MIT) har en mer avancerad
  metod att låna idéer från om det behövs.

## Fas 4 – automatik

### 13. Nattlig bevakning
- `clipsmith watch` finns redan och klipper nya VODs automatiskt.
- **Gör:** kör `python -m campaign finish` efter varje VOD, så att klippen väntar i
  godkänn-kön på morgonen.

### 14. Lär av resultaten
- Klipploggen (`klippdata/klipp.db`) sparar signaler, format, visningar och
  engagemang per klipp.
- **Gör:** ett kommando som visar vilka signaler och format som ger flest visningar,
  och justera vikterna i `config.timmy.yaml` (`candidates:`) efter det.
