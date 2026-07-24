import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useI18n, type Locale } from "@/lib/i18n-context"

type LanguageSelectProps = {
  triggerClassName?: string
}

export const LanguageSelect = ({ triggerClassName }: LanguageSelectProps) => {
  const { locale, setLocale, t } = useI18n()

  return (
    <Select value={locale} onValueChange={(value) => setLocale(value as Locale)}>
      <SelectTrigger className={triggerClassName ?? "w-40"}>
        <SelectValue placeholder={t("language")} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="en">{t("language_en")}</SelectItem>
        <SelectItem value="lv">{t("language_lv")}</SelectItem>
        <SelectItem value="ja">{t("language_ja")}</SelectItem>
      </SelectContent>
    </Select>
  )
}
