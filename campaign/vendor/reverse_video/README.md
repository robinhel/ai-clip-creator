# reverse-video measure.py

Kopierat från https://github.com/kelano86/reverse-video, commit 793b80352dea379579c949b8f194da1cd01f863d.
Licens: MIT, se LICENSE i den här mappen.

## Vad som är kopierat

scripts/measure.py och scripts/common.py. measure.py importerar common.py, så den behövs också.
LICENSE från repots rot.

## Hur det används

campaign/taste.py kör measure.py som ett eget program med samma Python. Den lägger videon som video.mp4
och en liten meta.json i en tillfällig arbetsmapp, precis som fetch.py gör med en lokal fil.
Ingen nedladdning sker. Bara några siffror ur measures.json sparas. Arbetsmappen raderas efteråt.

## Ändringar i measure.py

Originalet använder paketet rapidocr_onnxruntime. Appen har rapidocr version 3 i stället.
En liten funktion RapidOCR högst upp i filen kör rapidocr 3 och ger svaret i samma form som förut.
De två raderna "from rapidocr_onnxruntime import RapidOCR" är bytta mot "import rapidocr".

text_colours hoppar över caption-händelser som saknar "frame". Händelserna från captions_precise har
inget "frame". När all text i caption-bandet ligger kvar längre än 6 sekunder blir caption-måtten None,
och då skickade main ändå de händelserna till text_colours. Det gav KeyError: 'frame', och klippet
mättes inte. Det hände på TikTok-klipp med en fast rubrik i bandet hela klippet (Content Rewards,
blizzard_nordics, november 2026). Nu blir färgerna None för sådana klipp, och resten mäts som vanligt.
Inget annat är ändrat.

## Ändringar i common.py

Inga.
