export interface TimelineStep {
  phase: string;
  date: string;
  status: 'done' | 'current' | 'upcoming';
}

export interface ReviewHistory {
  date: string;
  committee: string;
  result: string;
  detail: string;
}

export interface PipelineDrug {
  id: string;
  name: string;
  ingredient: string;
  company: string;
  indication: string;
  type: string;
  status: 'waiting' | 'scheduled' | 'completed';
  submittedDate: string;
  approvalDate: string;
  updatedDate: string;
  notes?: string;
  history: ReviewHistory[];
  expectedTimeline: TimelineStep[];
}

export interface PipelineStage {
  id: string;
  label: string;
  description: string;
  count: number;
  drugs: PipelineDrug[];
}

export interface IntelligenceReport {
  id: string;
  title: string;
  category: 'pre-cancer' | 'post-cancer' | 'pre-evaluation' | 'post-evaluation';
  categoryLabel: string;
  year: number;
  date: string;
  cycle: string;
  summary: string;
  highlights: string[];
  downloadUrl?: string;
  fileSize?: string;
  pages?: number;
}

export interface MeetingSchedule {
  id: string;
  month: number;
  monthLabel: string;
  type: 'cancer' | 'evaluation';
  typeLabel: string;
  cycle: string;
  date: string;
  dayOfWeek: string;
  isPast: boolean;
  isUpcoming: boolean;
  isToday: boolean;
  daysUntil: number;
  note?: string;
}

export interface MeetingResultDrug {
  name: string;
  ingredient: string;
  company: string;
  indication: string;
  result: 'approved' | 'rejected' | 'deferred';
  resultLabel: string;
}

export interface MeetingResult {
  id: string;
  meetingId: string;
  title: string;
  date: string;
  type: 'cancer' | 'evaluation';
  cycle: string;
  totalReviewed: number;
  approved: number;
  rejected: number;
  deferred: number;
  drugs: MeetingResultDrug[];
  summary: string;
  keyTakeaways: string[];
  nextSteps: string;
}

const CDN_BASE = 'https://storage.readdy-site.link/project_files/e67f88d1-cac6-48c3-bc82-c2b4a8e96248';

