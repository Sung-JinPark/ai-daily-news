import { defineConfig } from "astro/config";
import tailwind from "@astrojs/tailwind";

const repo = process.env.GITHUB_REPOSITORY ?? "";
const repoName = repo.split("/")[1] ?? "";
// GitHub Pages serves at lowercased username, so lowercase here for correct
// absolute URLs in RSS / sitemap.
const owner = (process.env.GITHUB_REPOSITORY_OWNER ?? "").toLowerCase();
const userSite = owner ? `https://${owner}.github.io` : "https://localhost";

export default defineConfig({
  site: userSite,
  base: repoName && !repoName.endsWith(".github.io") ? `/${repoName}` : "/",
  integrations: [tailwind()],
  output: "static",
  trailingSlash: "ignore",
});
