import { FlaskConical } from "lucide-react";

/** Over every page in the demo mode (NEXT_PUBLIC_DEMO=1), so that nobody takes the sample data for real. */
export function DemoBanner() {
  return (
    <div
      role="status"
      className="flex items-center gap-2 border-b border-[#4a3b10] bg-[#1d170a] px-4 py-2 text-[13px] text-warn md:px-11"
    >
      <FlaskConical className="size-4 shrink-0" aria-hidden />
      <p>
        <span className="font-semibold">Demo mode: sample data.</span>{" "}
        <span className="text-[#e8d3a0]">The channels, clips and numbers are made up. Changes stay in this tab only.</span>
      </p>
    </div>
  );
}
