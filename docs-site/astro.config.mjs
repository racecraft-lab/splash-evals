import { defineConfig, passthroughImageService } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightLinksValidator from 'starlight-links-validator';
import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';

const SITE = 'https://racecraft-lab.github.io';
const BASE = '/splash-evals';

export default defineConfig({
  site: SITE,
  base: BASE,
  trailingSlash: 'always',
  image: { service: passthroughImageService() },
  devToolbar: { enabled: false },
  vite: { plugins: [tailwindcss()] },
  integrations: [
    starlight({
      title: 'Splash Evals',
      description:
        'Can a free AI model on your own Mac take routine work off a paid Claude Code or Codex plan? Step one: testing Qwen3.8 through Splash on two standard benchmarks.',
      plugins: [starlightLinksValidator()],
      customCss: ['./src/styles/global.css', './src/styles/brand.css', './src/styles/editorial.css', './src/styles/bench.css'],
      logo: {
        light: './src/assets/logo.svg',
        dark: './src/assets/logo-light.svg',
        replacesTitle: true,
        alt: 'Racecraft',
      },
      favicon: '/favicon.svg',
      components: {
        Header: './src/components/Header.astro',
        Footer: './src/components/Footer.astro',
        Hero: './src/components/PageIntro.astro',
        PageTitle: './src/components/PageIntro.astro',
        MarkdownContent: './src/components/MarkdownContent.astro',
        ThemeProvider: './src/components/ThemeProvider.astro',
        ThemeSelect: './src/components/ThemeSelect.astro',
      },
      head: [
        {
          tag: 'meta',
          attrs: { name: 'theme-color', content: '#dc143c' },
        },
      ],
      sidebar: [
        {
          label: 'Splash Evals',
          items: [
            'index',
            {
              label: 'Results',
              items: ['dashboard', 'dashboard/gpqa-diamond', 'dashboard/swe-bench-verified'],
            },
            'what-it-means',
            'methodology',
            'sources',
            'operations',
          ],
        },
      ],
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/racecraft-lab/splash-evals',
        },
      ],
    }),
    react(),
  ],
});
