import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 개발 서버에서도 운영(nginx)과 동일하게 `/v1`, `/realms`를 상대 경로로 호출할 수 있도록
// 백엔드/Keycloak으로 프록시합니다. 덕분에 CORS 설정이 필요 없습니다.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3002,
    proxy: {
      // 백엔드 API 프록시 (kmu-ai-api 호스트 포트 8003)
      '/v1': {
        target: 'http://localhost:8003',
        changeOrigin: true,
      },
      // Keycloak 토큰 엔드포인트 프록시 (kmu-ai-keycloak 호스트 포트 8082)
      '/realms': {
        target: 'http://localhost:8082',
        changeOrigin: true,
      },
    },
  },
})
