import { redirect } from "next/navigation";

/** Bevakning är fliken Nya kampanjer under Kampanjer. Gamla länkar och notiser hamnar där. */
export default function ScoutPage() {
  redirect("/campaigns?flik=nya");
}
