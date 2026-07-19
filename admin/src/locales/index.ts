import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import zhCN from './zh-CN';
import enUS from './en-US';

const savedLang = localStorage.getItem('admin_lang') || 'zh';

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      zh: zhCN,
      en: enUS,
    },
    lng: savedLang,
    fallbackLng: 'zh',
    interpolation: {
      escapeValue: false,
    },
  });

export function setAppLanguage(lang: 'zh' | 'en') {
  i18n.changeLanguage(lang);
  localStorage.setItem('admin_lang', lang);
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
}

export function getAppLanguage(): 'zh' | 'en' {
  return (localStorage.getItem('admin_lang') as 'zh' | 'en') || 'zh';
}

export default i18n;
