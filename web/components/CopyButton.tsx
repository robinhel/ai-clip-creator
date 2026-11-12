"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  text: string;
  label?: string;
}

export function CopyButton({ text, label = "Kopiera" }: Props) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Urklipp kräver https eller localhost; inget att göra här.
    }
  }

  return (
    <Button type="button" size="sm" variant="outline" onClick={handleCopy} disabled={!text}>
      {copied ? <Check /> : <Copy />}
      {copied ? "Kopierad" : label}
    </Button>
  );
}
