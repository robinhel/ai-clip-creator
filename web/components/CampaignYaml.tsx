"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignProfileDetail } from "@/lib/types";

/** Radnumret i serverns fel, till exempel "rad 12" eller "line 12". */
function errorLine(detail: string | null): number | null {
  const match = detail?.match(/(?:rad|line)\s+(\d+)/i);
  return match ? Number(match[1]) : null;
}

interface Props {
  id: string;
  onUpdate: (profile: CampaignProfileDetail) => void;
  /** true när texten har ändringar som inte är sparade. */
  onDirty?: (dirty: boolean) => void;
}

/** Kampanjens profilfil som text, för det som formulären inte täcker. */
export function CampaignYaml({ id, onUpdate, onDirty }: Props) {
  const [saved, setSaved] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.campaign.profile
      .yaml(id)
      .then(({ yaml }) => {
        setSaved(yaml);
        setText(yaml);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the campaign file"));
  }, [id]);

  const dirty = saved !== null && text !== saved;

  useEffect(() => {
    onDirty?.(dirty);
    return () => onDirty?.(false);
  }, [dirty, onDirty]);

  const bad = errorLine(error);
  const lines = text.split("\n").length;

  async function save() {
    setBusy(true);
    setError(null);
    setOk(false);
    try {
      onUpdate(await api.campaign.profile.saveYaml(id, text));
      setSaved(text);
      setOk(true);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not save the campaign";
      // 400: servern nekade texten innan den skrev filen. Texten i rutan ligger kvar.
      setError((err as { status?: number }).status === 400 ? `${message} Nothing was saved.` : message);
      // Rulla till raden med felet.
      const line = errorLine(message);
      const el = box.current?.querySelector(`[data-line="${line}"]`) as HTMLElement | null;
      if (box.current && el) box.current.scrollTop = el.offsetTop - box.current.clientHeight / 3;
    } finally {
      setBusy(false);
    }
  }

  if (saved === null) {
    return error ? (
      <p role="alert" className="text-sm text-bad">{error}</p>
    ) : (
      <p className="text-sm text-muted-foreground">Loading the campaign file…</p>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        The whole campaign file, for what Overview does not show. Comments are kept as they are, and the server saves a copy of the old file.
      </p>
      {/* En enda rullningsruta: radnumren och texten rullar alltid tillsammans. */}
      <div
        ref={box}
        className="relative flex max-h-[640px] items-start overflow-auto rounded-xl border border-line bg-panel font-mono text-[13px] leading-[1.6] focus-within:border-brand"
      >
        <div aria-hidden className="sticky left-0 shrink-0 border-r border-line bg-panel py-3 text-right text-muted-foreground tabular-nums select-none">
          {Array.from({ length: lines }, (_, i) => (
            <div key={i} data-line={i + 1} className={cn("px-3", bad === i + 1 && "bg-[#3a1416] text-[#fca5a5]")}>
              {i + 1}
            </div>
          ))}
        </div>
        <textarea
          aria-label="The campaign file as YAML"
          value={text}
          spellCheck={false}
          wrap="off"
          onChange={(e) => {
            setText(e.target.value);
            setOk(false);
          }}
          className="min-h-[480px] min-w-0 flex-1 resize-none overflow-hidden bg-transparent px-3 py-3 whitespace-pre outline-none"
          style={{ height: `calc(${lines * 1.6}em + 1.5rem)`, width: `${Math.max(...text.split("\n").map((l) => l.length)) + 4}ch` }}
        />
      </div>
      {error && (
        <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
      {ok && !dirty && (
        <p role="status" className="flex items-center gap-2 text-sm text-good">
          <CheckCircle2 className="size-4" aria-hidden />
          The campaign is saved.
        </p>
      )}
      <div className="flex gap-2">
        <Button className="h-9" disabled={!dirty || busy} onClick={save}>
          {busy && <Loader2 className="animate-spin" aria-hidden />}
          Save
        </Button>
        <Button
          variant="ghost"
          className="h-9"
          disabled={!dirty || busy}
          onClick={() => {
            setText(saved);
            setError(null);
          }}
        >
          Undo changes
        </Button>
      </div>
    </div>
  );
}
