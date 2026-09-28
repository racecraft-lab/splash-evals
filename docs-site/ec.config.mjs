// @ts-check
/**
 * Expressive Code configuration for Splash Evals.
 * Ported unchanged from the Racecraft website (racecraft-lab/racecraft, website/ec.config.mjs)
 * so code blocks match the Racecraft "Racecraft Lab" dark theme.
 * Racecraft Systems LLC relicensed it for this repository under Apache-2.0.
 *
 * Features Enabled:
 * ✓ Line numbers (plugin)
 * ✓ Collapsible sections (plugin)
 * ✓ Text & line markers (built-in)
 * ✓ Diff syntax highlighting (built-in)
 * ✓ Editor/terminal frames (built-in)
 * ✓ Word wrap with indent preservation (built-in)
 */

import { defineEcConfig } from '@astrojs/starlight/expressive-code';
import { pluginCollapsibleSections } from '@expressive-code/plugin-collapsible-sections';
import { pluginLineNumbers } from '@expressive-code/plugin-line-numbers';

export default defineEcConfig({
  // ═══════════════════════════════════════════════════════════════════════
  // PLUGINS - IDE-like features
  // ═══════════════════════════════════════════════════════════════════════
  plugins: [pluginLineNumbers(), pluginCollapsibleSections()],

  // Use a dark base theme
  themes: ['github-dark'],

  // ═══════════════════════════════════════════════════════════════════════
  // DEFAULT PROPS - Applied to all code blocks
  // ═══════════════════════════════════════════════════════════════════════
  defaultProps: {
    // Line numbers - the IDE essential
    showLineNumbers: true,

    // Word wrapping for mobile/narrow viewports
    wrap: true,
    preserveIndent: true,

    // Collapsible sections - smart re-collapsible behavior
    collapseStyle: 'collapsible-auto',
  },

  // ═══════════════════════════════════════════════════════════════════════
  // STYLE OVERRIDES - Racecraft Lab dark theme
  // ═══════════════════════════════════════════════════════════════════════
  styleOverrides: {
    // ─────────────────────────────────────────────────────────────────────
    // TYPOGRAPHY - Fira Code with premium dev feel
    // ─────────────────────────────────────────────────────────────────────
    codeFontFamily:
      '"Fira Code", "Fira Mono", "JetBrains Mono", Menlo, Monaco, "Courier New", monospace',
    codeFontSize: '0.875rem',
    codeLineHeight: '1.7',
    codePaddingBlock: '1.25rem',
    codePaddingInline: '1.25rem',

    // ─────────────────────────────────────────────────────────────────────
    // COLORS - Deep blue-gray atmosphere
    // ─────────────────────────────────────────────────────────────────────
    codeBackground: '#0d1117',
    borderColor: '#30363d',
    borderWidth: '1px',
    borderRadius: '0.75rem',

    // Selection with brand blue
    codeSelectionBackground: 'rgba(60, 137, 198, 0.4)',

    // ─────────────────────────────────────────────────────────────────────
    // LINE NUMBERS - WCAG 2.1 AA compliant (4.5:1 contrast minimum)
    // #8b949e on #0d1117 = ~5.23:1 contrast ratio
    // ─────────────────────────────────────────────────────────────────────
    lineNumbers: {
      foreground: '#8b949e',
      highlightForeground: '#c9d1d9',
    },

    // ─────────────────────────────────────────────────────────────────────
    // TEXT MARKERS - Brand colors for highlighting
    // ─────────────────────────────────────────────────────────────────────
    textMarkers: {
      // Default marker (mark) - Brand Blue
      markBackground: 'rgba(60, 137, 198, 0.25)',
      markBorderColor: '#3c89c6',

      // Inserted lines - Green
      insBackground: 'rgba(63, 185, 80, 0.2)',
      insBorderColor: '#3fb950',
      insDiffIndicatorColor: '#3fb950',

      // Deleted lines - Red
      delBackground: 'rgba(248, 81, 73, 0.2)',
      delBorderColor: '#f85149',
      delDiffIndicatorColor: '#f85149',
    },

    // ─────────────────────────────────────────────────────────────────────
    // UI ELEMENTS - Professional frame styling
    // ─────────────────────────────────────────────────────────────────────
    uiFontFamily: '"Geist", "Inter", system-ui, sans-serif',
    uiFontSize: '0.8rem',
    uiFontWeight: '500',

    // Focus states with brand blue
    focusBorder: '#3c89c6',

    // ─────────────────────────────────────────────────────────────────────
    // FRAME STYLING - Tab bar and terminal header
    // ─────────────────────────────────────────────────────────────────────
    frames: {
      // Editor frame - VS Code inspired
      editorBackground: '#0d1117',
      editorActiveTabBackground: '#0d1117',
      editorActiveTabForeground: '#e6edf3',
      editorActiveTabBorderColor: 'transparent',
      editorActiveTabIndicatorBottomColor: '#3c89c6',
      editorActiveTabIndicatorTopColor: 'transparent',
      editorActiveTabIndicatorHeight: '2px',
      editorTabBarBackground: '#010409',
      editorTabBarBorderColor: '#21262d',
      editorTabBarBorderBottomColor: '#21262d',

      // Terminal frame styling
      terminalBackground: '#0d1117',
      terminalTitlebarBackground: '#010409',
      terminalTitlebarForeground: '#8b949e',
      terminalTitlebarBorderBottomColor: '#21262d',
      terminalTitlebarDotsForeground: '#8b949e',

      // Shadow for depth
      shadowColor: 'rgba(0, 0, 0, 0.5)',

      // Copy button - brand blue
      inlineButtonBackground: 'rgba(60, 137, 198, 0.1)',
      inlineButtonForeground: '#8b949e',
      inlineButtonBorder: 'transparent',
      inlineButtonBackgroundHoverOrFocusOpacity: '1',
      inlineButtonBackgroundActiveOpacity: '1',

      // Success tooltip - brand orange for visual pop
      tooltipSuccessBackground: '#e74900',
      tooltipSuccessForeground: '#ffffff',
    },
  },

  // ═══════════════════════════════════════════════════════════════════════
  // FRAME CONFIGURATION
  // ═══════════════════════════════════════════════════════════════════════
  frames: {
    showCopyToClipboardButton: true,
    extractFileNameFromCode: true,
    removeCommentsWhenCopyingTerminalFrames: true,
  },

  // ═══════════════════════════════════════════════════════════════════════
  // THEME CUSTOMIZATION - Enhanced syntax colors
  // ═══════════════════════════════════════════════════════════════════════
  customizeTheme: (theme) => {
    const colors = theme.colors;

    // Keywords - Bright blue
    if (colors['token.keyword']) {
      colors['token.keyword'] = '#ff7b72';
    }

    // Strings - Soft blue
    if (colors['token.string']) {
      colors['token.string'] = '#a5d6ff';
    }

    // Comments - Muted but readable
    if (colors['token.comment']) {
      colors['token.comment'] = '#8b949e';
    }

    // Functions - Purple accent
    if (colors['token.function']) {
      colors['token.function'] = '#d2a8ff';
    }

    // Numbers and constants - Orange
    if (colors['token.constant']) {
      colors['token.constant'] = '#ffa657';
    }

    // Variables
    if (colors['token.variable']) {
      colors['token.variable'] = '#c9d1d9';
    }

    // Types - Green
    if (colors['token.type']) {
      colors['token.type'] = '#7ee787';
    }

    return theme;
  },
});
