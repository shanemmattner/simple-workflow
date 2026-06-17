/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      screens: {
        mobile: { max: '640px' },
      },
    },
  },
  plugins: [],
};
