import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { FolderOpen, Plus, Search } from "lucide-react"

import { agentApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import type { Agent } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Card } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

export const ProjectsPage = () => {
  const navigate = useNavigate()
  const { t } = useI18n()
  const [projects, setProjects] = useState<Agent[]>([])
  const [query, setQuery] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState("")
  const [newInstructions, setNewInstructions] = useState("")

  useEffect(() => {
    void (async () => {
      try {
        setLoading(true)
        setError(null)
        setProjects(await agentApi.list())
      } catch (err) {
        setError(err instanceof Error ? err.message : t("project_load_failed"))
      } finally {
        setLoading(false)
      }
    })()
  }, [t])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return projects
    return projects.filter(
      (project) =>
        project.name.toLowerCase().includes(needle) ||
        (project.description ?? "").toLowerCase().includes(needle)
    )
  }, [projects, query])

  const handleCreate = async () => {
    const name = newName.trim()
    if (!name) {
      setError(t("project_name_required"))
      return
    }
    try {
      setCreating(true)
      setError(null)
      const created = await agentApi.create({
        name,
        master_prompt: newInstructions.trim() || null,
      })
      setCreateOpen(false)
      setNewName("")
      setNewInstructions("")
      navigate(`/projects/${created.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("project_create_failed"))
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="mx-auto flex h-svh w-full max-w-5xl flex-col gap-6 overflow-y-auto px-4 py-8 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <FolderOpen className="h-6 w-6" aria-hidden="true" />
          <h1 className="text-2xl font-semibold">{t("project_title")}</h1>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => navigate("/chat")}>
            {t("common_back_to_chat")}
          </Button>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            {t("project_new")}
          </Button>
        </div>
      </header>

      <p className="text-muted-foreground text-sm">{t("project_description")}</p>

      <div className="relative">
        <Search
          className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
          aria-hidden="true"
        />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("project_search_placeholder")}
          className="pl-9"
          aria-label={t("project_search_aria")}
        />
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {loading ? (
        <p className="text-muted-foreground text-sm">{t("project_loading")}</p>
      ) : filtered.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 rounded-xl border border-dashed py-20 text-center">
          <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-2xl">
            <FolderOpen className="text-muted-foreground h-6 w-6" aria-hidden="true" />
          </div>
          <div className="space-y-1">
            <p className="font-medium">
              {projects.length === 0
                ? t("project_empty_title")
                : t("project_empty_search_title")}
            </p>
            <p className="text-muted-foreground text-sm">
              {projects.length === 0
                ? t("project_empty_desc")
                : t("project_empty_search_desc")}
            </p>
          </div>
          {projects.length === 0 ? (
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              {t("project_new")}
            </Button>
          ) : null}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((project) => (
            <Card
              key={project.id}
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/projects/${project.id}`)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault()
                  navigate(`/projects/${project.id}`)
                }
              }}
              className="hover:border-primary/60 hover:bg-accent/40 cursor-pointer gap-2 p-4 transition-colors"
            >
              <div className="flex items-center gap-2">
                <FolderOpen className="text-muted-foreground h-5 w-5 shrink-0" aria-hidden="true" />
                <p className="min-w-0 flex-1 truncate font-medium">{project.name}</p>
              </div>
              {project.description ? (
                <p className="text-muted-foreground line-clamp-2 text-sm">{project.description}</p>
              ) : (
                <p className="text-muted-foreground/70 text-sm italic">
                  {t("project_no_description")}
                </p>
              )}
            </Card>
          ))}
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("project_create_title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              autoFocus
              placeholder={t("project_name_placeholder")}
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault()
                  void handleCreate()
                }
              }}
            />
            <Textarea
              rows={4}
              placeholder={t("project_instructions_placeholder")}
              value={newInstructions}
              onChange={(event) => setNewInstructions(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t("common_cancel")}
            </Button>
            <Button onClick={handleCreate} disabled={creating || !newName.trim()}>
              {t("project_create_action")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default ProjectsPage
