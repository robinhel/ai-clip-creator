import { DashboardClient } from "@/components/DashboardClient";

export const dynamic = "force-dynamic";

export default function Page() {
  if (process.env.MAINTENANCE_MODE === "true") {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <div className="text-center space-y-2">
          <p className="text-lg font-semibold">Tjänsten är pausad</p>
          <p className="text-sm text-muted-foreground">Klippverktyget är avstängt just nu. Försök igen senare.</p>
        </div>
      </main>
    );
  }

  return <DashboardClient />;
}
