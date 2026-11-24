"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { uploadsApi, type DraftRender } from "@/lib/uploadsApi";
import { errorText } from "./ui";

const DEBOUNCE_MS = 300;

/** Bilden ur servern för url, hämtad en kort stund efter senaste ändringen. Fel visas som text. */
function useFrame(url: string | null) {
  const [src, setSrc] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = useRef<string | null>(null);

  useEffect(() => {
    if (!url) return;
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true);
      uploadsApi
        .frame(url, ctrl.signal)
        .then((blob) => {
          if (current.current) URL.revokeObjectURL(current.current);
          current.current = URL.createObjectURL(blob);
          setSrc(current.current);
          setError(null);
        })
        .catch((err) => !ctrl.signal.aborted && setError(errorText(err, "The picture could not be loaded.")))
        .finally(() => !ctrl.signal.aborted && setLoading(false));
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [url]);

  useEffect(() => () => void (current.current && URL.revokeObjectURL(current.current)), []);
  return { src, loading, error };
}

/** TikToks knappar och text täcker banden uppe och nere. */
function SafeZone({ top, bottom }: { top: number; bottom: number }) {
  return (
    <>
      <div data-testid="safe-top" className="pointer-events-none absolute inset-x-0 top-0 border-b border-dashed border-white/40 bg-black/45" style={{ height: `${top * 100}%` }} />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 border-t border-dashed border-white/40 bg-black/45" style={{ height: `${bottom * 100}%` }}>
        <span className="absolute top-1 left-1 rounded bg-black/70 px-1 text-[10px] text-white/80">Hidden by TikTok</span>
      </div>
    </>
  );
}

function Notes({ render }: { render: DraftRender }) {
  if (render.problems.length + render.warnings.length === 0) return <p className="text-xs text-good">The rule check found nothing.</p>;
  return (
    <ul className="space-y-1 text-xs">
      {render.problems.map((p) => (
        <li key={p} className="text-[#fca5a5]">
          {p}
        </li>
      ))}
      {render.warnings.map((w) => (
        <li key={w} className="text-warn">
          {w}
        </li>
      ))}
    </ul>
  );
}

interface Props {
  /** Bildens adress med valen, eller null utan utkast. */
  frameUrl: string | null;
  /** Förhandsvisningen när den gäller valen som är valda nu. */
  render?: DraftRender;
  outdated: boolean;
  previewing: boolean;
  /** Ljudet är inte originalljudet: bilden visar inte hur det låter. */
  silent?: boolean;
  safe?: { top: number; bottom: number } | null;
  showSafe: boolean;
  onShowSafe: (on: boolean) => void;
}

function Frame({ frameUrl }: { frameUrl: string | null }) {
  const frame = useFrame(frameUrl);
  return (
    <>
      {frame.src && (
        // eslint-disable-next-line @next/next/no-img-element -- bilden kommer som blob från servern.
        <img src={frame.src} alt="Frame from the clip as it will be" className="absolute inset-0 size-full object-contain" />
      )}
      {frame.loading && <Loader2 className="absolute top-2 right-2 size-4 animate-spin text-white/80" aria-label="Loading the picture" />}
      {frame.error && (
        <p role="alert" className="absolute inset-x-2 top-1/2 -translate-y-1/2 rounded bg-black/80 p-2 text-center text-xs text-[#fca5a5]">
          {frame.error}
        </p>
      )}
    </>
  );
}

function Caption({ render, outdated, silent, safe, showSafe, onShowSafe }: Omit<Props, "frameUrl" | "previewing">) {
  const playing = render && !outdated;
  return (
    <figcaption className="mt-2 space-y-1.5 text-xs text-muted-foreground">
      {safe && (
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showSafe} onChange={(e) => onShowSafe(e.target.checked)} className="size-3.5 accent-[var(--brand,#7c5cff)]" />
          Show TikTok's safe zone
        </label>
      )}
      <p>{playing ? "The whole clip, exactly as it is saved." : "The picture changes with the choices. Preview makes the whole clip."}</p>
      {silent && !playing && <p>The picture has no sound. Preview to hear the audio.</p>}
      {render && outdated && <p className="text-warn">The choices have changed. Preview again.</p>}
      {playing && <Notes render={render} />}
    </figcaption>
  );
}

function Busy() {
  return (
    <div role="status" className="absolute inset-0 grid place-content-center gap-2 bg-black/70 p-4 text-center text-xs text-white">
      <Loader2 className="mx-auto size-6 animate-spin" aria-hidden />
      Making the whole clip. It takes a little over a minute.
    </div>
  );
}

/** 9:16-rutan: bilden som ändras med valen, eller hela klippet efter Förhandsvisa. */
export function PreviewPane(props: Props) {
  const { frameUrl, render, outdated, previewing, safe, showSafe } = props;
  const playing = render && !outdated;
  return (
    <figure className="mx-auto w-full max-w-[230px] md:max-w-none">
      <div className="relative aspect-[9/16] w-full overflow-hidden rounded-xl border border-line bg-black">
        {playing ? (
          <video key={render.video_url} src={uploadsApi.videoUrl(render)} controls playsInline loop className="absolute inset-0 size-full" />
        ) : (
          <Frame frameUrl={frameUrl} />
        )}
        {showSafe && safe && <SafeZone top={safe.top} bottom={safe.bottom} />}
        {previewing && <Busy />}
      </div>
      <Caption {...props} />
    </figure>
  );
}
