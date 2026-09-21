import { defineConfig, passthroughImageService } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightLinksValidator from 'starlight-links-validator';

const SITE = 'https://racecraft-lab.github.io';
const BASE = '/splash-evals';

export default defineConfig({
  site: SITE,
  base: BASE,
  trailingSlash: 'always',
  redirects: {
    '/benchmark-tasks': `${BASE}/methodology/#benchmark-and-sample`,
    '/local-pilot-results': `${BASE}/methodology/#transport-and-scorer-qualification`,
    '/architecture': `${BASE}/operations/#local-system-boundary`,
    '/privacy': `${BASE}/methodology/#privacy-and-public-evidence`,
    '/capability-readiness': `${BASE}/dashboard/#run-record-and-limitations`,
    '/historical-frontier-comparison': `${BASE}/dashboard/#benchmark-comparison`,
    '/frontier-catalog': `${BASE}/sources/#catalog-semantics`,
    '/frontier-verification': `${BASE}/sources/#transcription-checks`,
    '/glossary': `${BASE}/#key-terms`,
  },
  image: { service: passthroughImageService() },
  integrations: [
    starlight({
      title: 'Splash Evals',
      description:
        'Privacy-first local Splash evaluation through LM Studio with dated frontier evidence.',
      plugins: [starlightLinksValidator()],
      customCss: ['./src/styles/brand.css', './src/styles/editorial.css'],
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
          items: ['index', 'dashboard', 'methodology', 'operations', 'sources'],
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
  ],
});
