import {
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type HTMLAttributes,
  type ReactNode,
  type SyntheticEvent,
} from "react"
import { useNavigate } from "@tanstack/react-router"
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core"
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { FolderOpen, GripVertical, Plus, Search } from "lucide-react"

import { useI18n } from "@/lib/i18n-context"
import { orgStore } from "@/lib/storage"
import type { Agent } from "@/lib/types"
import { useAgents, useCreateAgent, useUpdateAgentOrder } from "@/hooks/use-chat-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Card } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { AppShell } from "@/components/AppShell"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

type SortableCardProps = {
  setNodeRef: (node: HTMLElement | null) => void
  style: CSSProperties
  attributes: HTMLAttributes<HTMLElement>
  listeners: Record<string, (event: SyntheticEvent) => void> | undefined
}

const SortableProjectCard = ({
  id,
  children,
}: {
  id: string
  children: (props: SortableCardProps) => ReactNode
}) => {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id,
  })
  const style: CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 10 : undefined,
    opacity: isDragging ? 0.8 : undefined,
  }
  return (
    <>
      {children({
        setNodeRef,
        style,
        attributes: attributes as HTMLAttributes<HTMLElement>,
        listeners: listeners as SortableCardProps["listeners"],
      })}
    </>
  )
}

const ProjectCardBody = ({
  project,
  showHandle,
  listeners,
}: {
  project: Agent
  showHandle: boolean
  listeners?: SortableCardProps["listeners"]
}) => {
  const { t } = useI18n()
  return (
    <>
      <div className="flex items-center gap-2">
        {showHandle ? (
          <button
            type="button"
            className="touch-none cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground shrink-0"
            aria-label={t("project_reorder")}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
            {...listeners}
          >
            <GripVertical aria-hidden="true" className="h-4 w-4" />
          </button>
        ) : null}
        <FolderOpen className="text-muted-foreground h-5 w-5 shrink-0" aria-hidden="true" />
        <p className="min-w-0 flex-1 truncate font-medium">{project.name}</p>
      </div>
      {project.description ? (
        <p className="text-muted-foreground line-clamp-2 text-sm">{project.description}</p>
      ) : (
        <p className="text-muted-foreground/70 text-sm italic">{t("project_no_description")}</p>
      )}
    </>
  )
}

export const ProjectsPage = () => {
  const navigate = useNavigate()
  const { t } = useI18n()
  const orgId = orgStore.get()
  const { data: projects = [], isLoading, error: loadError } = useAgents(orgId)
  const createAgentMutation = useCreateAgent(orgId)
  const updateAgentOrderMutation = useUpdateAgentOrder(orgId)
  const [query, setQuery] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [hasNewName, setHasNewName] = useState(false)
  const newNameRef = useRef<HTMLInputElement | null>(null)
  const newInstructionsRef = useRef<HTMLTextAreaElement | null>(null)
  const canReorder = query.trim().length === 0

  const projectSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

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
    const name = newNameRef.current?.value.trim() ?? ""
    if (!name) {
      setError(t("project_name_required"))
      return
    }
    try {
      setError(null)
      const created = await createAgentMutation.mutateAsync({
        name,
        master_prompt: newInstructionsRef.current?.value.trim() || null,
      })
      setCreateOpen(false)
      setHasNewName(false)
      navigate({ href: `/projects/${created.id}` })
    } catch (err) {
      setError(err instanceof Error ? err.message : t("project_create_failed"))
    }
  }

  const handleProjectDragEnd = (event: DragEndEvent) => {
    if (!canReorder) return
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = projects.findIndex((project) => project.id === active.id)
    const newIndex = projects.findIndex((project) => project.id === over.id)
    if (oldIndex < 0 || newIndex < 0) return
    void updateAgentOrderMutation.mutateAsync(arrayMove(projects, oldIndex, newIndex))
  }

  const openProject = (projectId: string) => {
    navigate({ href: `/projects/${projectId}` })
  }

  const displayError =
    error ?? (loadError instanceof Error ? loadError.message : loadError ? t("project_load_failed") : null)

  return (
    <AppShell activeSection="projects">
      {(sidebarControls) => (
    <div className="mx-auto flex h-full w-full max-w-6xl flex-col gap-6 overflow-y-auto p-4 sm:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1">{sidebarControls}</div>
          <FolderOpen className="size-6" aria-hidden="true" />
          <h1 className="font-heading text-4xl font-normal leading-10">{t("project_title")}</h1>
        </div>
        <div className="flex items-center gap-2">
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

      {displayError ? (
        <Alert variant="destructive">
          <AlertDescription>{displayError}</AlertDescription>
        </Alert>
      ) : null}

      {isLoading ? (
        <p className="text-muted-foreground text-sm">{t("project_loading")}</p>
      ) : filtered.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 rounded-[var(--radius-card)] border border-dashed bg-card py-20 text-center">
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
      ) : canReorder ? (
        <DndContext
          sensors={projectSensors}
          collisionDetection={closestCenter}
          onDragEnd={handleProjectDragEnd}
        >
          <SortableContext
            items={projects.map((project) => project.id)}
            strategy={rectSortingStrategy}
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {projects.map((project) => (
                <SortableProjectCard key={project.id} id={project.id}>
                  {({ setNodeRef, style, attributes, listeners }) => (
                    <Card
                      ref={setNodeRef}
                      style={style}
                      role="button"
                      tabIndex={0}
                      onClick={() => openProject(project.id)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault()
                          openProject(project.id)
                        }
                      }}
                      className="cursor-pointer gap-2 p-4 transition-colors hover:border-primary/40 hover:bg-accent"
                      {...attributes}
                    >
                      <ProjectCardBody project={project} showHandle listeners={listeners} />
                    </Card>
                  )}
                </SortableProjectCard>
              ))}
            </div>
          </SortableContext>
        </DndContext>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((project) => (
            <Card
              key={project.id}
              role="button"
              tabIndex={0}
              onClick={() => openProject(project.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault()
                  openProject(project.id)
                }
              }}
              className="cursor-pointer gap-2 p-4 transition-colors hover:border-primary/40 hover:bg-accent"
            >
              <ProjectCardBody project={project} showHandle={false} />
            </Card>
          ))}
        </div>
      )}

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open)
          if (!open) setHasNewName(false)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("project_create_title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              key={String(createOpen)}
              ref={newNameRef}
              autoFocus
              placeholder={t("project_name_placeholder")}
              defaultValue=""
              onChange={(event) => {
                const next = event.target.value.trim().length > 0
                setHasNewName((prev) => (prev === next ? prev : next))
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault()
                  void handleCreate()
                }
              }}
            />
            <Textarea
              key={`instr-${String(createOpen)}`}
              ref={newInstructionsRef}
              rows={4}
              placeholder={t("project_instructions_placeholder")}
              defaultValue=""
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t("common_cancel")}
            </Button>
            <Button
              onClick={handleCreate}
              disabled={createAgentMutation.isPending || !hasNewName}
            >
              {t("project_create_action")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
      )}
    </AppShell>
  )
}

export default ProjectsPage
