/** Den dolda kampanjen för liveklipp utan kampanj (profiles/_ingen.yaml). Samma som NO_CAMPAIGN i campaign/rules.py. */
export const NO_CAMPAIGN = "_ingen";

/** Kampanjens namn som Robin ser det: den dolda kampanjen heter "Utan kampanj". */
export const campaignLabel = (name: string): string => (name === NO_CAMPAIGN ? "Utan kampanj" : name);

/** Falskt för den dolda kampanjen: den har ingen profil att hämta (API:t svarar 404). */
export const hasCampaign = (name: string): boolean => name !== NO_CAMPAIGN;
