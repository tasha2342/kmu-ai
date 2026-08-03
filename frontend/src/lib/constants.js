// 백엔드 app/models/enum.py 와 1:1로 대응하는 표시용 한글 라벨입니다.

// 'auto'는 백엔드 ChatLanguage에만 있는 값입니다. (FAQ 원문 언어를 뜻하는 Language에는 없습니다)
// 기본값으로 두어, 질문한 언어를 감지해 그 언어로 답하는 것이 기본 동작이 되게 합니다. (KAI-REQ-029)
export const LANGUAGES = [
  { value: 'auto', label: '자동 감지' },
  { value: 'ko', label: '한국어' },
  { value: 'en', label: 'English' },
  { value: 'zh', label: '中文' },
  { value: 'vi', label: 'Tiếng Việt' },
]

export const DEFAULT_LANGUAGE = 'auto'

// FAQ·규정 원문의 언어입니다. (백엔드 Language) 여기에는 '자동'이 있으면 안 됩니다.
// FAQ 편집·검색 화면이 이 목록을 그대로 선택지로 씁니다.
export const LANGUAGE_LABEL = {
  ko: '한국어',
  en: '영어',
  zh: '중국어',
  vi: '베트남어',
}

// 대화 세션의 언어 설정입니다. (백엔드 ChatLanguage)
export const CHAT_LANGUAGE_LABEL = {
  auto: '자동 감지',
  ...LANGUAGE_LABEL,
}

export const CHAT_SESSION_STATUS_LABEL = {
  active: '진행 중',
  idle_closed: '자동 종료',
  closed: '종료됨',
}

export const CHAT_ROLE_LABEL = {
  user: '사용자',
  assistant: '챗봇',
  system: '시스템',
}

export const CHAT_INTENT_LABEL = {
  academic: '학사',
  career: '취업·진로',
  personal: '개인 정보',
  document: '문서 기반',
  small_talk: '일상 대화',
  abuse: '부적절 발화',
  unknown: '판별 불가',
}

export const UNANSWERED_REASON_LABEL = {
  no_result: '검색 결과 없음',
  low_score: '유사도 임계값 미달',
  out_of_scope: '서비스 범위 밖',
  ambiguous: '질문이 모호함',
  model_error: '모델 호출 오류',
}

export const REVIEW_STATUS_LABEL = {
  pending: '검토 대기',
  in_review: '검토 중',
  resolved: '조치 완료',
  rejected: '조치 불필요',
}

export const REVIEW_STATUS_STYLE = {
  pending: 'bg-amber-50 text-amber-700 border-amber-200',
  in_review: 'bg-sky-50 text-sky-700 border-sky-200',
  resolved: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  rejected: 'bg-gray-100 text-gray-600 border-gray-200',
}

export const FAQ_STATUS_LABEL = {
  draft: '임시 저장',
  published: '공개',
  archived: '보관',
}

export const FAQ_STATUS_STYLE = {
  draft: 'bg-amber-50 text-amber-700 border-amber-200',
  published: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  archived: 'bg-gray-100 text-gray-600 border-gray-200',
}

export const FAQ_VISIBILITY_LABEL = {
  public: '전체 공개',
  student: '재학생',
  internal: '교직원',
}

export const VECTOR_STATUS_LABEL = {
  pending: '색인 대기',
  indexed: '색인 완료',
  stale: '재색인 필요',
  failed: '색인 실패',
}

export const VECTOR_STATUS_STYLE = {
  pending: 'bg-gray-100 text-gray-600 border-gray-200',
  indexed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  stale: 'bg-amber-50 text-amber-700 border-amber-200',
  failed: 'bg-rose-50 text-rose-700 border-rose-200',
}

export const SOURCE_TYPE_LABEL = {
  faq: 'FAQ',
  notice: '공지사항',
  regulation: '학칙·규정',
  document: '업로드 문서',
}

export const INGESTION_STATUS_LABEL = {
  pending: '대기',
  running: '실행 중',
  completed: '완료',
  failed: '실패',
}

export const INGESTION_STATUS_STYLE = {
  pending: 'bg-gray-100 text-gray-600 border-gray-200',
  running: 'bg-sky-50 text-sky-700 border-sky-200',
  completed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  failed: 'bg-rose-50 text-rose-700 border-rose-200',
}

export const INGESTION_ITEM_STATUS_LABEL = {
  pending: '대기',
  success: '성공',
  failed: '실패',
  skipped: '변경 없음',
}

export const RAG_EMBEDDING_STATUS_LABEL = {
  syncing: '동기화 중',
  success: '동기화 완료',
  error: '에러',
}

export const RAG_EMBEDDING_STATUS_STYLE = {
  syncing: 'bg-sky-50 text-sky-700 border-sky-200',
  success: 'bg-sky-50 text-sky-700 border-sky-200',
  error: 'bg-rose-50 text-rose-700 border-rose-200',
}

export const MASKING_TARGET_FIELD_LABEL = {
  student_id: '학번',
  phone: '전화번호',
  email: '이메일',
  resident_id: '주민등록번호',
  custom: '기타',
}

export const MASKING_METHOD_LABEL = {
  partial: '부분 마스킹',
  middle: '중간 마스킹',
  full: '전체 마스킹',
}

export const MASKING_ACTIVE_STYLE = {
  true: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  false: 'bg-gray-100 text-gray-600 border-gray-200',
}

export const ORDER_BY_OPTIONS = [
  { value: 'created_at_desc', label: '최신 생성순' },
  { value: 'created_at_asc', label: '오래된 생성순' },
  { value: 'updated_at_desc', label: '최근 수정순' },
  { value: 'updated_at_asc', label: '오래된 수정순' },
]

/** 챗봇 첫 화면에 노출할 예시 질문입니다. */
export const SAMPLE_QUESTIONS = [
  '2026학년도 1학기 수강신청 기간이 언제인가요?',
  '국가장학금 신청 방법을 알려주세요.',
  '졸업 요건은 어떻게 되나요?',
  '교내 취업 상담은 어디서 받을 수 있나요?',
]
