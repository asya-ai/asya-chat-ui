import type { ChatModel } from "./types"

export function supportsImageInput(model: ChatModel | null | undefined): boolean {
  return model?.supports_image_input === true
}

export function supportsImageOutput(model: ChatModel | null | undefined): boolean {
  return model?.supports_image_output === true
}
