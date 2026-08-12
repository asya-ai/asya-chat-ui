import { useEffect, useState } from "react"

import { orgApi, promptApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import type { Agent, MyTeam, Prompt, PromptVisibility } from "@/lib/types"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"

const PROFILE_LOCATION = "__profile__"

export type PromptFormValues = {
  name: string
  description: string
  body: string
  visibility: PromptVisibility
  team_ids: string[]
  agent_id: string | null
}

type PromptFormDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  orgId: string | null
  spaces: Agent[]
  initial?: Partial<PromptFormValues> & { id?: string }
  title: string
  description?: string
  onSaved: (prompt: Prompt) => void
}

const defaultVisibility = (
  agentId: string | null | undefined,
  visibility?: PromptVisibility
): PromptVisibility => {
  if (visibility) return visibility
  return agentId ? "space" : "private"
}

export const PromptFormDialog = ({
  open,
  onOpenChange,
  orgId,
  spaces,
  initial,
  title,
  description,
  onSaved,
}: PromptFormDialogProps) => {
  const { t } = useI18n()
  const [name, setName] = useState("")
  const [desc, setDesc] = useState("")
  const [body, setBody] = useState("")
  const [visibility, setVisibility] = useState<PromptVisibility>("private")
  const [teamIds, setTeamIds] = useState<string[]>([])
  const [location, setLocation] = useState(PROFILE_LOCATION)
  const [myTeams, setMyTeams] = useState<MyTeam[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    const agentId = initial?.agent_id ?? null
    setName(initial?.name ?? "")
    setDesc(initial?.description ?? "")
    setBody(initial?.body ?? "")
    setVisibility(defaultVisibility(agentId, initial?.visibility))
    setTeamIds(initial?.team_ids ?? [])
    setLocation(agentId ?? PROFILE_LOCATION)
    setError(null)
  }, [open, initial])

  useEffect(() => {
    if (!open || !orgId) {
      setMyTeams([])
      return
    }
    let cancelled = false
    orgApi
      .myTeams(orgId)
      .then((teams) => {
        if (!cancelled) setMyTeams(teams)
      })
      .catch(() => {
        if (!cancelled) setMyTeams([])
      })
    return () => {
      cancelled = true
    }
  }, [open, orgId])

  const editableSpaces = spaces.filter(
    (space) => space.role === "owner" || space.role === "editor"
  )
  const selectedSpace =
    location === PROFILE_LOCATION
      ? null
      : spaces.find((space) => space.id === location) ?? null

  const handleLocationChange = (value: string) => {
    setLocation(value)
    if (value === PROFILE_LOCATION) {
      if (visibility === "space") setVisibility("private")
      return
    }
    if (!initial?.id || visibility === "private" || visibility === "space") {
      setVisibility("space")
    }
  }

  const toggleTeam = (teamId: string) => {
    setTeamIds((prev) =>
      prev.includes(teamId) ? prev.filter((id) => id !== teamId) : [...prev, teamId]
    )
  }

  const handleSave = async () => {
    const trimmedName = name.trim()
    const trimmedBody = body.trim()
    if (!trimmedName) {
      setError(t("prompt_name_required"))
      return
    }
    if (!trimmedBody) {
      setError(t("prompt_body_required"))
      return
    }
    if (visibility === "team" && teamIds.length === 0) {
      setError(t("prompt_teams_required"))
      return
    }
    if (visibility === "space" && location === PROFILE_LOCATION) {
      setError(t("prompt_space_visibility_requires_space"))
      return
    }
    const agentId = location === PROFILE_LOCATION ? null : location
    try {
      setSaving(true)
      setError(null)
      const payload = {
        name: trimmedName,
        description: desc.trim() || null,
        body: trimmedBody,
        visibility,
        team_ids: visibility === "team" ? teamIds : [],
        agent_id: agentId,
      }
      const saved = initial?.id
        ? await promptApi.update(initial.id, {
            ...payload,
            clear_agent: agentId === null,
          })
        : await promptApi.create(payload)
      onSaved(saved)
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_save_failed"))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium" htmlFor="prompt-name">
              {t("prompt_name")}
            </label>
            <Input
              id="prompt-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={120}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium" htmlFor="prompt-description">
              {t("prompt_description")}
            </label>
            <Input
              id="prompt-description"
              value={desc}
              onChange={(event) => setDesc(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium" htmlFor="prompt-body">
              {t("prompt_body")}
            </label>
            <Textarea
              id="prompt-body"
              value={body}
              onChange={(event) => setBody(event.target.value)}
              rows={8}
              className="min-h-32"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">{t("prompt_location")}</label>
            <Select value={location} onValueChange={handleLocationChange}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={PROFILE_LOCATION}>
                  {t("prompt_location_profile")}
                </SelectItem>
                {editableSpaces.map((space) => (
                  <SelectItem key={space.id} value={space.id}>
                    {space.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">{t("prompt_visibility")}</label>
            <Select
              value={visibility}
              onValueChange={(value) => setVisibility(value as PromptVisibility)}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="private">{t("prompt_visibility_private")}</SelectItem>
                {selectedSpace ? (
                  <SelectItem value="space">
                    {t("prompt_visibility_space", { name: selectedSpace.name })}
                  </SelectItem>
                ) : null}
                {myTeams.length > 0 ? (
                  <SelectItem value="team">{t("prompt_visibility_team")}</SelectItem>
                ) : null}
                <SelectItem value="org">{t("prompt_visibility_org")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {visibility === "team" && myTeams.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">{t("prompt_teams")}</span>
              <div className="flex flex-col gap-1 rounded-md border p-2">
                {myTeams.map((team) => (
                  <label
                    key={team.id}
                    className="flex cursor-pointer items-center gap-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={teamIds.includes(team.id)}
                      onChange={() => toggleTeam(team.id)}
                    />
                    <span className="truncate">{team.name}</span>
                  </label>
                ))}
              </div>
            </div>
          ) : null}
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t("common_cancel")}
          </Button>
          <Button type="button" onClick={() => void handleSave()} disabled={saving}>
            {saving ? t("common_saving") : t("common_save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
