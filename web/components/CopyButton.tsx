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
    <Button type="button" size={className ? "default" : "sm"} variant="outline" onClick={handleCopy} disabled={disabled || !text} className={className}>
      {copied ? <Check /> : <Copy />}
      {copied ? "Kopierad" : label}
    </Button>
  );
}
