import { useEffect, useState } from "react"
import { useNavigate } from "@tanstack/react-router"

import { authApi, orgApi } from "@/lib/api"
import { orgStore } from "@/lib/storage"
import { useI18n } from "@/lib/i18n-context"
import { SettingsShell } from "@/components/SettingsShell"
import {
  SettingsEmptyState,
  SettingsListItem,
  SettingsPage,
  SettingsSection,
} from "@/components/settings/SettingsPanel"
import type { Org, OrgMember, Team, TeamMember, TeamModel } from "@/lib/types"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"

const OIDC_NONE = "__none__"

export const TeamsPage = () => {
  const navigate = useNavigate()
  const { t } = useI18n()
  const [authChecked, setAuthChecked] = useState(false)
  const [isAdmin, setIsAdmin] = useState(false)
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [orgs, setOrgs] = useState<Org[]>([])
  const [selectedOrg, setSelectedOrg] = useState<string | null>(orgStore.get())
  const [teams, setTeams] = useState<Team[]>([])
  const [members, setMembers] = useState<OrgMember[]>([])
  const [oidcGroups, setOidcGroups] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [newTeamName, setNewTeamName] = useState("")
  const [newTeamGroup, setNewTeamGroup] = useState(OIDC_NONE)
  const [editingTeam, setEditingTeam] = useState<Team | null>(null)
  const [editName, setEditName] = useState("")
  const [editGroup, setEditGroup] = useState(OIDC_NONE)
  const [teamModels, setTeamModels] = useState<TeamModel[]>([])
  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([])
  const [selectedMemberIds, setSelectedMemberIds] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setIsSuperAdmin(me.is_super_admin)
        setIsAdmin(me.is_admin)
        setAuthChecked(true)
      })
      .catch(() => {
        setIsSuperAdmin(false)
        setIsAdmin(false)
        setAuthChecked(true)
      })
  }, [])

  useEffect(() => {
    if (!authChecked) return
    if (!isSuperAdmin && !isAdmin) {
      navigate({ to: "/settings/me" })
    }
  }, [authChecked, isAdmin, isSuperAdmin, navigate])

  useEffect(() => {
    if (!authChecked || (!isAdmin && !isSuperAdmin)) return
    orgApi
      .list()
      .then((list) => {
        setOrgs(list)
        if (!selectedOrg && list[0]) {
          setSelectedOrg(list[0].id)
          orgStore.set(list[0].id)
        }
      })
      .catch(() => setOrgs([]))
  }, [authChecked, isAdmin, isSuperAdmin, selectedOrg])

  const loadTeams = async (orgId: string) => {
    const [teamList, memberList, groups] = await Promise.all([
      orgApi.teams(orgId),
      orgApi.members(orgId),
      orgApi.oidcGroups(orgId),
    ])
    setTeams(teamList)
    setMembers(memberList)
    setOidcGroups(groups)
  }

  useEffect(() => {
    if (!selectedOrg || (!isAdmin && !isSuperAdmin)) return
    loadTeams(selectedOrg).catch((err) =>
      setError(err instanceof Error ? err.message : t("common_error"))
    )
  }, [selectedOrg, isAdmin, isSuperAdmin, t])

  const selectOrg = (orgId: string) => {
    setSelectedOrg(orgId)
    orgStore.set(orgId)
    setEditingTeam(null)
    setNewTeamGroup(OIDC_NONE)
  }

  const groupValue = (value: string) => (value === OIDC_NONE ? null : value)

  const createTeam = async () => {
    if (!selectedOrg || !newTeamName.trim()) return
    setError(null)
    try {
      await orgApi.createTeam(selectedOrg, {
        name: newTeamName.trim(),
        oidc_group: groupValue(newTeamGroup),
      })
      setNewTeamName("")
      setNewTeamGroup(OIDC_NONE)
      await loadTeams(selectedOrg)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const openEdit = async (team: Team) => {
    if (!selectedOrg) return
    setError(null)
    setEditingTeam(team)
    setEditName(team.name)
    setEditGroup(team.oidc_group ?? OIDC_NONE)
    try {
      const [models, currentMembers, groups] = await Promise.all([
        orgApi.teamModels(selectedOrg, team.id),
        team.is_default
          ? Promise.resolve([] as TeamMember[])
          : orgApi.teamMembers(selectedOrg, team.id),
        orgApi.oidcGroups(selectedOrg),
      ])
      setTeamModels(models)
      setTeamMembers(currentMembers)
      setSelectedMemberIds(new Set(currentMembers.map((m) => m.user_id)))
      setOidcGroups(groups)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const saveTeam = async () => {
    if (!selectedOrg || !editingTeam) return
    setSaving(true)
    setError(null)
    try {
      await orgApi.updateTeam(selectedOrg, editingTeam.id, {
        name: editName.trim(),
        oidc_group: editingTeam.is_default ? null : groupValue(editGroup),
      })
      await orgApi.setTeamModels(
        selectedOrg,
        editingTeam.id,
        teamModels.map((model) => ({
          model_id: model.model_id,
          is_enabled: model.is_enabled,
        }))
      )
      if (!editingTeam.is_default) {
        await orgApi.setTeamMembers(selectedOrg, editingTeam.id, [
          ...selectedMemberIds,
        ])
      }
      await loadTeams(selectedOrg)
      setEditingTeam(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setSaving(false)
    }
  }

  const deleteTeam = async (team: Team) => {
    if (!selectedOrg || team.is_default) return
    if (!window.confirm(t("org_teams_delete_confirm"))) return
    setError(null)
    try {
      await orgApi.deleteTeam(selectedOrg, team.id)
      if (editingTeam?.id === team.id) setEditingTeam(null)
      await loadTeams(selectedOrg)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const toggleModel = (modelId: string, enabled: boolean) => {
    setTeamModels((prev) =>
      prev.map((model) =>
        model.model_id === modelId ? { ...model, is_enabled: enabled } : model
      )
    )
  }

  const toggleMember = (userId: string, checked: boolean) => {
    setSelectedMemberIds((prev) => {
      const next = new Set(prev)
      if (checked) next.add(userId)
      else next.delete(userId)
      return next
    })
  }

  if (!authChecked) return null
  if (!isSuperAdmin && !isAdmin) return null

  const groupOptions = Array.from(
    new Set([
      ...oidcGroups,
      ...(newTeamGroup !== OIDC_NONE ? [newTeamGroup] : []),
      ...(editGroup !== OIDC_NONE ? [editGroup] : []),
    ])
  ).sort((a, b) => a.localeCompare(b))

  return (
    <SettingsShell
      title={t("org_section_teams")}
      actions={
        isSuperAdmin ? (
          <Select value={selectedOrg ?? ""} onValueChange={selectOrg}>
            <SelectTrigger className="w-64">
              <SelectValue placeholder={t("org_select_placeholder")} />
            </SelectTrigger>
            <SelectContent>
              {orgs.map((org) => (
                <SelectItem key={org.id} value={org.id}>
                  {org.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null
      }
    >
        <SettingsPage wide>
          <div className="space-y-6">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        <SettingsSection title={t("org_teams_create")}>
          <div className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:px-5">
            <Input
              placeholder={t("org_teams_name")}
              value={newTeamName}
              onChange={(event) => setNewTeamName(event.target.value)}
              className="sm:flex-1"
            />
            <Select value={newTeamGroup} onValueChange={setNewTeamGroup}>
              <SelectTrigger className="sm:w-64">
                <SelectValue placeholder={t("org_teams_oidc_group")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={OIDC_NONE}>{t("org_teams_oidc_none")}</SelectItem>
                {groupOptions.map((group) => (
                  <SelectItem key={group} value={group}>
                    {group}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button onClick={createTeam} disabled={!newTeamName.trim() || !selectedOrg}>
              {t("org_teams_create")}
            </Button>
          </div>
        </SettingsSection>

        <SettingsSection>
          {teams.length === 0 ? (
            <SettingsEmptyState title={t("org_teams_no_teams")} />
          ) : (
            teams.map((team) => (
              <SettingsListItem
                key={team.id}
                title={
                  team.is_default
                    ? `${team.name} (${t("org_teams_default_badge")})`
                    : team.name
                }
                subtitle={[
                  `${t("org_teams_models")}: ${team.model_count}`,
                  !team.is_default ? `${t("org_teams_members")}: ${team.member_count}` : null,
                  team.oidc_group ? `${t("org_teams_oidc_group")}: ${team.oidc_group}` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
                actions={
                  <>
                    <Button variant="outline" size="sm" onClick={() => openEdit(team)}>
                      {t("org_teams_edit")}
                    </Button>
                    {!team.is_default ? (
                      <Button variant="outline" size="sm" onClick={() => deleteTeam(team)}>
                        {t("common_delete")}
                      </Button>
                    ) : null}
                  </>
                }
              />
            ))
          )}
        </SettingsSection>
      </div>

      <Dialog open={Boolean(editingTeam)} onOpenChange={(open) => !open && setEditingTeam(null)}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("org_teams_edit")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("org_teams_name")}</label>
              <Input value={editName} onChange={(event) => setEditName(event.target.value)} />
            </div>
            {!editingTeam?.is_default ? (
              <div className="space-y-2">
                <label className="text-sm font-medium">{t("org_teams_oidc_group")}</label>
                <Select value={editGroup} onValueChange={setEditGroup}>
                  <SelectTrigger>
                    <SelectValue placeholder={t("org_teams_oidc_group")} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={OIDC_NONE}>{t("org_teams_oidc_none")}</SelectItem>
                    {groupOptions.map((group) => (
                      <SelectItem key={group} value={group}>
                        {group}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}

            <div className="space-y-2">
              <p className="text-sm font-medium">{t("org_teams_models")}</p>
              {teamModels.map((model) => (
                <div
                  key={model.model_id}
                  className="flex items-center justify-between gap-3 rounded-md border px-3 py-2"
                >
                  <div>
                    <p className="text-sm">{model.display_name}</p>
                    <p className="text-muted-foreground text-xs">
                      {model.provider} / {model.model_name}
                    </p>
                  </div>
                  <Switch
                    checked={model.is_enabled}
                    onCheckedChange={(checked) => toggleModel(model.model_id, checked)}
                  />
                </div>
              ))}
              {teamModels.length === 0 ? (
                <p className="text-muted-foreground text-sm">{t("org_teams_no_teams")}</p>
              ) : null}
            </div>

            {!editingTeam?.is_default ? (
              <div className="space-y-2">
                <p className="text-sm font-medium">{t("org_teams_members")}</p>
                {members.map((member) => {
                  const existing = teamMembers.find((m) => m.user_id === member.user_id)
                  const checked = selectedMemberIds.has(member.user_id)
                  return (
                    <label
                      key={member.user_id}
                      className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
                    >
                      <span>
                        {member.email}
                        {existing ? (
                          <span className="text-muted-foreground ml-2 text-xs">
                            (
                            {existing.source === "oidc"
                              ? t("org_teams_source_oidc")
                              : t("org_teams_source_manual")}
                            )
                          </span>
                        ) : null}
                      </span>
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={existing?.source === "oidc" && checked}
                        onChange={(event) =>
                          toggleMember(member.user_id, event.target.checked)
                        }
                      />
                    </label>
                  )
                })}
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditingTeam(null)}>
              {t("chat_cancel")}
            </Button>
            <Button onClick={saveTeam} disabled={saving || !editName.trim()}>
              {t("org_teams_save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
        </SettingsPage>
    </SettingsShell>
  )
}
