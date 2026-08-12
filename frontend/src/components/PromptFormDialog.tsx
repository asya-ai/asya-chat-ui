import { useEffect, useState } from "react"
import { Trash2 } from "lucide-react"

import { orgApi, promptApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import type { Agent, MyTeam, Prompt, PromptSharedUser, PromptVisibility } from "@/lib/types"
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
  user_ids: string[]
  users: PromptSharedUser[]
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

const userLabel = (user: PromptSharedUser) =>
  user.display_name ? `${user.display_name} (${user.email})` : user.email

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
  const [selectedUsers, setSelectedUsers] = useState<PromptSharedUser[]>([])
  const [userQuery, setUserQuery] = useState("")
  const [userSuggestions, setUserSuggestions] = useState<PromptSharedUser[]>([])
  const [suggestionsOpen, setSuggestionsOpen] = useState(false)
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
    setSelectedUsers(initial?.users ?? [])
    setUserQuery("")
    setUserSuggestions([])
    setSuggestionsOpen(false)
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

  useEffect(() => {
    if (!open || visibility !== "users") {
      setUserSuggestions([])
      return
    }
    let cancelled = false
    const handle = window.setTimeout(() => {
      void promptApi
        .shareSuggestions(userQuery)
        .then((items) => {
          if (cancelled) return
          const selected = new Set(selectedUsers.map((user) => user.user_id))
          setUserSuggestions(items.filter((item) => !selected.has(item.user_id)))
        })
        .catch(() => {
          if (!cancelled) setUserSuggestions([])
        })
    }, 200)
    return () => {
      cancelled = true
      window.clearTimeout(handle)
    }
  }, [open, visibility, userQuery, selectedUsers])

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

  const addUser = (user: PromptSharedUser) => {
    setSelectedUsers((prev) =>
      prev.some((item) => item.user_id === user.user_id) ? prev : [...prev, user]
    )
    setUserQuery("")
    setSuggestionsOpen(false)
  }

  const removeUser = (userId: string) => {
    setSelectedUsers((prev) => prev.filter((user) => user.user_id !== userId))
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
    if (visibility === "users" && selectedUsers.length === 0) {
      setError(t("prompt_users_required"))
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
        user_ids: visibility === "users" ? selectedUsers.map((user) => user.user_id) : [],
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
                <SelectItem value="users">{t("prompt_visibility_users")}</SelectItem>
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
          {visibility === "users" ? (
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">{t("prompt_users")}</span>
              <div className="relative">
                <Input
                  placeholder={t("prompt_users_search_placeholder")}
                  value={userQuery}
                  onChange={(event) => {
                    setUserQuery(event.target.value)
                    setSuggestionsOpen(true)
                  }}
                  onFocus={() => setSuggestionsOpen(true)}
                  onBlur={() => window.setTimeout(() => setSuggestionsOpen(false), 150)}
                />
                {suggestionsOpen && userSuggestions.length > 0 ? (
                  <div className="bg-popover absolute z-50 mt-1 max-h-56 w-full overflow-y-auto rounded-md border p-1 shadow-md">
                    {userSuggestions.map((suggestion) => (
                      <button
                        key={suggestion.user_id}
                        type="button"
                        className="hover:bg-accent flex w-full flex-col items-start rounded-sm px-2 py-1.5 text-left text-sm"
                        onMouseDown={(event) => {
                          event.preventDefault()
                          addUser(suggestion)
                        }}
                      >
                        {suggestion.display_name ? (
                          <>
                            <span className="truncate">{suggestion.display_name}</span>
                            <span className="text-muted-foreground truncate text-xs">
                              {suggestion.email}
                            </span>
                          </>
                        ) : (
                          <span className="truncate">{suggestion.email}</span>
                        )}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
              {selectedUsers.length > 0 ? (
                <div className="flex flex-col gap-1 rounded-md border p-2">
                  {selectedUsers.map((user) => (
                    <div
                      key={user.user_id}
                      className="flex items-center justify-between gap-2 text-sm"
                    >
                      <span className="truncate">{userLabel(user)}</span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="text-destructive h-8 w-8 shrink-0"
                        aria-label={t("prompt_user_remove_aria")}
                        onClick={() => removeUser(user.user_id)}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden="true" />
                      </Button>
                    </div>
                  ))}
                </div>
              ) : null}
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
