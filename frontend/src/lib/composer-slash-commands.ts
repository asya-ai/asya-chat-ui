export type ComposerSlashCommand = {
  id: string
  name: string
  description: string
  keywords?: string[]
  insertText: string
  onSelect?: () => void
}

export const filterSlashCommands = (
  commands: ComposerSlashCommand[],
  query: string
): ComposerSlashCommand[] => {
  const needle = query.trim().toLowerCase()
  if (!needle) return commands
  return commands.filter((command) => {
    if (command.name.toLowerCase().includes(needle)) return true
    if (command.description.toLowerCase().includes(needle)) return true
    return command.keywords?.some((keyword) => keyword.toLowerCase().includes(needle)) ?? false
  })
}
