import { nextTick, onBeforeUnmount, onMounted, type Ref } from 'vue'

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),' +
  'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

/** Make a modal usable without a mouse.
 *
 * Opening one left focus on the button behind it, Tab walked out into the
 * dimmed page after five presses — reaching other items' buttons, the sidebar
 * and the search box, all of them unreachable-looking but live — and Escape did
 * nothing. A keyboard-only person could open a dialog and then not get back to
 * it or out of it.
 *
 * The listener is on the document in capture phase so it works wherever focus
 * has wandered to, including out of the dialog entirely.
 */
export function useDialog(root: Ref<HTMLElement | null>, close: () => void) {
  function focusable(): HTMLElement[] {
    if (!root.value) return []
    return Array.from(root.value.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
      (el) => el.offsetParent !== null || el === document.activeElement,
    )
  }

  function onKeydown(e: KeyboardEvent) {
    if (!root.value) return
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      close()
      return
    }
    if (e.key !== 'Tab') return
    const items = focusable()
    if (!items.length) return
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement as HTMLElement | null
    const inside = !!active && root.value.contains(active)
    if (e.shiftKey && (!inside || active === first)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && (!inside || active === last)) {
      e.preventDefault()
      first.focus()
    }
  }

  onMounted(async () => {
    await nextTick()
    focusable()[0]?.focus()
    document.addEventListener('keydown', onKeydown, true)
  })
  onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown, true))
}
