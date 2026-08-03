/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // 계명대학교 상징색(짙은 남색) 계열
        kmu: {
          950: '#04193a',
          900: '#062a5c',
          800: '#0a3a7a',
          700: '#0f4c96',
          600: '#1660b4',
          500: '#2a7ad0',
          400: '#5c9ce0',
          300: '#95bfec',
          200: '#c5dcf5',
          100: '#e4eefb',
          50: '#f3f7fd',
        },
      },
      fontFamily: {
        sans: [
          'Pretendard',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'Apple SD Gothic Neo',
          'Malgun Gothic',
          'sans-serif',
        ],
      },
    },
  },
  plugins: [],
}
