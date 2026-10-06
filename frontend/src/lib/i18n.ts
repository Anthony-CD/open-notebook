import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { resources } from './locales'

export const SUPPORTED_LANGUAGES = Object.keys(resources)
export const DEFAULT_LANGUAGE = 'en-US'

/**
 * Map any detected/saved language tag onto one of the locales we ship.
 *
 * Browsers report tags like `es`, `es-AR` or `es-419`, and older saved
 * values may be bare (`es`). Our resources only exist under full codes
 * (`es-ES`), so without this mapping i18next would look up `es-AR` → `es`
 * → fallback and render English for every Spanish-speaking user.
 */
export function normalizeLanguage(lng: string | null | undefined): string {
  if (!lng) return DEFAULT_LANGUAGE
  const tag = lng.replace('_', '-')
  const exact = SUPPORTED_LANGUAGES.find(code => code.toLowerCase() === tag.toLowerCase())
  if (exact) return exact

  const lower = tag.toLowerCase()
  if (lower.startsWith('zh')) {
    // Traditional script / regions → zh-TW, everything else → zh-CN
    return /hant|-tw|-hk|-mo/.test(lower) ? 'zh-TW' : 'zh-CN'
  }

  const base = lower.split('-')[0]
  const byBase = SUPPORTED_LANGUAGES.find(code => code.toLowerCase().split('-')[0] === base)
  return byBase ?? DEFAULT_LANGUAGE
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: DEFAULT_LANGUAGE,
    supportedLngs: SUPPORTED_LANGUAGES,
    interpolation: {
      escapeValue: false, // react already safes from xss
    },
    react: {
      useSuspense: false,
    },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      convertDetectedLanguage: normalizeLanguage,
    },
  })

/** Keep <html lang> in sync with the active language (a11y, spellcheck, fonts). */
export function syncDocumentLanguage(lng: string = i18n.language) {
  if (typeof document !== 'undefined' && lng) {
    document.documentElement.lang = lng
  }
}

i18n.on('languageChanged', syncDocumentLanguage)

export default i18n
