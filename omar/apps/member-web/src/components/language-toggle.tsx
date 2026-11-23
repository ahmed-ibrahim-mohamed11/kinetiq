'use client';

import * as React from 'react';
import { Button } from '@smartgym/ui';
import { Globe } from 'lucide-react';

export function LanguageToggle() {
  const [locale, setLocale] = React.useState<'en' | 'ar'>('en');

  React.useEffect(() => {
    const saved = localStorage.getItem('kinetiq_locale') as 'en' | 'ar' | null;
    const currentLang = document.documentElement.lang || 'en';
    const active = saved || (currentLang.startsWith('ar') ? 'ar' : 'en');
    setLocale(active);
    document.documentElement.lang = active;
    document.documentElement.dir = active === 'ar' ? 'rtl' : 'ltr';
  }, []);

  const toggleLanguage = (newLocale: 'en' | 'ar') => {
    setLocale(newLocale);
    localStorage.setItem('kinetiq_locale', newLocale);
    document.documentElement.lang = newLocale;
    document.documentElement.dir = newLocale === 'ar' ? 'rtl' : 'ltr';
  };

  return (
    <div className="flex items-center gap-2">
      <Globe className="h-4 w-4 text-muted-foreground" />
      <span className="text-xs text-muted-foreground mr-1">اللغة / Language:</span>
      <Button
        variant={locale === 'en' ? 'default' : 'outline'}
        size="sm"
        onClick={() => toggleLanguage('en')}
        className="px-2.5 py-1 text-xs"
      >
        English
      </Button>
      <Button
        variant={locale === 'ar' ? 'default' : 'outline'}
        size="sm"
        onClick={() => toggleLanguage('ar')}
        className="px-2.5 py-1 text-xs font-semibold"
      >
        العربية
      </Button>
    </div>
  );
}
