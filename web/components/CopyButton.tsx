"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  text: string;
  label?: string;
  disabled?: boolean;
  className?: string;
}

export function CopyButton({ text, label = "Kopiera", disabled = false, className }: Props) {
  const [result, setResult] = useState<"copied" | "failed" | null>(null);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setResult("copied");
    } catch {
      // Urklipp kräver https eller localhost, och webbläsaren kan neka. Säg det, så att Robin inte klistrar in något gammalt.
      setResult("failed");
    }
    setTimeout(() => setResult(null), 2500);
  }

  return (
    <Button type="button" size={className ? "default" : "sm"} variant="outline" onClick={handleCopy} disabled={disabled || !text} className={className}>
      {result === "copied" ? <Check /> : <Copy />}
      {result === "copied" ? "Kopierad" : result === "failed" ? "Gick inte att kopiera" : label}
    </Button>
  );
}
