import { defineConfig } from "vitepress";

// The fastplace.dev docs site. Content-first: every page is plain Markdown
// under docs/site/; the sidebar mirrors the guides/ directory exactly, so a
// missing page fails `tests/test_publishing.py` rather than shipping a dead
// link. Deploy: `npm run docs:build` → .vitepress/dist (see
// .github/workflows/docs.yml).
const base = process.env.DOCS_BASE || "/";

export default defineConfig({
  lang: "en-US",
  title: "Fastplace",
  description:
    "An opinionated, full-stack, AI-native web framework built on Python and React — a productive developer experience, one modular-monolith deployable.",
  base,
  cleanUrls: true,
  sitemap: { hostname: "https://fastplace.dev" },
  // Per-page canonical: a single static <link rel="canonical"> pointing at
  // "/" tells search engines every page duplicates the homepage.
  transformHead({ pageData, head }) {
    const path = pageData.relativePath
      .replace(/\.md$/, "")
      .replace(/(^|\/)index$/, "$1");
    head.push(["link", { rel: "canonical", href: `https://fastplace.dev/${path}` }]);
  },
  themeConfig: {
    search: { provider: "local" },
    siteTitle: "Fastplace",
    nav: [
      { text: "Docs", activeMatch: "/(getting-started|guides|api)/", items: [
        { text: "Getting started", link: "/getting-started" },
        { text: "Guides", link: "/guides/pages-and-the-bridge" },
        { text: "API overview", link: "/api/overview" },
      ] },
      { text: "GitHub", link: "https://github.com/fastplace-dev/fastplace.dev" },
    ],
    sidebar: [
      {
        text: "Introduction",
        items: [
          { text: "Why Fastplace", link: "/index" },
          { text: "Getting started", link: "/getting-started" },
        ],
      },
      {
        text: "Guides",
        items: [
          { text: "Pages & the bridge", link: "/guides/pages-and-the-bridge" },
          { text: "Database & ORM", link: "/guides/database" },
          { text: "Authentication", link: "/guides/auth" },
          { text: "AI-native apps", link: "/guides/ai" },
          { text: "Multi-tenancy", link: "/guides/tenancy" },
          { text: "Background jobs & cache", link: "/guides/background-and-cache" },
          { text: "Storage", link: "/guides/storage" },
          { text: "Search", link: "/guides/search" },
          { text: "Mail & notifications", link: "/guides/mail-and-notifications" },
          { text: "Testing your app", link: "/guides/app-testing" },
          { text: "Framework internals testing", link: "/guides/testing" },
          { text: "Deployment", link: "/guides/deployment" },
          { text: "Upgrading", link: "/guides/upgrading" },
          { text: "Versioning", link: "/guides/versioning" },
        ],
      },
      {
        text: "Reference",
        items: [{ text: "API overview", link: "/api/overview" }],
      },
    ],
    socialLinks: [
      { icon: "github", link: "https://github.com/fastplace-dev/fastplace.dev" },
    ],
    footer: {
      message: "Released under the MIT License.",
      copyright: "Copyright © 2026 Firoz Anam",
    },
    outline: { level: [2, 3] },
  },
});
