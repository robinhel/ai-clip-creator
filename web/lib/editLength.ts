// Längden i Redigera (ClipEditor), räknad som servern (campaign/editor.py _check_trim).

const speedText = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 });

/**
 * Klippets längd: råtiden (ut − in) delad med hastigheten, plus serverns extra tid (length_extra_s),
 * avrundad till tusendelar. Utan hastighet 1, utan extra tid 0.
 */
export function trimLength(inS: number, outS: number, speed = 1, extraS = 0): number {
  return Math.round(((outS - inS) / speed + extraS) * 1000) / 1000;
}

/** " Längden gäller med hastigheten 1,1x." under längden, eller tomt när klippet går i vanlig hastighet. */
export function speedNote(speed = 1): string {
  return speed === 1 ? "" : ` Längden gäller med hastigheten ${speedText.format(speed)}x.`;
}
