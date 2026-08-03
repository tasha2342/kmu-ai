import { api, buildQuery } from './api.js'

/**
 * 백엔드 엔드포인트 래퍼입니다.
 * 경로/파라미터 이름은 `app/api/v1/endpoints/{chat,chatbot_admin,faq,ingestion,rag,masking}.py` 의
 * OpenAPI 스키마와 그대로 맞췄습니다.
 */

// ===== 챗봇 대화 (/v1/chatbot) =====

export const chatbot = {
  createSession: (body) => api.post('/chatbot/session/create', body),
  listSessions: (params) => api.get(`/chatbot/session/list${buildQuery(params)}`),
  getSession: (sessionId, params) => api.get(`/chatbot/session/${sessionId}${buildQuery(params)}`),
  updateSession: (sessionId, body) => api.patch(`/chatbot/session/${sessionId}`, body),
  deleteSession: (sessionId) => api.delete(`/chatbot/session/${sessionId}`),
  /** stream=false 로 단일 JSON 응답을 받습니다. (스트리밍은 lib/chatStream.js) */
  sendMessage: (body) => api.post('/chatbot/message', { ...body, stream: false }),
  addFeedback: (body) => api.post('/chatbot/feedback', body),
  listFeedback: (params) => api.get(`/chatbot/feedback/list${buildQuery(params)}`),
}

// ===== 챗봇 분석·로그 (/v1/chatbot) =====

export const chatbotAdmin = {
  stats: (params) => api.get(`/chatbot/stats${buildQuery(params)}`),
  keywordStats: (params) => api.get(`/chatbot/stats/keywords${buildQuery(params)}`),
  intentStats: (params) => api.get(`/chatbot/stats/intents${buildQuery(params)}`),
  feedbackStats: (params) => api.get(`/chatbot/stats/feedback${buildQuery(params)}`),
  listUnanswered: (params) => api.get(`/chatbot/unanswered/list${buildQuery(params)}`),
  updateUnanswered: (id, body) => api.patch(`/chatbot/unanswered/${id}`, body),
  retrievalLogs: (params) => api.get(`/chatbot/logs/retrieval${buildQuery(params)}`),
  conversationLogs: (params) => api.get(`/chatbot/logs/conversation${buildQuery(params)}`),
  userLogs: (params) => api.get(`/chatbot/logs/user${buildQuery(params)}`),
}

// ===== FAQ 지식베이스 (/v1/faq) =====

export const faq = {
  listCategories: (params) => api.get(`/faq/category/list${buildQuery(params)}`),
  createCategory: (body) => api.post('/faq/category/create', body),
  updateCategory: (id, body) => api.patch(`/faq/category/update/${id}`, body),
  deleteCategory: (id) => api.delete(`/faq/category/delete/${id}`),
  list: (params) => api.get(`/faq/list${buildQuery(params)}`),
  info: (id) => api.get(`/faq/info/${id}`),
  create: (body) => api.post('/faq/create', body),
  update: (id, body) => api.patch(`/faq/update/${id}`, body),
  remove: (id) => api.delete(`/faq/delete/${id}`),
  sync: (body) => api.post('/faq/sync', body ?? {}),
  search: (body) => api.post('/faq/search', body),
}

// ===== 데이터 수집 (/v1/ingestion) =====

export const ingestion = {
  listJobs: (params) => api.get(`/ingestion/job/list${buildQuery(params)}`),
  runJob: (body) => api.post('/ingestion/job/run', body),
  getJob: (id) => api.get(`/ingestion/job/${id}`),
  deleteJob: (id) => api.delete(`/ingestion/job/${id}`),
  listJobItems: (id, params) => api.get(`/ingestion/job/${id}/items${buildQuery(params)}`),
}

// ===== RAG 관리 (/v1/rag) =====

export const rag = {
  listItems: (params) => api.get(`/rag/items${buildQuery(params)}`),
  getItem: (name) => api.get(`/rag/items/${encodeURIComponent(name)}`),
  updateItem: (name, body) => api.patch(`/rag/items/${encodeURIComponent(name)}`, body),
  setActive: (name, is_active) =>
    api.patch(`/rag/items/${encodeURIComponent(name)}/active`, { is_active }),
  sync: (body) => api.post('/rag/sync', body),
  remove: (names) => api.delete('/rag/items', { body: { names } }),
  listLogs: (name, params) =>
    api.get(`/rag/items/${encodeURIComponent(name)}/logs${buildQuery(params)}`),
  exportItems: (params) => api.get(`/rag/export${buildQuery(params)}`),
  upload: (name, formData) => api.postForm(`/rag/items/${encodeURIComponent(name)}/upload`, formData),
}

// ===== 마스킹 규칙 (/v1/masking) =====

export const masking = {
  listItems: (params) => api.get(`/masking/items${buildQuery(params)}`),
  getItem: (id) => api.get(`/masking/items/${id}`),
  create: (body) => api.post('/masking/items', body),
  update: (id, body) => api.patch(`/masking/items/${id}`, body),
  remove: (id) => api.delete(`/masking/items/${id}`),
  test: (body) => api.post('/masking/test', body),
}
