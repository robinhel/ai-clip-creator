import type { Meta, StoryObj } from "@storybook/react";
import { userEvent, within } from "storybook/test";
import { NewRunDialog } from "./NewRunDialog";
import { api } from "@/lib/api";
import { sampleRun } from "@/lib/fixtures";
import type { CampaignProfile } from "@/lib/types";

const profiles: CampaignProfile[] = [
  {
    name: "exempel",
    streamer: "Exempelstreamer",
    campaign: "Exempelkampanj",
    language: "en",
    channel: "exempel",
    formats: [],
    problems: [],
    ready: true,
  },
  {
    name: "trasig",
    streamer: "Annan streamer",
    campaign: "Kampanj utan overlay",
    language: "sv",
    channel: "annan",
    formats: [],
    problems: ["Overlay-bilden saknas"],
    ready: false,
  },
];

function mockProfiles() {
  const original = api.campaign.profiles;
  api.campaign.profiles = async () => profiles;
  return () => {
    api.campaign.profiles = original;
  };
}

const meta: Meta<typeof NewRunDialog> = {
  title: "Components/NewRunDialog",
  component: NewRunDialog,
  parameters: { nextjs: { appDirectory: true } },
  args: { onCreated: () => {} },
  beforeEach: mockProfiles,
};

export default meta;
type Story = StoryObj<typeof NewRunDialog>;

export const Default: Story = {};

export const Open: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText("Ny körning"));
  },
};

export const Submitting: Story = {
  beforeEach: () => {
    const original = api.campaign.runs.create;
    api.campaign.runs.create = () => new Promise(() => {}); // never resolves
    return () => {
      api.campaign.runs.create = original;
    };
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText("Ny körning"));
    await userEvent.type(
      await within(document.body).findByPlaceholderText("t.ex. 2345678901"),
      sampleRun.vod_id
    );
    await userEvent.click(within(document.body).getByRole("button", { name: /starta/i }));
  },
};

export const WithError: Story = {
  beforeEach: () => {
    const original = api.campaign.runs.create;
    api.campaign.runs.create = async () => {
      throw new Error("En körning pågår redan. Vänta tills den är klar.");
    };
    return () => {
      api.campaign.runs.create = original;
    };
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText("Ny körning"));
    await userEvent.type(
      await within(document.body).findByPlaceholderText("t.ex. 2345678901"),
      sampleRun.vod_id
    );
    await userEvent.click(within(document.body).getByRole("button", { name: /starta/i }));
  },
};
