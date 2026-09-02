import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './style.css'

/** Tell the person when a deploy pulled the page out from under them.
 *
 * Every screen is a separate chunk whose filename carries a content hash, so a
 * deploy renames them. A tab that was already open then asks for a file that no
 * longer exists, the route never resolves, and what people saw was a completely
 * blank white page with no text, no error and nothing to click. Reloading is
 * all that is needed; nobody could tell that from an empty page.
 */
function showStaleVersionNotice() {
  if (document.getElementById('stale-version')) return
  const bar = document.createElement('div')
  bar.id = 'stale-version'
  bar.setAttribute('data-testid', 'stale-version')
  bar.innerHTML =
    '<span>A new version of this app has been released, so this page could not finish loading.</span>'
  const reload = document.createElement('button')
  reload.textContent = 'Reload'
  reload.setAttribute('data-testid', 'stale-version-reload')
  reload.onclick = () => location.reload()
  bar.appendChild(reload)
  document.body.appendChild(bar)
}

// Vite raises this when a lazily-loaded chunk 404s, which is exactly the
// after-a-deploy case. The router's own failures land in onError.
window.addEventListener('vite:preloadError', (e) => {
  e.preventDefault()
  showStaleVersionNotice()
})

router.onError((error) => {
  if (/dynamically imported module|Importing a module script failed|Failed to fetch/i.test(String(error))) {
    showStaleVersionNotice()
  }
})

createApp(App).use(router).mount('#app')
