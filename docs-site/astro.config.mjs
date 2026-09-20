import { defineConfig, passthroughImageService } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightLinksValidator from 'starlight-links-validator';

const SITE = 'https://racecraft-lab.github.io';
const BASE = '/splash-evals';

export default defineConfig({
  site: SITE,
  base: BASE,
  trailingSlash: 'always',
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
          label: 'Start here',
          items: ['index', 'dashboard', 'methodology', 'benchmark-tasks', 'local-pilot-results'],
        },
        {
          label: 'System',
          items: ['architecture', 'privacy', 'operations'],
        },
        {
          label: 'Evidence',
          items: [
            'sources',
            'historical-frontier-comparison',
            'frontier-catalog',
            'frontier-verification',
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
  ],
});
