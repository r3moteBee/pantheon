/** @type {import('tailwindcss').Config} */
import typography from '@tailwindcss/typography'

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f0f9ff',
          100: '#e0f2fe',
          500: '#0ea5e9',
          600: '#0284c7',
          700: '#0369a1',
          900: '#0c4a6e',
        }
      },
      // Customize the typography plugin's prose-invert theme so the
      // artifact preview has clear visual hierarchy on dark backgrounds.
      typography: ({ theme }) => ({
        invert: {
          css: {
            '--tw-prose-headings': theme('colors.gray.100'),
            '--tw-prose-body': theme('colors.gray.300'),
            '--tw-prose-bold': theme('colors.gray.50'),
            '--tw-prose-links': theme('colors.brand.500'),
            '--tw-prose-quotes': theme('colors.gray.300'),
            '--tw-prose-quote-borders': theme('colors.brand.700'),
            '--tw-prose-code': theme('colors.amber.300'),
            '--tw-prose-pre-bg': theme('colors.gray.900'),
            '--tw-prose-pre-code': theme('colors.gray.200'),
            'h1': { fontSize: '1.75rem', marginTop: '1.5em', marginBottom: '0.6em', lineHeight: '1.2' },
            'h2': { fontSize: '1.4rem', marginTop: '1.5em', marginBottom: '0.5em', lineHeight: '1.25', borderBottom: '1px solid rgb(55 65 81)', paddingBottom: '0.25em' },
            'h3': { fontSize: '1.15rem', marginTop: '1.25em', marginBottom: '0.4em' },
            'p': { marginTop: '0.8em', marginBottom: '0.8em', lineHeight: '1.7' },
            'strong': { fontWeight: '700' },
            'a': { textDecoration: 'underline', textUnderlineOffset: '2px' },
            'hr': { borderColor: theme('colors.gray.800') },
            'code::before': { content: '""' },
            'code::after': { content: '""' },
          },
        },
      }),
    },
  },
  plugins: [
    typography,
  ],
}
