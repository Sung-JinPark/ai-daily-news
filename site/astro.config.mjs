import { defineConfig } from "astro/config";
import tailwind from "@astrojs/tailwind";

const repo = process.env.GITHUB_REPOSITORY ?? "";
const repoName = repo.split("/")[1] ?? "";
const userSite = process.env.GITHUB_REPOSITORY_OWNER
  ? `https://${process.env.GITHUB_REPOSITORY_OWNER}.github.io`
  : undefined;

export default defineConfig({
  site: userSite,
  base: repoName && !repoName.endsWith(".github.io") ? `/${repoName}` : "/",
  integrations: [tailwind()],
  output: "static",
  trailingSlash: "ignore",
});
