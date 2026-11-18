// Längden i Redigera (ClipEditor), räknad som servern (campaign/editor.py _check_trim).

const speedText = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 });

/** Klippets längd: råtiden (ut − in) delad med hastigheten, avrundad till tusendelar. Utan hastighet 1. */
export function trimLength(inS: number, outS: number, speed = 1): number {
  return Math.round(((outS - inS) / speed) * 1000) / 1000;
}

/** " Längden gäller med hastigheten 1,1x." under längden, eller tomt när klippet går i vanlig hastighet. */
export function speedNote(speed = 1): string {
  return speed === 1 ? "" : ` Längden gäller med hastigheten ${speedText.format(speed)}x.`;
}
