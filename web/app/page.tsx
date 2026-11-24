import { HomeClient } from "@/components/HomeClient";

export const dynamic = "force-dynamic";

export default function Page() {
  if (process.env.MAINTENANCE_MODE === "true") {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <div className="text-center space-y-2">
          <p className="text-lg font-semibold">The service is paused</p>
          <p className="text-sm text-muted-foreground">The clip tool is turned off right now. Try again later.</p>
        </div>
      </main>
    );
  }

  return <HomeClient />;
}
