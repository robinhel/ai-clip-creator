// Regelkontrollens problem och varningar sparas i klipp.db när klippet görs. Klipp från före översättningen till
// engelska har kvar den svenska texten. Här är de gamla svenska texterna, på ett ställe: appen visar dem på engelska
// och känner igen dem i steps.ts. Nya texter kommer från campaign/rules.py, campaign/caption.py, drafts.with_advice,
// NO_OWN och NO_ACCOUNT i campaign/versions.py och MUSIC_GONE i campaign/drafts.py.

/** Gamla tal har komma (4,6), andra punkt. Engelska har punkt. */
const num = (value: string) => value.replace(",", ".");

const OLD: [RegExp, (...groups: string[]) => string][] = [
  [/^Captionen har kvar platshållaren (.+?)\. Skriv en egen text\.$/, (p) => `Caption still has the placeholder ${p}. Write your own text.`],
  [/^Captionen saknar (.+?) på en egen rad efter texten\.$/, (t) => `Caption is missing ${t} on its own line after the text.`],
  [/^Captionen har hashtags på första raden\. De ska stå efter (.+)\.$/, (t) => `Caption has hashtags on the first line. They belong after ${t}.`],
  [
    /^Captionen har (\d+) hashtags utöver (.+?)\. Kampanjen tillåter högst (\d+)\.$/,
    (n, own, max) =>
      `Caption has ${n} hashtags besides ${own === "de obligatoriska" ? "the required ones" : own}. The campaign allows at most ${max}.`,
  ],
  [/^Captionen saknar hashtaggen (.+)\.$/, (tag) => `Caption is missing the hashtag ${tag}.`],
  [/^Captionen saknar (.+?) på första raden\.$/, (must) => `Caption is missing ${must} on the first line.`],
  [/^Captionen saknar (.+)\.$/, (must) => `Caption is missing ${must}.`],
  [
    /^Klippet är ([\d.,]+) s\. Kampanjen kräver minst ([\d.,]+) s\.( Ladda upp en längre video eller tryck Nej\.)?$/,
    (length, min, advice) =>
      `The clip is ${num(length)} s. The campaign requires at least ${num(min)} s.${advice ? " Upload a longer video or press No." : ""}`,
  ],
  [/^Klippet är ([\d.,]+) s\. Profilens tak är ([\d.,]+) s\.$/, (length, max) => `The clip is ${num(length)} s. The campaign allows at most ${num(max)} s.`],
  [
    /^Klippet är (\d+×\d+), inte 1080×1920\. Sätt reframe\.mode: stacked \(eller center\) i config\.$/,
    (size) => `The clip is ${size}, not 1080×1920. Set reframe.mode: stacked (or center) in the config.`,
  ],
  [/^Originalljudet hörs inte\. Kampanjen har en regel om ljud: ([\s\S]*)$/, (rule) => `The original sound cannot be heard. The campaign has a rule about sound: ${rule}`],
  [/^Formatet (.+) finns inte i kampanjens lista\.$/, (format) => `The format ${format} is not in the campaign's list.`],
  // NO_ACCOUNT i campaign/versions.py
  [
    /^Klippet har ett inlägg utan konto på (.+)\. Det kan redan ligga på det här kontot\. Kontrollera innan du postar\.$/,
    (platform) => `The clip has a post without an account on ${platform}. It may already be on this account. Check before you post.`,
  ],
  // MUSIC_GONE i campaign/drafts.py
  [
    /^Musiken (.+) finns inte längre\. Lägg tillbaka den i (.+)\/, eller gör om klippet på Egna videor\.$/,
    (music, folder) => `The music ${music} no longer exists. Put it back in ${folder}/, or redo the clip on Own videos.`,
  ],
];

const OLD_FIXED: Record<string, string> = {
  "Klippet har inget format ännu. Välj ett när du godkänner.": "The clip has no format yet. Choose one when you approve.",
  "Klippet har ingen kampanj. Välj kampanj först.": "The clip has no campaign. Choose a campaign first.",
  "Klippet saknar ljud. Streamens ljud måste höras.": "The clip has no sound. The stream's sound must be heard.",
  "Texten på skärmen saknas.": "The on-screen text is missing.",
  "Overlayen saknas. Lägg bilden i overlays/ och kör finish igen.": "The overlay is missing. Put the image in overlays/ and run finish again.",
  "Ingen bildgranskning gjord. Kontrollera själv att facecam syns.": "No image review was done. Check yourself that the facecam is visible.",
  "Bildgranskningen hittade ingen facecam i klippet.": "The image review found no facecam in the clip.",
  "Bildgranskningen kunde inte läsa texten på skärmen.": "The image review could not read the on-screen text.",
  "Bildgranskningen såg text över ansiktet.": "The image review saw text over the face.",
  // NO_OWN i campaign/versions.py
  "Versionen har ingen ny hook och ingen ny startpunkt. Bara spegling, hastighet, filter eller font kan ge strike på YouTube och stoppa klippet från För dig på TikTok.":
    "The version has no new hook and no new start point. Only mirroring, speed, filter or font can get a strike on YouTube and keep the clip out of For You on TikTok.",
};

/** Ett gammalt captionfel som inte känns igen ovan räknas ändå som ett captionfel. */
export const isOldCaptionProblem = (problem: string) => problem.startsWith("Captionen ");

/** Den engelska texten för ett gammalt svenskt problem eller en varning. Andra texter, som de nya, blir som de är. */
export function englishProblem(problem: string): string {
  if (problem in OLD_FIXED) return OLD_FIXED[problem];
  for (const [pattern, english] of OLD) {
    const match = pattern.exec(problem);
    if (match) return english(...match.slice(1));
  }
  return problem;
}
