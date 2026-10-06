<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { Graph3D, type GraphEdge3D, type GraphNode3D } from '../graph3d'
import { store } from '../store'

interface ConceptRow {
  id: string
  name: string
  mentions: number
}
interface LocalNode {
  id: string
  type: string
  label: string
  sublabel?: string
  center?: boolean
}
interface LinkEdge {
  source: string
  target: string
  label: string
  style: string
  link_id: string | null
  directed?: boolean
}
interface SourceNode {
  id: string
  type: 'item' | 'question' | 'document'
  label: string
  kind?: string
  status?: string | null
}
interface GlobalGraph {
  clusters: { id: string; label: string; concepts: { id: string; name: string; size: number }[] }[]
  edges: LinkEdge[]
  total_nodes: number
  total_edges: number
  omitted_nodes: number
  omitted_edges: number
  sources: SourceNode[]
  source_edges: { source: string; concept_id: string }[]
  omitted_sources: number
}
/** A node as the legend, filter and explorer describe it. */
interface Entry {
  id: string
  group: Group
  name: string
  description: string
}
type Group = 'concept' | 'open' | 'answered' | 'item' | 'document'

const GROUPS: Record<Group, { label: string; color: string }> = {
  concept: { label: 'Concept', color: '#3fa9f5' },
  open: { label: 'Open question', color: '#ff5d6c' },
  answered: { label: 'Answered question', color: '#3ccf7a' },
  item: { label: 'Post', color: '#c792ea' },
  document: { label: 'Document', color: '#f5b942' },
}

const props = defineProps<{ focusConceptIds: string[] }>()
const emit = defineEmits<{ openItem: [string]; openQuestion: [string]; evidence: [string] }>()

const router = useRouter()
const concepts = ref<ConceptRow[]>([])
const mode = ref<'full' | 'focused'>('full')
const focusIds = ref<string[]>([])
const focusNames = ref<string[]>([])
const showWeak = ref(false)
const omittedNodes = ref(0)
const omittedEdges = ref(0)
const omittedSources = ref(0)
const totalEdges = ref(0)
const entries = ref<Entry[]>([])
const groupFilter = ref<'all' | Group>('all')
const selectedId = ref<string | null>(null)
const preview = ref<{ entry: Entry; x: number; y: number } | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)
const explorerEl = ref<HTMLElement | null>(null)
let graph: Graph3D | null = null
let requestId = 0

