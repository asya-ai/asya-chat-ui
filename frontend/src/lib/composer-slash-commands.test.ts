import { describe, expect, it } from "vitest"
import { filterSlashCommands, type ComposerSlashCommand } from "./composer-slash-commands"

const commands: ComposerSlashCommand[] = [
  {
    id: "cowork",
    name: "cowork",
    description: "Co-editing document",
    insertText: "Create a document",
  },
  {
    id: "search",
    name: "search",
    description: "Web search",
    keywords: ["web"],
    insertText: "Search for",
  },
]

describe("filterSlashCommands", () => {
  it("returns all commands for empty query", () => {
    expect(filterSlashCommands(commands, "")).toHaveLength(2)
  })

  it("filters by command name", () => {
    expect(filterSlashCommands(commands, "cow")).toEqual([commands[0]])
  })

  it("filters by keyword", () => {
    expect(filterSlashCommands(commands, "web")).toEqual([commands[1]])
  })
})
