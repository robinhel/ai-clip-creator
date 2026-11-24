// Små delar som flera delar av Egna videor använder.

export const field =
  "mt-1 h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand disabled:opacity-60 [&>option]:bg-panel";

export const label = (text: string, id: string) => (
  <label htmlFor={id} className="text-[13px] text-muted-foreground">
    {text}
  </label>
);

export const errorText = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

/** Ett fel med knappen Try again, till exempel när servern inte svarar. */
export function LoadError({ error, onRetry, className = "mt-4" }: { error: string; onRetry: () => void; className?: string }) {
  return (
    <div
      role="alert"
      className={`${className} flex flex-wrap items-center gap-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]`}
    >
      <span className="min-w-0 flex-1">{error}</span>
      <button type="button" onClick={onRetry} className="inline-flex h-8 items-center rounded-lg border border-[#4d1d1d] px-3 font-medium hover:bg-white/5">
        Try again
      </button>
    </div>
  );
}
