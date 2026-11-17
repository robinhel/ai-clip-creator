import Link from "next/link";
import { CheckCircle2, Circle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChecklistItem } from "@/lib/checklist";

/** Checklistan överst på kampanjsidan. Visas tills allt är klart. */
export function CampaignChecklist({ items, created }: { items: ChecklistItem[]; created?: boolean }) {
  const left = items.filter((i) => !i.done).length;
  if (left === 0) {
    return created ? (
      <p role="status" className="mt-4 flex items-center gap-2 rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-sm text-good">
        <CheckCircle2 className="size-4 shrink-0" aria-hidden />
        Kampanjen är skapad och klar.
      </p>
    ) : null;
  }
  return (
    <section aria-label="Gör kampanjen klar" className="mt-5 max-w-[760px] rounded-2xl border border-line bg-panel p-4 sm:p-5">
      <h2 className="font-semibold">
        {created ? "Kampanjen är skapad. " : ""}Gör kampanjen klar
        <span className="ml-2 text-sm font-normal text-muted-foreground tabular-nums">
          {items.length - left} av {items.length} klart
        </span>
      </h2>
      <ul className="mt-3 space-y-2">
        {items.map((item) => (
          <li key={item.id} data-check={item.id} data-done={item.done} className="flex gap-2.5 text-sm">
            {item.done ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" aria-label="Klart" />
            ) : (
              <Circle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="Inte klart" />
            )}
            <span className="min-w-0">
              <Link
                href={item.href}
                className={cn("font-medium underline-offset-4 hover:underline", item.done ? "text-muted-foreground" : "text-foreground underline")}
              >
                {item.label}
              </Link>
              {!item.done && <span className="block text-[13px] text-muted-foreground">{item.todo}</span>}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