const trim = (t: string, n: number) => (t.length > n ? t.slice(0, n).trimEnd() + '…' : t)
const lineStyle = (style: string): GraphEdge3D['style'] => (style === 'dashed' || style === 'dotted' ? style : 'solid')
const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`

const byId = computed(() => new Map(entries.value.map((entry) => [entry.id, entry])))
const presentGroups = computed(() => (Object.keys(GROUPS) as Group[]).filter((group) => entries.value.some((e) => e.group === group)))
const shownEntries = computed(() =>
  entries.value
    .filter((entry) => groupFilter.value === 'all' || entry.group === groupFilter.value)
    .sort((a, b) => presentGroups.value.indexOf(a.group) - presentGroups.value.indexOf(b.group) || a.name.localeCompare(b.name)),
)

function sourceEntry(source: { id: string; type: string; label: string; kind?: string; status?: string | null }): Entry {
  if (source.type === 'question') {
    const open = !source.status || source.status === 'open'
    return { id: source.id, group: open ? 'open' : 'answered', name: source.label,
      description: open ? 'Open question' : `Question · ${source.status}` }
  }
  if (source.type === 'document') return { id: source.id, group: 'document', name: source.label, description: 'Team document' }
  const kind = source.kind ? source.kind[0].toUpperCase() + source.kind.slice(1) : 'Post'
  return { id: source.id, group: 'item', name: source.label, description: `${kind} shared with the team` }
}

function show(nextEntries: Entry[], nodes: GraphNode3D[], edges: GraphEdge3D[], fit: boolean) {
  entries.value = nextEntries
  if (selectedId.value && !nextEntries.some((entry) => entry.id === selectedId.value)) selectedId.value = null
  graph?.setData(nodes, edges, fit)
  graph?.select(selectedId.value)
  applyFilter()
}

function node(entry: Entry, size: number, labelled: boolean): GraphNode3D {
  return { id: entry.id, label: trim(entry.name, entry.group === 'concept' ? 42 : 30), color: GROUPS[entry.group].color, size, labelled }
}

async function loadFull(preserveView = false) {
  const currentRequest = ++requestId
  mode.value = 'full'
  let data: GlobalGraph
  try {
    data = await api.get<GlobalGraph>(`/api/graph/global?show_weak=${showWeak.value}`)
  } catch (e) {
    return void store.fail(e, 'Could not load the knowledge graph')
  }
  if (currentRequest !== requestId) return
  omittedNodes.value = data.omitted_nodes
  omittedEdges.value = data.omitted_edges
  omittedSources.value = data.omitted_sources
  totalEdges.value = data.total_edges
  const conceptEntries: Entry[] = data.clusters.flatMap((cluster) =>
    cluster.concepts.map((concept) => ({ id: `c:${concept.id}`, group: 'concept' as const, name: concept.name,
      description: concept.size ? `Mentioned in ${plural(concept.size, 'post')}` : 'Not mentioned yet' })),
  )
  const sizes = new Map<string, number>(data.clusters.flatMap((cluster) => cluster.concepts.map((c) => [`c:${c.id}`, c.size] as const)))
  const sourceEntries = data.sources.map(sourceEntry)
  show(
    [...conceptEntries, ...sourceEntries],
    [
      ...conceptEntries.map((entry) => node(entry, Math.min(12, 6 + (sizes.get(entry.id) ?? 0)), true)),
      // Posts are sentences: label them on hover or selection, so concept names stay readable.
      ...sourceEntries.map((entry) => node(entry, 4.5, false)),
    ],
    [
      ...data.edges.map((edge) => ({ source: `c:${edge.source}`, target: `c:${edge.target}`,
        label: edge.label, style: lineStyle(edge.style), directed: edge.directed, linkId: edge.link_id })),
      ...data.source_edges.map((edge) => ({ source: edge.source, target: `c:${edge.concept_id}`,
        style: 'solid' as const, faint: true })),
    ],
    !preserveView,
  )
}

async function focus(conceptIds: string[], preserveView = false) {
  if (!conceptIds.length) return
  const currentRequest = ++requestId
  mode.value = 'focused'
  focusIds.value = conceptIds
  let graphs: { nodes: LocalNode[]; edges: LinkEdge[] }[]
  try {
    graphs = await Promise.all(
      conceptIds.map((id) => api.get<{ nodes: LocalNode[]; edges: LinkEdge[] }>(`/api/graph/local?concept_id=${encodeURIComponent(id)}&show_weak=${showWeak.value}`)),
    )
  } catch (e) {
    return void store.fail(e, 'Could not focus the graph on that concept')
  }
  if (currentRequest !== requestId) return
  focusNames.value = graphs.map((g) => g.nodes[0]?.label ?? '')
  const seen = new Map<string, Entry>()
  const centres = new Set<string>()
  const edges = new Map<string, GraphEdge3D>()
  for (const local of graphs) {
    for (const n of local.nodes) {
      if (n.center) centres.add(n.id)
      if (seen.has(n.id)) continue
      seen.set(n.id, n.type === 'concept'
        ? { id: n.id, group: 'concept', name: n.label, description: n.center ? 'Focused concept' : 'Related concept' }
        : sourceEntry({ id: n.id, type: n.type, label: n.label, status: n.sublabel }))
    }
    for (const e of local.edges) {
      const id = e.link_id ? `link:${e.link_id}` : `${e.source}>${e.target}`
      edges.set(id, { source: e.source, target: e.target, label: e.label, style: lineStyle(e.style),
        directed: e.directed, linkId: e.link_id })
    }
  }
  const focusedEntries = [...seen.values()]
  show(
    focusedEntries,
    focusedEntries.map((entry) => node(entry, centres.has(entry.id) ? 13 : entry.group === 'concept' ? 9 : 6, true)),
    [...edges.values()],
    !preserveView,
  )
}

function applyFilter() {
  const visible = new Set(shownEntries.value.map((entry) => entry.id))
  if (selectedId.value && !visible.has(selectedId.value)) selectedId.value = null  // the filter hid it
  graph?.setVisible(visible)
}

function choose(id: string | null, centre = false) {
  selectedId.value = id
  graph?.select(id, centre)
}

/** Double-click: focus a concept; open a post, question or document. */
function activate(id: string) {
  if (id.startsWith('c:')) return void focus([id.slice(2)])
  if (id.startsWith('d:')) return void router.push(`/documents/${id.slice(2)}`)
  if (byId.value.get(id)?.group === 'item') emit('openItem', id.slice(2))
  else emit('openQuestion', id.slice(2))
}

function fit() {
  graph?.fit()
}

function reset() {
  groupFilter.value = 'all'
  applyFilter() // before reset() fits the view, so the fit covers every node
  graph?.reset()
  if (mode.value === 'focused') void loadFull()
}

function initGraph() {
  if (graph || !canvasEl.value) return
  graph = new Graph3D(canvasEl.value, {
    hover: (id, x, y) => {
      const entry = id ? byId.value.get(id) : undefined
      preview.value = entry ? { entry, x, y } : null
    },
    select: (id) => {
      selectedId.value = id
    },
    activate,
    link: (linkId) => emit('evidence', linkId),
  })
}

async function refresh() {
  try {
    concepts.value = await api.get<ConceptRow[]>('/api/graph/concepts')
  } catch (e) {
    return void store.fail(e, 'Could not refresh the knowledge graph')
  }
  // The canvas may have just become visible; size it before laying out.
  await nextTick()
  initGraph()
  const availableFocus = focusIds.value.filter((id) => concepts.value.some((concept) => concept.id === id))
  if (mode.value === 'focused' && availableFocus.length) await focus(availableFocus, true)
  else await loadFull(true)
}

watch(groupFilter, applyFilter)
watch(showWeak, refresh)
watch(selectedId, async (id) => {
  await nextTick()
  // Scroll only the explorer list: scrollIntoView would also move the page.
  const list = explorerEl.value
  const entry = list?.querySelector<HTMLElement>(`[data-entry="${CSS.escape(id ?? '')}"]`)
  if (!list || !entry) return
  const header = list.querySelector('h3')?.offsetHeight ?? 0
  if (entry.offsetTop - header < list.scrollTop) list.scrollTop = entry.offsetTop - header
  else if (entry.offsetTop + entry.offsetHeight > list.scrollTop + list.clientHeight)
    list.scrollTop = entry.offsetTop + entry.offsetHeight - list.clientHeight
})
watch(
  () => props.focusConceptIds,
  async (ids) => {
    if (ids.length) await focus(ids)
    else if (mode.value === 'focused') await loadFull()
  },
)

onMounted(refresh)
onBeforeUnmount(() => { requestId++; graph?.destroy(); graph = null })
defineExpose({ refresh })
</script>

<template>
  <div class="map-card home-graph">
    <div class="map-toolbar">
      <strong data-testid="graph-title">
        {{ mode === 'full' ? 'Knowledge graph' : `Focused on ${focusNames.join(' · ')}` }}
      </strong>
      <div class="row gap8 wrap">
        <select v-model="groupFilter" aria-label="Show node types" data-testid="graph-filter">
          <option value="all">All</option>
          <option v-for="group in presentGroups" :key="group" :value="group">{{ GROUPS[group].label }}</option>
        </select>
        <button class="btn small" data-testid="graph-fit" @click="fit">Fit</button>
        <button class="btn small" data-testid="graph-reset" @click="reset">Reset</button>
        <button v-if="mode === 'focused'" class="btn small" data-testid="graph-full" @click="loadFull()">Full map</button>
      </div>
    </div>
    <div v-if="concepts.length" class="row gap8 graph-options">
      <label class="muted"><input v-model="showWeak" type="checkbox" /> Show weak associations</label>
      <select aria-label="Focus on a concept" value="" @change="focus([($event.target as HTMLSelectElement).value])">
        <option value="" disabled>Focus on a concept…</option>
        <option v-for="concept in concepts" :key="concept.id" :value="concept.id">{{ concept.name }}</option>
      </select>
      <span v-if="mode === 'full'" class="muted" data-testid="graph-counts">
        {{ concepts.length - omittedNodes }} of {{ concepts.length }} concepts ·
        {{ totalEdges - omittedEdges }} of {{ totalEdges }} links ·
        {{ entries.filter((entry) => entry.group !== 'concept').length }} posts, questions and documents<template
          v-if="omittedNodes || omittedEdges || omittedSources"
        >
          · more omitted ({{ omittedNodes }} concepts, {{ omittedEdges }} links, {{ omittedSources }} posts, questions and documents); choose a concept to focus</template>
      </span>
    </div>
    <div v-if="!concepts.length" class="graph-empty">
      Supported concepts and relationships appear here as the team contributes knowledge. You can also define concepts under Expertise Routing.
    </div>
    <div v-show="concepts.length" class="graph3d">
      <div class="graph3d-stage graph-box" data-testid="graph">
        <canvas ref="canvasEl" role="img" aria-label="Knowledge graph in 3D; use the graph explorer to browse it with a keyboard"></canvas>
        <div class="graph3d-legend" data-testid="graph-legend">
          <span v-for="group in presentGroups" :key="group" class="graph3d-chip">
            <i :style="{ background: GROUPS[group].color }"></i>{{ GROUPS[group].label }}
          </span>
          <span class="graph3d-chip">Links: solid active · dashed awaiting evidence · dotted association</span>
        </div>
        <div class="graph3d-hint">
          Drag rotate · Shift+drag pan · Wheel zoom · Hover preview · Click select · Double-click focus · Click a link to see why
        </div>
        <div v-if="preview" class="graph3d-preview" data-testid="graph-preview"
          :style="{ left: `${preview.x + 14}px`, top: `${preview.y + 14}px` }">
          <span class="eyebrow">{{ GROUPS[preview.entry.group].label }}</span>
          <strong>{{ preview.entry.name }}</strong>
          <span>{{ preview.entry.description }}</span>
        </div>
      </div>
      <aside ref="explorerEl" class="graph3d-explorer" data-testid="graph-explorer" aria-label="Graph explorer">
        <h3>Graph explorer</h3>
        <button v-for="entry in shownEntries" :key="entry.id" type="button" class="graph3d-entry"
          :class="{ active: entry.id === selectedId }" :data-entry="entry.id" data-testid="graph-explorer-item"
          :aria-pressed="entry.id === selectedId" @click="choose(entry.id, true)" @dblclick="activate(entry.id)">
          <span class="eyebrow" :style="{ color: GROUPS[entry.group].color }">{{ GROUPS[entry.group].label }}</span>
          <strong>{{ entry.name }}</strong>
          <span>{{ entry.description }}</span>
        </button>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.graph-options { padding: 0 16px 8px; flex-wrap: wrap; }
.graph-options .muted { color: #9fb4d2; }
.graph3d { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 12px; }
.graph3d-stage { position: relative; border-radius: 12px; overflow: hidden; }
.graph3d-stage canvas { display: block; width: 100%; height: 100%; cursor: grab; touch-action: none; }
.graph3d-legend { position: absolute; top: 12px; left: 12px; right: 12px; display: flex; flex-wrap: wrap; gap: 6px; pointer-events: none; }
.graph3d-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 9px; border-radius: 999px; background: rgba(6, 17, 34, 0.75); font-size: 11px; color: #dbe7f6; }
.graph3d-chip i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.graph3d-hint { position: absolute; left: 12px; bottom: 12px; padding: 6px 10px; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.12); background: rgba(6, 17, 34, 0.75); font-size: 11px; color: #c8d6ea; pointer-events: none; }
.graph3d-preview { position: absolute; max-width: 260px; padding: 9px 11px; border-radius: 9px; background: #fff; color: var(--ink); box-shadow: var(--shadow); font-size: 12px; display: grid; gap: 2px; pointer-events: none; z-index: 2; }
.graph3d-preview .eyebrow { margin: 0; }
.graph3d-explorer { position: relative; height: 430px; overflow: auto; background: #fff; color: var(--ink); border-radius: 12px; padding: 0 0 8px; }
.graph3d-explorer h3 { position: sticky; top: 0; background: #fff; padding: 14px 16px 10px; border-bottom: 1px solid var(--border); font-size: 16px; }
.graph3d-entry { display: grid; gap: 3px; width: 100%; text-align: left; border: 0; border-bottom: 1px solid var(--border); background: transparent; padding: 11px 16px; color: inherit; }
.graph3d-entry:hover { background: #f5f8fc; }
.graph3d-entry.active { background: var(--info-soft); box-shadow: inset 3px 0 var(--accent); }
.graph3d-entry .eyebrow { margin: 0; }
.graph3d-entry strong { font-size: 13px; overflow-wrap: anywhere; }
.graph3d-entry span:last-child { font-size: 11px; color: var(--muted); }
@media (max-width: 900px) {
  .graph3d { grid-template-columns: 1fr; }
  .graph3d-explorer { height: 260px; }
}
</style>
