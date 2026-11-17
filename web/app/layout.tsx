import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { SiteNav } from "@/components/SiteNav";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Klipp",
  description: "Kampanjklipp från Twitch-streams",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="sv"
      // Appen är alltid mörk; klassen slår på shadcn:s dark:-varianter.
      className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full md:grid md:grid-cols-[220px_minmax(0,1fr)]">
        <SiteNav />
        <div className="flex min-w-0 flex-col">{children}</div>
      </body>
    </html>
  );
}
