# Byggplan

**Viktigt:** Bocka av uppifrån och ner. Efter fas 1 kan du posta kampanjklipp utan
OpusClip. Hela planen med jämförelser finns här:
https://claude.ai/artifact/CEcrnwRKFeC3TKevEWyHgY

## Målet

Kampanjklara Twitch-klipp som når 2 000 visningar, med några minuters eget arbete per klipp.

| Mått | Mål (förslag) |
| --- | --- |
| Klipp som når 2 000 visningar | Högre andel än dina OpusClip-klipp |
| Engagemang per klipp | Minst 0,9 % |
| Träffar mot facit | Minst 7 av 10 handplockade ögonblick |
| Din tid per postat klipp | Under 5 minuter |

## Så hänger det ihop

1. **clipsmith** (klonat repo) hämtar VOD och chatt, transkriberar, hittar signaler,
   låter Claude välja ögonblick och klipper 9:16 med facecam överst.
2. **campaign/** (ditt lager) lägger på overlay och text, skriver caption,
   kontrollerar reglerna, låter Claude granska bildrutor och lägger klippet i kön.
3. **Du** säger ja eller nej, postar och skickar in länken.

## Fas 0 – Kom igång (1 kväll)

- [ ] Forka `ricardogr07/clipsmith` på GitHub
- [ ] Kör `python3 setup_klipp.py https://github.com/DITT-NAMN/clipsmith.git`
- [ ] Skapa miljön: `python3 -m venv .venv`, aktivera den, `pip install -e ".[dev,vision]"`
      och `pip install -r requirements-campaign.txt`
- [ ] Installera ffmpeg om det saknas
- [ ] Fyll i `.env`: Anthropic-nyckeln och Twitch-nycklarna
- [ ] Sätt en utgiftsgräns i Claude Console
- [ ] Kör `python -m pytest tests_campaign -q` (ska bli grönt)
- [ ] Kör `python -m pytest tests/unit -q` (clipsmiths egna tester; ett test om
      "Spanish" väntas falla, se OMBYGGNAD.md uppgift 1)
- [ ] Kör `python -m campaign check-setup --profile skiimasktimmy`

## Fas 1 – Första klippet (1–2 kvällar)

- [ ] Ladda ner kampanjens overlay till `overlays/skiimasktimmy.png`
- [ ] Välj en test-VOD och skriv upp de 10 bästa ögonblicken i `facit/`
- [ ] Kör `clipsmith run-vod <VOD-id> --config config.timmy.yaml`
- [ ] Titta på ett klipp: sitter facecam rätt? Annars OMBYGGNAD.md uppgift 12
- [ ] Mät hur lång tid transkriberingen tog; prova sedan `medium`
- [ ] Kör `python -m campaign finish <VOD-id> --profile skiimasktimmy --game "Spelets namn"`
- [ ] Kör `python -m campaign facit facit/<din-fil>.yaml` och skriv upp utgångsvärdet
- [ ] Kör `python -m campaign approve`, posta första klippet och logga det med `posted`
- [ ] OMBYGGNAD.md uppgift 1–4

## Fas 2 – Kampanjklara klipp i ett pass (3–4 kvällar)

- [ ] OMBYGGNAD.md uppgift 5: format och höjdpunkt i valet
- [ ] OMBYGGNAD.md uppgift 6: overlay och text i clipsmiths rendering
- [ ] OMBYGGNAD.md uppgift 7: undertexter i säker zon, mätt mot skärmbilder
- [ ] OMBYGGNAD.md uppgift 8: MCP-server för Claude Code
- [ ] Testa bildgranskningen på tio klipp och jämför med ditt eget omdöme
- [ ] Följ varje klipp i klipploggen fram till betalning

## Fas 3 – Bättre ögonblick (3–4 kvällar)

- [ ] OMBYGGNAD.md uppgift 9: höjdpunkten först
- [ ] OMBYGGNAD.md uppgift 10: flytta chattoppar bakåt, testa mot facit
- [ ] OMBYGGNAD.md uppgift 11: Claude läser hela transkriptet
- [ ] Jämför Sonnet 5.5 och Opus 5.5 mot facit och välj standardmodell
- [ ] Mät mot facit igen; målet är minst 7 av 10

## Fas 4 – Lär av resultaten (löpande)

- [ ] OMBYGGNAD.md uppgift 13: nattlig bevakning med `clipsmith watch`
- [ ] OMBYGGNAD.md uppgift 14: vilka signaler och format ger visningar?
- [ ] Jämför modeller via Opper; tumregel minst 20 postade klipp per variant
- [ ] Testa om maskade svordomar i undertexten påverkar räckvidden

## Resurser

| Resurs | Används till |
| --- | --- |
| Anthropic API-nyckel (200 euro/mån) | Välja ögonblick (Sonnet 5.5) och granska bildrutor (Haiku 5.5) |
| Claude-abonnemang (Claude Code) | Bygga om verktyget och styra det |
| Berget API-nyckel (lånad) | Reserv; Whisper-API om transkriberingen är för långsam |
| Opper API-nyckel | Jämföra modeller; reserv |
| Dator med RTX 2060 | Transkribering och rendering |

## Öppna frågor

- [ ] Finns datorn med RTX 2060 kvar, och kör den Windows eller Linux?
- [ ] Hur räknar kampanjen engagemang? Fråga i kampanjchatten.
- [ ] Hur länge sparas Timmys VODs på Twitch?
- [ ] Vilka gränser har den lånade Berget-nyckeln?
- [ ] Vilken kampanj blir nästa profil? Timmy-kampanjen har 2 500 dollar i budget.