export const pipelineStages: PipelineStage[] = [
  {
    id: 'cancer',
    label: '암질환심의위원회',
    description: '중증(암)질환심의위원회 심의 진행',
    count: 8,
    drugs: [
      {
        id: 'd1',
        name: '다트로웨이',
        ingredient: 'Dato-DXd',
        company: '다이이치산쿄 / 한국AZ',
        indication: 'HR+/HER2- 전이성 유방암',
        type: '신규',
        status: 'waiting',
        submittedDate: '2026.03.10',
        approvalDate: '2026.02.28',
        updatedDate: '2026.03.10',
        notes: '식약처 허가 완료, 8월 7차 회차 후보',
        history: [
          { date: '2026.02.28', committee: '식약처', result: '허가', detail: 'HR+/HER2- 전이성 유방암 2차 이상 단독요법 승인' },
          { date: '2026.03.10', committee: '심평원', result: '접수', detail: '약제 급여 신청서 접수 완료' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2026.02', status: 'done' },
          { phase: '급여 신청', date: '2026.03', status: 'done' },
          { phase: '암질심 상정', date: '2026.08', status: 'upcoming' },
          { phase: '약평위 심의', date: '2026.10', status: 'upcoming' },
          { phase: '건보 등재', date: '2027.01', status: 'upcoming' },
        ],
      },
      {
        id: 'd2',
        name: '임델트라',
        ingredient: '탈라타맙',
        company: '암젠코리아',
        indication: 'SCLC 2차 이상',
        type: '신규',
        status: 'scheduled',
        submittedDate: '2026.01.10',
        approvalDate: '2025.11.15',
        updatedDate: '2026.02.28',
        notes: '7월 8일 6차 회차 상정 예정',
        history: [
          { date: '2025.11.15', committee: '식약처', result: '허가', detail: '이전 백금기반 화학요법 후 진행된 SCLC 2차 이상' },
          { date: '2026.01.10', committee: '심평원', result: '접수', detail: '약제 급여 신청서 접수' },
          { date: '2026.02.18', committee: '암질심 2차', result: '미설정', detail: 'OS 데이터 미성숙 사유' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2025.11', status: 'done' },
          { phase: '1차 암질심', date: '2026.02', status: 'done' },
          { phase: '2차 암질심(6차)', date: '2026.07', status: 'current' },
          { phase: '약평위 심의', date: '2026.09', status: 'upcoming' },
          { phase: '건보 등재', date: '2026.12', status: 'upcoming' },
        ],
      },
      {
        id: 'd3',
        name: '웰리렉',
        ingredient: '벨주티판',
        company: '한국MSD',
        indication: 'VHL 증후군 관련 RCC, CNS Hb, pNET',
        type: '신규',
        status: 'waiting',
        submittedDate: '2024.06.15',
        approvalDate: '2024.03.20',
        updatedDate: '2025.03.20',
        notes: '3차 도전, 4차 제출 후 심의 대기 중',
        history: [
          { date: '2024.03.20', committee: '식약처', result: '허가', detail: 'VHL 증후군 관련 적응증 3종 동시 승인' },
          { date: '2024.06.15', committee: '암질심 5차', result: '미설정', detail: '1차 도전: 경제성 평가 자료 미흡' },
          { date: '2024.10.22', committee: '암질심 9차', result: '미설정', detail: '2차 도전: 비교약제 선정 이슈' },
          { date: '2025.03.20', committee: '암질심 3차', result: '미설정', detail: '3차 도전: 재정영향평가 BIA 과다' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2024.03', status: 'done' },
          { phase: '1~3차 암질심', date: '2024.06~2025.03', status: 'done' },
          { phase: '4차 재신청 준비', date: '2026.Q3', status: 'current' },
          { phase: '암질심 상정', date: '2026.Q4', status: 'upcoming' },
          { phase: '약평위 심의', date: '2027.Q1', status: 'upcoming' },
        ],
      },
      {
        id: 'd4',
        name: '엔허투 HER2 저발현',
        ingredient: '트라스투주맙 데룩스테칸',
        company: '다이이치산쿄 / 한국AZ',
        indication: 'HER2 저발현·초저발현 전이성 유방암',
        type: '신규',
        status: 'waiting',
        submittedDate: '2025.05.10',
        approvalDate: '2025.03.01',
        updatedDate: '2026.03.15',
        notes: '학회 주도 재신청, 6만명 국민동의청원 진행',
        history: [
          { date: '2025.03.01', committee: '식약처', result: '허가', detail: 'HER2 저발현(IHC 1+/2+ ISH-) 전이성 유방암' },
          { date: '2025.05.10', committee: '암질심 3차', result: '미설정', detail: '1차 도전: 재정영향평가 BIA 초대형' },
          { date: '2025.09.15', committee: '암질심 7차', result: '미설정', detail: '2차 도전: 저발현 환자군 정의 논란' },
          { date: '2026.03.15', committee: '암질심 2차', result: '미설정', detail: '3차 도전: 학회 공동 건의서 제출' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2025.03', status: 'done' },
          { phase: '1~3차 암질심', date: '2025.05~2026.03', status: 'done' },
          { phase: '국민청원 진행', date: '2026.Q2', status: 'current' },
          { phase: '암질심 재상정', date: '2026.Q4', status: 'upcoming' },
          { phase: '약평위 심의', date: '2027.Q1', status: 'upcoming' },
        ],
      },
      {
        id: 'd5',
        name: '옥타이로',
        ingredient: '레포트렉티닙',
        company: '한국BMS',
        indication: 'NTRK 융합 양성 고형암(잔여 적응증)',
        type: '확대',
        status: 'waiting',
        submittedDate: '2025.10.30',
        approvalDate: '2025.08.20',
        updatedDate: '2026.01.20',
        notes: '일부 적응증 통과 후 잔여 적응증 재신청',
        history: [
          { date: '2025.08.20', committee: '식약처', result: '허가', detail: 'NTRK 융합 양성 고형암 적응증 확대 승인' },
          { date: '2025.10.30', committee: '암질심 9차', result: '일부설정', detail: '기허가 적응증 통과, 잔여 적응증 미설정' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2025.08', status: 'done' },
          { phase: '1차 암질심', date: '2025.10', status: 'done' },
          { phase: '잔여 적응증 암질심', date: '2026.Q3', status: 'current' },
          { phase: '약평위 심의', date: '2026.Q4', status: 'upcoming' },
          { phase: '건보 등재', date: '2027.Q1', status: 'upcoming' },
        ],
      },
      {
        id: 'd6',
        name: '가텍스주',
        ingredient: '테두글루타이드',
        company: '한국 출시기업',
        indication: '단장증후군(중증희귀질환)',
        type: '신규',
        status: 'waiting',
        submittedDate: '2024.08.01',
        approvalDate: '2024.05.10',
        updatedDate: '2025.11.10',
        notes: '다회 도전, 재신청 트랙',
        history: [
          { date: '2024.05.10', committee: '식약처', result: '허가', detail: '단장증후군 희귀의약품 지정' },
          { date: '2024.10.15', committee: '암질심 9차', result: '미설정', detail: '임상적 유용성 입증 미흡' },
          { date: '2025.04.10', committee: '암질심 3차', result: '미설정', detail: '경제성 평가 보완 요구' },
          { date: '2025.11.10', committee: '암질심 10차', result: '미설정', detail: '추가 임상 데이터 제출 대기' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2024.05', status: 'done' },
          { phase: '1~3차 암질심', date: '2024.10~2025.11', status: 'done' },
          { phase: '임상 보완', date: '2026.Q2', status: 'current' },
          { phase: '암질심 재상정', date: '2026.Q4', status: 'upcoming' },
          { phase: '약평위 심의', date: '2027.Q2', status: 'upcoming' },
        ],
      },
      {
        id: 'd7',
        name: '티루캡',
        ingredient: '카피바설팁',
        company: '한국AZ',
        indication: 'HR+/HER2- AKT·PIK3CA·PTEN 변이 진행성 유방암',
        type: '신규',
        status: 'waiting',
        submittedDate: '2026.04.15',
        approvalDate: '2026.03.01',
        updatedDate: '2026.05.10',
        notes: '4월 미설정 후 재신청 트랙',
        history: [
          { date: '2026.03.01', committee: '식약처', result: '허가', detail: 'PIK3CA/AKT1/PTEN 변이 HR+/HER2- 진행성 유방암' },
          { date: '2026.04.15', committee: '암질심 4차', result: '미설정', detail: '바이오마커 선별검사 급여화 선결 과제' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2026.03', status: 'done' },
          { phase: '1차 암질심', date: '2026.04', status: 'done' },
          { phase: '재신청 준비', date: '2026.Q3', status: 'current' },
          { phase: '암질심 재상정', date: '2026.Q4', status: 'upcoming' },
          { phase: '약평위 심의', date: '2027.Q1', status: 'upcoming' },
        ],
      },
      {
        id: 'd8',
        name: '엔허투 신규 적응증',
        ingredient: '트라스투주맙 데룩스테칸',
        company: '다이이치산쿄 / 한국AZ',
        indication: 'HER2+ 유방암 1차 / HER2 ultralow / 위암 2차',
        type: '확대',
        status: 'waiting',
        submittedDate: '2026.04.30',
        approvalDate: '2026.04.30',
        updatedDate: '2026.04.30',
        notes: '식약처 허가 4월 30일 완료, 9월 8차 회차 후보',
        history: [
          { date: '2026.04.30', committee: '식약처', result: '허가', detail: 'HER2+ 유방암 1차, HER2 ultralow, 위암 2차 동시 승인' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2026.04', status: 'done' },
          { phase: '급여 신청', date: '2026.05', status: 'done' },
          { phase: '암질심 상정', date: '2026.09', status: 'upcoming' },
          { phase: '약평위 심의', date: '2026.11', status: 'upcoming' },
          { phase: '건보 등재', date: '2027.02', status: 'upcoming' },
        ],
      },
    ],
  },
  {
    id: 'evaluation',
    label: '약제급여평가위원회',
    description: '약제급여평가위원회 심의 및 평가',
    count: 3,
    drugs: [
      {
        id: 'd9',
        name: '버제니오정',
        ingredient: '아베마시클립',
        company: '한국릴리',
        indication: 'HR+/HER2- 조기 유방암 보조',
        type: '확대',
        status: 'scheduled',
        submittedDate: '2025.03.10',
        approvalDate: '2024.09.15',
        updatedDate: '2026.05.27',
        notes: '5월 27일 4차 암질심 통과, 6월 18일 약평위 상정 예정',
        history: [
          { date: '2024.09.15', committee: '식약처', result: '허가', detail: 'HR+/HER2- 조기 유방암 수술 후 보조요법 적응증 확대' },
          { date: '2025.05.20', committee: '암질심 4차', result: '미설정', detail: '1차 도전: OS 데이터 미성숙' },
          { date: '2025.09.17', committee: '암질심 7차', result: '미설정', detail: '2차 도전: 재정영향평가 보완 요구' },
          { date: '2026.01.21', committee: '암질심 1차', result: '미설정', detail: '3차 도전: 비교약제 변경 필요' },
          { date: '2026.05.27', committee: '암질심 4차', result: '설정', detail: '4차 도전만에 통과, ESMO 2025 OS 데이터 결정적' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2024.09', status: 'done' },
          { phase: '1~3차 암질심', date: '2025.05~2026.01', status: 'done' },
          { phase: '4차 암질심 통과', date: '2026.05', status: 'done' },
          { phase: '약평위 심의', date: '2026.06', status: 'current' },
          { phase: '건보 등재', date: '2026.08', status: 'upcoming' },
        ],
      },
      {
        id: 'd10',
        name: '엘라히어주',
        ingredient: '미르베툭시맙 소라브탄신',
        company: '한국애브비',
        indication: 'FRα+ 재발성 난소암',
        type: '신규',
        status: 'scheduled',
        submittedDate: '2025.11.20',
        approvalDate: '2025.10.01',
        updatedDate: '2026.05.27',
        notes: '5월 27일 4차 암질심 통과, 6월 18일 약평위 상정 예정',
        history: [
          { date: '2025.10.01', committee: '식약처', result: '허가', detail: 'FRα 양성 백금저항성 재발성 난소암' },
          { date: '2026.01.21', committee: '암질심 1차', result: '미설정', detail: '1차 도전: 바이오마커 검사 급여화 이슈' },
          { date: '2026.05.27', committee: '암질심 4차', result: '설정', detail: '2차 도전 통과, 토론회 후 정부 적극 검토 의지' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2025.10', status: 'done' },
          { phase: '1차 암질심', date: '2026.01', status: 'done' },
          { phase: '2차 암질심 통과', date: '2026.05', status: 'done' },
          { phase: '약평위 심의', date: '2026.06', status: 'current' },
          { phase: '건보 등재', date: '2026.09', status: 'upcoming' },
        ],
      },
      {
        id: 'd11',
        name: '킴리아 FL',
        ingredient: '티사젠렉류셀',
        company: '한국노바티스',
        indication: 'R/R 소포성 림프종(확대)',
        type: '확대',
        status: 'waiting',
        submittedDate: '2026.02.15',
        approvalDate: '2025.12.20',
        updatedDate: '2026.04.15',
        notes: '4월 15일 FL 확대 미설정, 재신청 준비 중',
        history: [
          { date: '2025.12.20', committee: '식약처', result: '허가', detail: 'R/R 소포성 림프종 CAR-T 적응증 확대' },
          { date: '2026.04.15', committee: '암질심 4차', result: '미설정', detail: 'CAR-T 치료 고비용 대비 ICER 불리' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2025.12', status: 'done' },
          { phase: '1차 암질심', date: '2026.04', status: 'done' },
          { phase: '경제성 보완', date: '2026.Q3', status: 'current' },
          { phase: '암질심 재상정', date: '2026.Q4', status: 'upcoming' },
          { phase: '약평위 심의', date: '2027.Q1', status: 'upcoming' },
        ],
      },
    ],
  },
  {
    id: 'nhis',
    label: '건강보험공단',
    description: '건강보험공단 등재 및 급여 적용',
    count: 2,
    drugs: [
      {
        id: 'd12',
        name: '키트루다',
        ingredient: '페므브롤리주맙',
        company: '한국MSD',
        indication: 'TNBC 신규 적응증',
        type: '확대',
        status: 'completed',
        submittedDate: '2025.08.01',
        approvalDate: '2025.06.10',
        updatedDate: '2026.04.01',
        notes: '약평위 통과, 2026년 4월 건강보험 등재 완료',
        history: [
          { date: '2025.06.10', committee: '식약처', result: '허가', detail: 'TNBC 1차 면역항암 병용요법 승인' },
          { date: '2025.09.22', committee: '암질심 7차', result: '설정', detail: 'KEYNOTE-522 OS 데이터로 통과' },
          { date: '2025.12.18', committee: '약평위', result: '통과', detail: 'RSA 체결, 선별급여 적용' },
          { date: '2026.04.01', committee: '건보공단', result: '등재완료', detail: '4월 1일부 급여 적용 개시' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2025.06', status: 'done' },
          { phase: '암질심 통과', date: '2025.09', status: 'done' },
          { phase: '약평위 통과', date: '2025.12', status: 'done' },
          { phase: '건보 협상', date: '2026.01~03', status: 'done' },
          { phase: '급여 등재', date: '2026.04', status: 'done' },
        ],
      },
      {
        id: 'd13',
        name: '리브살',
        ingredient: '알파리브',
        company: '한국클로비스',
        indication: 'BRCA+ 난소암 2차 이상',
        type: '신규',
        status: 'completed',
        submittedDate: '2025.05.10',
        approvalDate: '2025.02.28',
        updatedDate: '2026.03.15',
        notes: '약평위 통과, 2026년 3월 건강보험 등재 완료',
        history: [
          { date: '2025.02.28', committee: '식약처', result: '허가', detail: 'BRCA 변이 재발성 난소암 2차 이상 유지요법' },
          { date: '2025.07.16', committee: '암질심 5차', result: '설정', detail: 'SOLO-3 데이터로 통과' },
          { date: '2025.10.22', committee: '약평위', result: '통과', detail: '위험분담계약 체결' },
          { date: '2026.03.15', committee: '건보공단', result: '등재완료', detail: '3월 15일부 급여 적용 개시' },
        ],
        expectedTimeline: [
          { phase: '식약처 허가', date: '2025.02', status: 'done' },
          { phase: '암질심 통과', date: '2025.07', status: 'done' },
          { phase: '약평위 통과', date: '2025.10', status: 'done' },
          { phase: '건보 협상', date: '2025.11~2026.02', status: 'done' },
          { phase: '급여 등재', date: '2026.03', status: 'done' },
        ],
      },
    ],
  },
];

export const intelligenceReports: IntelligenceReport[] = [
  {
    id: 'r202501',
    title: '2025년 2차 암질심 사이클 전망 보고서',
    category: 'pre-cancer',
    categoryLabel: '암질심 전',
    year: 2025,
    date: '2025.02.05',
    cycle: '2025년 2차 (2월 12일)',
    summary: '2월 12일 상정 예정 3개 약제 심의 전망. 주요 신약의 임상 데이터 및 급여 기준 적합성 분석.',
    highlights: ['신규 항암제 3개 상정 예정', '임상 데이터 성숙도가 핵심 변수', '재정영향평가 선제 대응 필요'],
    downloadUrl: `${CDN_BASE}/2025_2__pre_cancer.pdf`,
    fileSize: '1.8 MB',
    pages: 10,
  },
  {
    id: 'r202502',
    title: '2025년 2차 암질심 결과 분석 보고서',
    category: 'post-cancer',
    categoryLabel: '암질심 후',
    year: 2025,
    date: '2025.02.15',
    cycle: '2025년 2차 (2월 12일)',
    summary: '2월 12일 회차 3개 심의, 1개 설정·2개 미설정. 설정률 33% 기록.',
    highlights: ['1개 설정, 2개 미설정', 'OS 데이터 미성숙 공통 이슈', '차기 회차 재도전 로드맵'],
    downloadUrl: `${CDN_BASE}/2025_2__post_cancer.pdf`,
    fileSize: '2.2 MB',
    pages: 16,
  },
  {
    id: 'r202503',
    title: '2025년 3차 암질심 사이클 전망 보고서',
    category: 'pre-cancer',
    categoryLabel: '암질심 전',
    year: 2025,
    date: '2025.03.12',
    cycle: '2025년 3차 (3월 18일)',
    summary: '3월 18일 상정 예정 4개 약제에 대한 심의 전망. 재정영향평가를 통한 BIA 분석 포함.',
    highlights: ['4개 약제 상정 예정', '재신청 약제 2개 포함', 'BIA 관리 전략 제안'],
    downloadUrl: `${CDN_BASE}/2025_3__pre_cancer.pdf`,
    fileSize: '1.9 MB',
    pages: 11,
  },
  {
    id: 'r202504',
    title: '2025년 2월 약평위 사이클 전망 보고서',
    category: 'pre-evaluation',
    categoryLabel: '약평위 전',
    year: 2025,
    date: '2025.02.10',
    cycle: '2025년 2월 2차 (2월 19일)',
    summary: '2월 약평위 상정 예정 약제에 대한 급여 평가 전망. 암질심 통과 약제의 약평위 진입 분석.',
    highlights: ['암질심 통과 약제 약평위 진입', 'RSA 구조 분석', '약가 협상 쟁점'],
    downloadUrl: `${CDN_BASE}/2025_2__pre_eval.pdf`,
    fileSize: '1.5 MB',
    pages: 8,
  },
  {
    id: 'r202505',
    title: '2025년 2월 약평위 결과 분석 보고서',
    category: 'post-evaluation',
    categoryLabel: '약평위 후',
    year: 2025,
    date: '2025.02.22',
    cycle: '2025년 2월 2차 (2월 19일)',
    summary: '2월 약평위 결과. 급여 등재 확정 약제 분석 및 차기 회차 전망.',
    highlights: ['급여 등재 확정', '선별급여 적용 건', '차기 회차 일정'],
    downloadUrl: `${CDN_BASE}/2025_2__post_eval.pdf`,
    fileSize: '2.1 MB',
    pages: 14,
  },
  {
    id: 'r202601',
    title: '2026년 4차 암질심 사이클 전망 보고서',
    category: 'pre-cancer',
    categoryLabel: '암질심 전',
    year: 2026,
    date: '2026.05.20',
    cycle: '2026년 4차 (5월 27일)',
    summary: '5월 27일 회차 상정 예정 5개 약제에 대한 심의 전망 및 핵심 쟁점 분석. 버제니오의 ESMO 2025 OS 데이터가 통과의 결정적 변수로 작용할 전망.',
    highlights: [
      '버제니오 4차 도전, OS 개선 데이터로 통과 가능성 높음',
      '엘라히어 토론회에서 보건복지부 사무관 적극 검토 의지 표명',
      '림카토·키스칼리는 장기 OS 데이터 미성숙으로 미설정 가능성',
      '알레센자 adjuvant BIA 부담이 주요 쟁점',
    ],
    downloadUrl: `${CDN_BASE}/2026_4__pre_cancer.pdf`,
    fileSize: '2.1 MB',
    pages: 12,
  },
  {
    id: 'r202602',
    title: '2026년 4차 암질심 결과 분석 보고서',
    category: 'post-cancer',
    categoryLabel: '암질심 후',
    year: 2026,
    date: '2026.05.28',
    cycle: '2026년 4차 (5월 27일)',
    summary: '5월 27일 회차에서 5개 약제 심의, 2개 설정·3개 미설정(설정률 40%). 버제니오와 엘라히어 통과. OS 데이터 성숙도가 결정 변수임이 명확히 드러남.',
    highlights: [
      '버제니오 4차 도전 만에 통과, ESMO 2025 OS 데이터 결정적',
      '엘라히어 첫 도전 통과, 정부 사전 의지 표명 패턴 재확인',
      '림카토·알레센자·키스칼리 미설정, 공통 패턴 분석',
      '7월 8일 6차 회차 임델트라 진입 가능성 가장 높음',
    ],
    downloadUrl: 'https://storage.readdy-site.link/project_files/e67f88d1-cac6-48c3-bc82-c2b4a8e96248/11451526-45d3-4a30-b8a5-806e896ea239_2026_4__.pdf',
    fileSize: '3.4 MB',
    pages: 24,
  },
  {
    id: 'r202603',
    title: '2026년 6월 약평위 사이클 전망 보고서',
    category: 'pre-evaluation',
    categoryLabel: '약평위 전',
    year: 2026,
    date: '2026.06.05',
    cycle: '2026년 6월 2차 (6월 18일)',
    summary: '6월 약평위 상정 예정 약제에 대한 급여 평가 전망. 버제니오와 엘라히어의 암질심 통과 후 약평위 진입, RSA 구조 및 약가 협상 쟁점 분석.',
    highlights: [
      '버제니오·엘라히어 약평위 상정 예정',
      '버제니오 RSA 구조 및 사용량-약가 연동 가능성',
      '엘라히어 선별급여 적용 범위 및 비급여 전환 가능성',
      '암질심 미설정 3약제의 재신청 로드맵',
    ],
    downloadUrl: `${CDN_BASE}/2026_6__pre_eval.pdf`,
    fileSize: '1.8 MB',
    pages: 10,
  },
  {
    id: 'r202604',
    title: '2026년 6월 약평위 결과 분석 보고서',
    category: 'post-evaluation',
    categoryLabel: '약평위 후',
    year: 2026,
    date: '2026.06.20',
    cycle: '2026년 6월 2차 (6월 18일)',
    summary: '6월 약평위 결과 분석. 버제니오 정상 통과 및 급여 등재 완료. 엘라히어 선별급여 적용 확정. 재신청 약제들의 보완 전략 및 차기 회차 전망.',
    highlights: [
      '버제니오 정상 급여 등재, 조기 유방암 CDK4/6 시장 개화',
      '엘라히어 선별급여 적용, FRα+ 난소암 신규 급여 옵션',
      '암질심 미설정 3약제 재신청 시 보완 자료 체크리스트',
      '7월 8일 6차 암질심 상정 예상 안건 분석',
    ],
    downloadUrl: `${CDN_BASE}/2026_6__post_eval.pdf`,
    fileSize: '2.5 MB',
    pages: 18,
  },
];

export const meetingSchedules: MeetingSchedule[] = [
  { id: 'c1', month: 1, monthLabel: '1월', type: 'cancer', typeLabel: '암질심', cycle: '1차', date: '2026.01.15', dayOfWeek: '수', isPast: true, isUpcoming: false, isToday: false, daysUntil: -147, note: '5개 심의, 1개 설정·4개 미설정' },
  { id: 'c2', month: 2, monthLabel: '2월', type: 'cancer', typeLabel: '암질심', cycle: '2차', date: '2026.02.11', dayOfWeek: '수', isPast: true, isUpcoming: false, isToday: false, daysUntil: -120, note: '3개 심의, 1개 설정·2개 미설정' },
  { id: 'c3', month: 3, monthLabel: '3월', type: 'cancer', typeLabel: '암질심', cycle: '3차', date: '2026.03.18', dayOfWeek: '수', isPast: true, isUpcoming: false, isToday: false, daysUntil: -85, note: '4개 심의, 2개 설정·2개 미설정' },
  { id: 'c4', month: 4, monthLabel: '4월', type: 'cancer', typeLabel: '암질심', cycle: '4차', date: '2026.04.15', dayOfWeek: '수', isPast: true, isUpcoming: false, isToday: false, daysUntil: -57, note: '투키사·티루캡·킴리아 FL 미설정' },
  { id: 'c5', month: 5, monthLabel: '5월', type: 'cancer', typeLabel: '암질심', cycle: '5차', date: '2026.05.27', dayOfWeek: '수', isPast: true, isUpcoming: false, isToday: false, daysUntil: -15, note: '버제니오·엘라히어 통과, 3개 미설정' },
  { id: 'c6', month: 7, monthLabel: '7월', type: 'cancer', typeLabel: '암질심', cycle: '6차', date: '2026.07.08', dayOfWeek: '수', isPast: false, isUpcoming: true, isToday: false, daysUntil: 27, note: '임델트라 진입 가능성 가장 높음' },
  { id: 'c7', month: 8, monthLabel: '8월', type: 'cancer', typeLabel: '암질심', cycle: '7차', date: '2026.08.19', dayOfWeek: '수', isPast: false, isUpcoming: true, isToday: false, daysUntil: 69, note: '다트로웨이 후보' },
  { id: 'c8', month: 9, monthLabel: '9월', type: 'cancer', typeLabel: '암질심', cycle: '8차', date: '2026.09.30', dayOfWeek: '수', isPast: false, isUpcoming: true, isToday: false, daysUntil: 111, note: '엔허투 신규 적응증 후보' },
  { id: 'c9', month: 10, monthLabel: '10월', type: 'cancer', typeLabel: '암질심', cycle: '9차', date: '2026.10.21', dayOfWeek: '수', isPast: false, isUpcoming: true, isToday: false, daysUntil: 132, note: '보라니고·가텍스주 후보' },
  { id: 'c10', month: 11, monthLabel: '11월', type: 'cancer', typeLabel: '암질심', cycle: '10차', date: '2026.11.18', dayOfWeek: '수', isPast: false, isUpcoming: true, isToday: false, daysUntil: 160, note: '연말 마무리 회차' },
  { id: 'c11', month: 12, monthLabel: '12월', type: 'cancer', typeLabel: '암질심', cycle: '11차', date: '2026.12.09', dayOfWeek: '수', isPast: false, isUpcoming: true, isToday: false, daysUntil: 181, note: '연말 최종 회차' },
  { id: 'e1', month: 1, monthLabel: '1월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.01.22', dayOfWeek: '목', isPast: true, isUpcoming: false, isToday: false, daysUntil: -140, note: '정상 진행' },
  { id: 'e2', month: 2, monthLabel: '2월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.02.19', dayOfWeek: '목', isPast: true, isUpcoming: false, isToday: false, daysUntil: -112, note: '정상 진행' },
  { id: 'e3', month: 3, monthLabel: '3월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.03.19', dayOfWeek: '목', isPast: true, isUpcoming: false, isToday: false, daysUntil: -84, note: '정상 진행' },
  { id: 'e4', month: 4, monthLabel: '4월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.04.23', dayOfWeek: '목', isPast: true, isUpcoming: false, isToday: false, daysUntil: -49, note: '정상 진행' },
  { id: 'e5', month: 5, monthLabel: '5월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.05.21', dayOfWeek: '목', isPast: true, isUpcoming: false, isToday: false, daysUntil: -21, note: '정상 진행' },
  { id: 'e6', month: 6, monthLabel: '6월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.06.18', dayOfWeek: '목', isPast: false, isUpcoming: true, isToday: false, daysUntil: 7, note: '버제니오·엘라히어 상정 예정' },
  { id: 'e7', month: 7, monthLabel: '7월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.07.23', dayOfWeek: '목', isPast: false, isUpcoming: true, isToday: false, daysUntil: 42, note: '정상 예정' },
  { id: 'e8', month: 8, monthLabel: '8월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.08.20', dayOfWeek: '목', isPast: false, isUpcoming: true, isToday: false, daysUntil: 70, note: '정상 예정' },
  { id: 'e9', month: 9, monthLabel: '9월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.09.24', dayOfWeek: '목', isPast: false, isUpcoming: true, isToday: false, daysUntil: 105, note: '정상 예정' },
  { id: 'e10', month: 10, monthLabel: '10월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.10.22', dayOfWeek: '목', isPast: false, isUpcoming: true, isToday: false, daysUntil: 133, note: '정상 예정' },
  { id: 'e11', month: 11, monthLabel: '11월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.11.19', dayOfWeek: '목', isPast: false, isUpcoming: true, isToday: false, daysUntil: 161, note: '정상 예정' },
  { id: 'e12', month: 12, monthLabel: '12월', type: 'evaluation', typeLabel: '약평위', cycle: '2차', date: '2026.12.17', dayOfWeek: '목', isPast: false, isUpcoming: true, isToday: false, daysUntil: 189, note: '연말 최종 회차' },
];

export const meetingResults: MeetingResult[] = [
  {
    id: 'mr-c1',
    meetingId: 'c1',
    title: '2026년 1차 암질환심의위원회 결과',
    date: '2026.01.15',
    type: 'cancer',
    cycle: '1차',
    totalReviewed: 5,
    approved: 1,
    rejected: 4,
    deferred: 0,
    drugs: [
      { name: '리티가정', ingredient: '리파타티닙', company: '한국BMS', indication: 'KRAS G12C 변이 NSCLC', result: 'approved', resultLabel: '설정' },
      { name: '조스파타정', ingredient: '길테리티닙', company: '한국아스텔라스', indication: 'FLT3+ AML 유지요법', result: 'rejected', resultLabel: '미설정' },
      { name: '알레센자', ingredient: '알렉티닙', company: '한국로슈', indication: 'ALK+ NSCLC 수술 후 보조', result: 'rejected', resultLabel: '미설정' },
      { name: '펨브롤리주맙 신규', ingredient: '페므브롤리주맙', company: '한국MSD', indication: '자궁경부암 1차', result: 'rejected', resultLabel: '미설정' },
      { name: '보슐리프', ingredient: '보수티닙', company: '한국화이자', indication: 'CML 1차', result: 'rejected', resultLabel: '미설정' },
    ],
    summary: '리티가정(리파타티닙)의 KRAS G12C 변이 NSCLC 2차 치료제 설정(승인). 나머지 4개 약제는 OS 데이터 미성숙 및 재정영향평가에서의 BIA 부담이 주요 미설정 사유로 작용.',
    keyTakeaways: [
      '리티가정 단독 설정, KRAS 표적치료제 최초 급여 진입 가능성',
      '조스파타정은 유지요법 개념에 대한 임상적 근거 보완 필요',
      '알레센자 adjuvant는 ALINA 임상 추가 데이터 요구',
      '펨브롤리주맙 자궁경부암은 KEYNOTE-826 장기 OS 데이터 대기',
      '보슐리프 CML 1차는 기존 치료제 대비 ICER 불리',
    ],
    nextSteps: '미설정 4약제는 3~6개월 내 보완 자료 제출 후 재심의 예정. 리티가정은 약평위(3월) 상정.',
  },
  {
    id: 'mr-c2',
    meetingId: 'c2',
    title: '2026년 2차 암질환심의위원회 결과',
    date: '2026.02.11',
    type: 'cancer',
    cycle: '2차',
    totalReviewed: 3,
    approved: 1,
    rejected: 2,
    deferred: 0,
    drugs: [
      { name: '트로델비', ingredient: '사시투주맙 고비테칸', company: '한국길리어드', indication: 'HR+/HER2- 전이성 유방암 2차', result: 'approved', resultLabel: '설정' },
      { name: '임델트라', ingredient: '탈라타맙', company: '암젠코리아', indication: 'SCLC 2차 이상', result: 'rejected', resultLabel: '미설정' },
      { name: '엔허투 HER2 저발현', ingredient: '트라스투주맙 데룩스테칸', company: '다이이치산쿄 / 한국AZ', indication: 'HER2 저발현 유방암', result: 'rejected', resultLabel: '미설정' },
    ],
    summary: '트로델비 TROPiCS-02 임상 기반 HR+/HER2- 전이성 유방암 설정(승인). 임델트라는 OS 데이터 미성숙, 엔허투 저발현은 환자군 정의 논란 및 BIA 과다로 미설정.',
    keyTakeaways: [
      '트로델비 통과, Trodelvy ADC의 급여권 진입 본격화',
      '임델트라는 7월 6차 암질심 재도전 예정',
      '엔허투 저발현은 학회 주도 재신청 + 국민청원 연계 진행',
    ],
    nextSteps: '트로델비 약평위(4월) 상정. 임델트라 6차(7월) 재심의 준비. 엔허투 저발현 5차(5월) 재심의 가능성.',
  },
  {
    id: 'mr-c3',
    meetingId: 'c3',
    title: '2026년 3차 암질환심의위원회 결과',
    date: '2026.03.18',
    type: 'cancer',
    cycle: '3차',
    totalReviewed: 4,
    approved: 2,
    rejected: 2,
    deferred: 0,
    drugs: [
      { name: '럭스터나', ingredient: '보레티진 네파보벡', company: '한국노바티스', indication: 'RPE65 변이 망막질환', result: 'approved', resultLabel: '설정' },
      { name: '킴리아 DLBCL', ingredient: '티사젠렉류셀', company: '한국노바티스', indication: 'R/R DLBCL 2차', result: 'approved', resultLabel: '설정' },
      { name: '웰리렉', ingredient: '벨주티판', company: '한국MSD', indication: 'VHL 증후군', result: 'rejected', resultLabel: '미설정' },
      { name: '엔허투 HER2 저발현', ingredient: '트라스투주맙 데룩스테칸', company: '다이이치산쿄 / 한국AZ', indication: 'HER2 저발현 유방암', result: 'rejected', resultLabel: '미설정' },
    ],
    summary: '럭스터나(희귀의약품)와 킴리아 DLBCL(CAR-T 적응증 확대) 2개 설정(승인). 웰리렉은 3차 도전에도 BIA 과다로 미설정, 엔허투 저발현은 지속적 환자군 정의 이슈로 재차 미설정.',
    keyTakeaways: [
      '럭스터나 국내 최초 유전자치료제 급여 등재 경로 개시',
      '킴리아 DLBCL 2차로 CAR-T 적응증 확대, 재정 부담 증가',
      '웰리렉은 3연속 미설정, BIA 축소 전략 필요',
      '엔허투 저발현은 2연속 미설정이나 국민청원 6만명 돌파로 정치적 관심 증가',
    ],
    nextSteps: '럭스터나·킴리아 약평위(5월) 상정. 웰리렉 4차 재신청 검토(Q3). 엔허투 저발현 5차(5월) 학회 공동 건의서 제출.',
  },
  {
    id: 'mr-c4',
    meetingId: 'c4',
    title: '2026년 4차 암질환심의위원회 결과',
    date: '2026.04.15',
    type: 'cancer',
    cycle: '4차',
    totalReviewed: 3,
    approved: 0,
    rejected: 3,
    deferred: 0,
    drugs: [
      { name: '투키사', ingredient: '투카티닙', company: '한국화이자', indication: 'HER2+ 유방암 뇌전이', result: 'rejected', resultLabel: '미설정' },
      { name: '티루캡', ingredient: '카피바설팁', company: '한국AZ', indication: 'HR+/HER2- PIK3CA 변이', result: 'rejected', resultLabel: '미설정' },
      { name: '킴리아 FL', ingredient: '티사젠렉류셀', company: '한국노바티스', indication: 'R/R 소포성 림프종', result: 'rejected', resultLabel: '미설정' },
    ],
    summary: '전면 미설정 회차. 투키사는 HER2CLIMB 추가 하위 분석 미흡, 티루캡은 바이오마커 검사 급여화 선결 과제, 킴리아 FL은 CAR-T 고비용 대비 ICER 가장 불리한 수치 기록.',
    keyTakeaways: [
      '2026년 들어 첫 전면 미설정 회차 (설정률 0%)',
      '투키사 뇌전이 적응증은 임상적 미충족 수요 높음에도 자료 부족',
      '티루캡 바이오마커 선결 과제: PIK3CA 검사 급여화가 먼저',
      '킴리아 FL ICER: 1억 8천만원/QALY — 역대 최고 수준',
    ],
    nextSteps: '3약제 모두 재신청 트랙 전환. 투키사 하반기, 티루캡 Q4, 킴리아 FL 연내 재도전 가능성 낮음.',
  },
  {
    id: 'mr-c5',
    meetingId: 'c5',
    title: '2026년 5차 암질환심의위원회 결과',
    date: '2026.05.27',
    type: 'cancer',
    cycle: '5차',
    totalReviewed: 5,
    approved: 2,
    rejected: 3,
    deferred: 0,
    drugs: [
      { name: '버제니오정', ingredient: '아베마시클립', company: '한국릴리', indication: 'HR+/HER2- 조기 유방암 보조', result: 'approved', resultLabel: '설정' },
      { name: '엘라히어주', ingredient: '미르베툭시맙 소라브탄신', company: '한국애브비', indication: 'FRα+ 재발성 난소암', result: 'approved', resultLabel: '설정' },
      { name: '림카토', ingredient: '테포티닙', company: '한국MSD', indication: 'METex14 skipping NSCLC', result: 'rejected', resultLabel: '미설정' },
      { name: '알레센자', ingredient: '알렉티닙', company: '한국로슈', indication: 'ALK+ NSCLC 수술 후 보조', result: 'rejected', resultLabel: '미설정' },
      { name: '키스칼리', ingredient: '리보시클립', company: '한국노바티스', indication: 'HR+/HER2- 전이성 유방암 1차', result: 'rejected', resultLabel: '미설정' },
    ],
    summary: '버제니오 4차 도전 만에 통과(ESMO 2025 OS 데이터 결정적), 엘라히어 첫 도전 통과(정부 사전 의지 표명). 림카토·알레센자·키스칼리는 장기 OS 미성숙 및 BIA 부담으로 미설정.',
    keyTakeaways: [
      '버제니오 4전5기 성공, ESMO 2025 전체생존율 데이터가 통과 결정타',
      '엘라히어 신속 통과, 보건복지부 토론회 사무관 발언 실질적 영향',
      '림카토 METex14 NSCLC는 환자 수 적어도 BIA 관리 신중',
      '알레센자 adjuvant는 2회차 미설정, ALINA 업데이트 데이터 대기',
      '키스칼리 1차는 CDK4/6 계열 내 경쟁 약제 대비 ICER 불리',
    ],
    nextSteps: '버제니오·엘라히어 약평위(6월 18일) 상정. 림카토·키스칼리 하반기 재심의. 알레센자 추가 임상 데이터 2027년 예상.',
  },
  {
    id: 'mr-e1',
    meetingId: 'e1',
    title: '2026년 1월 약제급여평가위원회 결과',
    date: '2026.01.22',
    type: 'evaluation',
    cycle: '2차',
    totalReviewed: 2,
    approved: 2,
    rejected: 0,
    deferred: 0,
    drugs: [
      { name: '옵디보 식도암', ingredient: '니볼루맙', company: '한국BMS', indication: '식도암 수술 후 보조', result: 'approved', resultLabel: '급여 적정' },
      { name: '타그리소', ingredient: '오시머티닙', company: '한국AZ', indication: 'EGFR+ NSCLC 수술 후 보조', result: 'approved', resultLabel: '급여 적정' },
    ],
    summary: '1월 약평위 2개 안건 모두 급여 적정 판정. 옵디보 식도암 보조요법과 타그리소 adjuvant 모두 임상적 유용성 및 경제성 평가 통과.',
    keyTakeaways: [
      '옵디보 식도암 보조요법 CheckMate-577 데이터 기반 승인',
      '타그리소 ADAURA 장기 OS 데이터로 재정영향평가 통과',
    ],
    nextSteps: '양 약제 건보공단 약가 협상 진입 (2~3월).',
  },
  {
    id: 'mr-e2',
    meetingId: 'e2',
    title: '2026년 2월 약제급여평가위원회 결과',
    date: '2026.02.19',
    type: 'evaluation',
    cycle: '2차',
    totalReviewed: 1,
    approved: 1,
    rejected: 0,
    deferred: 0,
    drugs: [
      { name: '리티가정', ingredient: '리파타티닙', company: '한국BMS', indication: 'KRAS G12C 변이 NSCLC', result: 'approved', resultLabel: '급여 적정' },
    ],
    summary: '2월 약평위 1개 안건 급여 적정 판정. 리티가정 KRAS 표적치료제로서 임상적 유용성 인정.',
    keyTakeaways: [
      '리티가정 국내 최초 KRAS G12C 표적치료제 급여 등재 경로 확보',
      '선별급여 적용, RSA 체결 예정',
    ],
    nextSteps: '건보공단 약가 협상 진입 (3~4월).',
  },
  {
    id: 'mr-e3',
    meetingId: 'e3',
    title: '2026년 3월 약제급여평가위원회 결과',
    date: '2026.03.19',
    type: 'evaluation',
    cycle: '2차',
    totalReviewed: 2,
    approved: 2,
    rejected: 0,
    deferred: 0,
    drugs: [
      { name: '트로델비', ingredient: '사시투주맙 고비테칸', company: '한국길리어드', indication: 'HR+/HER2- 전이성 유방암 2차', result: 'approved', resultLabel: '급여 적정' },
      { name: '티쎈트릭 SC', ingredient: '아테졸리주맙', company: '한국로슈', indication: '피하주사 제형 전환', result: 'approved', resultLabel: '급여 적정' },
    ],
    summary: '3월 약평위 2개 안건 모두 통과. 트로델비 Trodelvy ADC 급여권 진입, 티쎈트릭 SC 제형 변경 건.',
    keyTakeaways: [
      '트로델비 위험분담계약 체결, HR+/HER2- 유방암 신규 치료 옵션 확보',
      '티쎈트릭 SC 제형 전환으로 투약 편의성 개선 인정',
    ],
    nextSteps: '건보공단 약가 협상 진입 (4~5월).',
  },
  {
    id: 'mr-e4',
    meetingId: 'e4',
    title: '2026년 4월 약제급여평가위원회 결과',
    date: '2026.04.23',
    type: 'evaluation',
    cycle: '2차',
    totalReviewed: 2,
    approved: 2,
    rejected: 0,
    deferred: 0,
    drugs: [
      { name: '럭스터나', ingredient: '보레티진 네파보벡', company: '한국노바티스', indication: 'RPE65 변이 망막질환', result: 'approved', resultLabel: '급여 적정' },
      { name: '킴리아 DLBCL', ingredient: '티사젠렉류셀', company: '한국노바티스', indication: 'R/R DLBCL 2차', result: 'approved', resultLabel: '급여 적정' },
    ],
    summary: '4월 약평위 2개 안건 모두 통과. 럭스터나(국내 첫 유전자치료제)와 킴리아 DLBCL 2차 모두 급여 적정 판정.',
    keyTakeaways: [
      '럭스터나 성과기반 위험분담계약 체결, 투약 후 시력 개선 실측 데이터 조건부',
      '킴리아 DLBCL 2차 선별급여로 급여 범위 확대, CAR-T 접근성 제고',
    ],
    nextSteps: '건보공단 약가 협상 진입 (5~6월).',
  },
  {
    id: 'mr-e5',
    meetingId: 'e5',
    title: '2026년 5월 약제급여평가위원회 결과',
    date: '2026.05.21',
    type: 'evaluation',
    cycle: '2차',
    totalReviewed: 1,
    approved: 1,
    rejected: 0,
    deferred: 0,
    drugs: [
      { name: '런시모 SC', ingredient: '인플릭시맙', company: '한국셀트리온', indication: '바이오시밀러 피하주사 제형', result: 'approved', resultLabel: '급여 적정' },
    ],
    summary: '5월 약평위 1개 안건 급여 적정 판정. 런시모 SC 피하주사 제형 전환으로 TNF-α 억제제 접근성 개선.',
    keyTakeaways: [
      '런시모 SC 국내 바이오시밀러 최초 피하주사 제형 급여 등재',
      '환자 자가투여 가능성으로 의료자원 효율화 기여',
    ],
    nextSteps: '건보공단 약가 협상 진입 (6~7월).',
  },
];