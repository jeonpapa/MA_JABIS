import { StrictMode } from 'react'
import './i18n'
import { initUiLang } from './i18n/uiTranslate'
import { createRoot } from 'react-dom/client'
import './index.css'
import './styles/apple-theme.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// UI 언어 토글(영어 표시) — 저장된 선택을 첫 화면부터 적용
initUiLang();
