"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import type { Rect } from "@/lib/types";

const SRC_W = 1920;
const SRC_H = 1080;

/** En panel som visar rutan `rect` ur källbilden, utdragen till panelens form som i klippet. */
function Panel({ src, rect, heightPct, onError }: { src: string; rect: Rect; heightPct: number; onError: () => void }) {
  const [x, y, w, h] = rect;
  return (
    <div className="relative w-full overflow-hidden" style={{ height: `${heightPct}%` }}>
      {/* eslint-disable-next-line @next/next/no-img-element -- bildruta från servern, beskärs med CSS. */}
      <img
        src={src}
        alt=""
        draggable={false}
        onError={onError}
        className="absolute max-w-none select-none"
        style={{
          width: `${(SRC_W / w) * 100}%`,
          height: `${(SRC_H / h) * 100}%`,
          left: `${(-x / w) * 100}%`,
          top: `${(-y / h) * 100}%`,
        }}
      />
    </div>
  );
}

interface Props {
  /** Bildrutan i 1920x1080. Saknas den visas en neutral platta. */
  src: string | null;
  webcam: Rect | null;
  gameplay: Rect | null;
  split: number;
  className?: string;
  /** Lager ovanpå, till exempel overlay-bilden och de säkra zonerna. */
  children?: React.ReactNode;
  /** Kampanjen har ingen kamera: en panel med videon i stället för kamera och spel. */
  single?: boolean;
}

/** Klippet i 9:16: kameran överst och spelet under, som clipsmiths läge stacked. */
export function StackedPreview({ src, webcam, gameplay, split, className, children, single }: Props) {
  const [failed, setFailed] = useState<string | null>(null);
  const ok = !single && src && failed !== src && webcam && gameplay;
  return (
    <div
      className={cn("relative aspect-[9/16] w-full overflow-hidden rounded-xl bg-[#1f1f26] [container-type:inline-size]", className)}
    >
      {ok ? (
        <>
          <Panel src={src} rect={webcam} heightPct={split * 100} onError={() => setFailed(src)} />
          <Panel src={src} rect={gameplay} heightPct={(1 - split) * 100} onError={() => setFailed(src)} />
        </>
      ) : single ? (
        <div className="grid h-full place-items-center text-xs text-muted-foreground">Video</div>
      ) : (
        <div className="flex h-full flex-col">
          <div className="grid place-items-center border-b border-dashed border-line text-xs text-muted-foreground" style={{ height: `${split * 100}%` }}>
            Camera
          </div>
          <div className="grid flex-1 place-items-center text-xs text-muted-foreground">Game</div>
        </div>
      )}
      {children}
    </div>
  );
}
