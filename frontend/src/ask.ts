import { ref } from 'vue'

export interface AskSpec {
  title: string
  message?: string
  inputLabel?: string
  confirmLabel?: string
  danger?: boolean
}

/** The app's own confirm/prompt, in one place.
 *
 * Keeps the contract of the native dialogs it replaces: cancelling resolves to
 * `null`, confirming resolves to the text (possibly an empty string). Null and
 * empty are different answers whenever the input is optional.
 */
export function useAsk() {
  const ask = ref<(AskSpec & { resolve: (v: string | null) => void }) | null>(null)

  function askUser(spec: AskSpec): Promise<string | null> {
    return new Promise((resolve) => {
      ask.value = { ...spec, resolve }
    })
  }

  function answerAsk(value: string | null) {
    const pending = ask.value
    ask.value = null
    pending?.resolve(value)
  }

  return { ask, askUser, answerAsk }
}
