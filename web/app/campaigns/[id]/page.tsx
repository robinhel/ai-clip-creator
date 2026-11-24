import type { Metadata } from "next";
import { CampaignDetailClient } from "@/components/CampaignDetailClient";

export const metadata: Metadata = { title: "Campaign · Clips" };

type Query = string | string[] | undefined;
type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: Query; del?: Query; chatt?: Query; skapad?: Query; varning?: Query }>;
};

const one = (value: Query) => (typeof value === "string" ? value : undefined);

export default async function CampaignPage({ params, searchParams }: Props) {
  const [{ id }, { tab, del, chatt, skapad, varning }] = await Promise.all([params, searchParams]);
  const chat = Number(one(chatt));
  return (
    <CampaignDetailClient
      id={id}
      tab={one(tab)}
      part={one(del)}
      chat={Number.isInteger(chat) && chat > 0 ? chat : undefined}
      created={one(skapad) === "1"}
      twitchWarning={one(varning) === "twitch"}
    />
  );
}
