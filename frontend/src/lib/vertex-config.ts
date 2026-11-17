export const VERTEX_LOCATION_OPTIONS = [
  "eu",
  "us",
  "global",
  "europe-west1",
  "europe-west4",
  "us-east5",
] as const

export function buildVertexConfigJson(input: {
  credentialsJson: string
  project: string
  location: string
}): string | undefined {
  const hasMeta = input.project.trim() !== "" || input.location.trim() !== ""
  if (!input.credentialsJson.trim() && !hasMeta) {
    return undefined
  }
  let config: Record<string, unknown> = {}
  if (input.credentialsJson.trim()) {
    config = JSON.parse(input.credentialsJson) as Record<string, unknown>
  }
  if (input.project.trim()) {
    config.project = input.project.trim()
  }
  if (input.location.trim()) {
    config.location = input.location.trim()
  }
  return JSON.stringify(config)
}