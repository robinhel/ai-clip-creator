"use client";

import { useEffect } from "react";

const LEAVE_WARNING =
  "Videorna i listan är inte sparade som klipp. Lämnar du sidan försvinner de ur listan. Vill du lämna sidan ändå?";

/** Ett klick på en länk som byter sida i samma flik. */
function leavesPage(e: MouseEvent): boolean {
  const link = (e.target as Element | null)?.closest?.("a[href]");
  if (e.defaultPrevented || !link) return false;
  return link.getAttribute("target") !== "_blank";
}

/** Varnar innan sidan lämnas medan videor inte är sparade: vid omladdning och vid klick på en länk i appen. */
export function useLeaveWarning(on: boolean) {
  useEffect(() => {
    if (!on) return;
    function beforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
      e.returnValue = ""; // äldre webbläsare kräver ett värde
    }
    // Före Next.js länkar (fångas på vägen ner), så att ett nej stoppar bytet av sida.
    function click(e: MouseEvent) {
      if (!leavesPage(e)) return;
      if (!window.confirm(LEAVE_WARNING)) {
        e.preventDefault();
        e.stopPropagation();
      }
    }
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", click, true);
    };
  }, [on]);
}
