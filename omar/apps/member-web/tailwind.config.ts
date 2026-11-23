import type { Config } from 'tailwindcss';
import preset from '../../packages/ui/tailwind-preset.cjs';

export default {
  presets: [preset as unknown as Config],
  content: ['./src/**/*.{ts,tsx}', '../../packages/ui/src/**/*.{ts,tsx}'],
} satisfies Config;
