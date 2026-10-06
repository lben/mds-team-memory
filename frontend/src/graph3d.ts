// A small 3D graph on a 2D canvas: a seeded force layout, a perspective camera,
// and pointer controls (drag rotate, Shift+drag pan, wheel zoom, hover,
// click select, double-click activate). No WebGL or library is needed for the
// few hundred nodes the overview shows.

export interface GraphNode3D {
  id: string
  label: string
  color: string
  size: number // base radius in pixels at unit depth
  labelled: boolean // always show the label, not only on hover or selection
}

export interface GraphEdge3D {
  source: string
  target: string
  label?: string
  style: 'solid' | 'dashed' | 'dotted'
  directed?: boolean
  linkId?: string | null // clicking the link opens its evidence
  faint?: boolean
}

export interface Graph3DEvents {
  hover(id: string | null, x: number, y: number): void
  select(id: string | null): void
  activate(id: string): void
  link(linkId: string): void
}

interface Placed extends GraphNode3D {
  x: number
  y: number
  z: number
  // Projection, refreshed each frame.
  sx: number
  sy: number
  scale: number
  depth: number
}

const FOV = (50 * Math.PI) / 180
const REFERENCE_DISTANCE = 900 // node and label sizes are their base sizes at this camera distance
const DEFAULT_YAW = 0.6
const DEFAULT_PITCH = -0.3

function seeded(id: string) {
  // Stable pseudo-random numbers per node, so the same data lays out the same way.
  let h = 2166136261
  for (const c of id) h = Math.imul(h ^ c.charCodeAt(0), 16777619)
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507)
    h = Math.imul(h ^ (h >>> 13), 3266489909)
    return ((h ^= h >>> 16) >>> 0) / 4294967296
  }
}

export class Graph3D {
  private nodes: Placed[] = []
  private byId = new Map<string, Placed>()
  private edges: GraphEdge3D[] = []
  private neighbors = new Map<string, Set<string>>()
  private visible = new Set<string>()
  private selected: string | null = null
  private hovered: string | null = null
  private yaw = DEFAULT_YAW
  private pitch = DEFAULT_PITCH
  private distance = 900
  private panX = 0
  private panY = 0
  private centre = { x: 0, y: 0, z: 0 }
  private frame = 0
  private drag: { x: number; y: number; moved: boolean; pan: boolean } | null = null
  private resize = new ResizeObserver(() => this.draw())
  private context: CanvasRenderingContext2D
  private canvas: HTMLCanvasElement
  private events: Graph3DEvents

  constructor(canvas: HTMLCanvasElement, events: Graph3DEvents) {
    this.canvas = canvas
    this.events = events
    this.context = canvas.getContext('2d')!
    // Browser tests drive real pointer events at a node's drawn position (see screenPosition).
    Object.defineProperty(canvas, 'graph3d', { value: this, configurable: true })
    canvas.addEventListener('pointerdown', this.onDown)
    canvas.addEventListener('pointermove', this.onMove)
    canvas.addEventListener('pointerup', this.onUp)
    canvas.addEventListener('pointerleave', this.onLeave)
    canvas.addEventListener('dblclick', this.onDoubleClick)
    canvas.addEventListener('wheel', this.onWheel, { passive: false })
    this.resize.observe(canvas)
  }

  destroy() {
    cancelAnimationFrame(this.frame)
    this.resize.disconnect()
    this.canvas.removeEventListener('pointerdown', this.onDown)
    this.canvas.removeEventListener('pointermove', this.onMove)
    this.canvas.removeEventListener('pointerup', this.onUp)
    this.canvas.removeEventListener('pointerleave', this.onLeave)
    this.canvas.removeEventListener('dblclick', this.onDoubleClick)
    this.canvas.removeEventListener('wheel', this.onWheel)
  }

  /** Replace the graph and lay it out; keeps the camera unless `fit` is set. */
  setData(nodes: GraphNode3D[], edges: GraphEdge3D[], fit = true) {
    const previous = this.byId
    this.nodes = nodes.map((node) => {
      const kept = previous.get(node.id)
      const random = seeded(node.id)
      const radius = 160 * Math.cbrt(random())
      const theta = 2 * Math.PI * random()
      const phi = Math.acos(2 * random() - 1)
      return {
        ...node,
        x: kept?.x ?? radius * Math.sin(phi) * Math.cos(theta),
        y: kept?.y ?? radius * Math.sin(phi) * Math.sin(theta),
        z: kept?.z ?? radius * Math.cos(phi),
        sx: 0, sy: 0, scale: 1, depth: 0,
      }
    })
    this.byId = new Map(this.nodes.map((node) => [node.id, node]))
    this.edges = edges.filter((edge) => this.byId.has(edge.source) && this.byId.has(edge.target))
    this.neighbors = new Map(this.nodes.map((node) => [node.id, new Set<string>()]))
    for (const edge of this.edges) {
      this.neighbors.get(edge.source)!.add(edge.target)
      this.neighbors.get(edge.target)!.add(edge.source)
    }
    this.visible = new Set(this.byId.keys())
    if (this.selected && !this.byId.has(this.selected)) this.selected = null
    this.layout()
    if (fit || !previous.size) this.fit()
    else this.draw()
  }

  setVisible(ids: Set<string>) {
    this.visible = ids
    if (this.selected && !ids.has(this.selected)) this.select(null)
    this.draw()
  }

  select(id: string | null, centre = false) {
    this.selected = id
    const node = id ? this.byId.get(id) : undefined
    if (node && centre) {
      this.centre = { x: node.x, y: node.y, z: node.z }
      this.panX = this.panY = 0
    }
    this.draw()
  }

  /** Zoom and centre so every visible node is in view. */
  fit() {
    const shown = this.nodes.filter((node) => this.visible.has(node.id))
    if (!shown.length) return this.draw()
    const centre = { x: 0, y: 0, z: 0 }
    for (const node of shown) {
      centre.x += node.x / shown.length
      centre.y += node.y / shown.length
      centre.z += node.z / shown.length
    }
    const radius = Math.max(60, ...shown.map((n) => Math.hypot(n.x - centre.x, n.y - centre.y, n.z - centre.z)))
    const { width, height } = this.canvas.getBoundingClientRect()
    const aspect = Math.min(1, (width || 1) / (height || 1))
    this.centre = centre
    this.panX = this.panY = 0
    // A little extra room keeps labels at the edge readable.
    this.distance = (radius * 1.2) / (Math.tan(FOV / 2) * aspect) + radius * 0.4
    this.draw()
  }

  reset() {
    this.yaw = DEFAULT_YAW
    this.pitch = DEFAULT_PITCH
    this.selected = null
    this.events.select(null)
    this.fit()
  }

  /** Where a node is drawn, in CSS pixels relative to the canvas (also used by browser tests). */
  screenPosition(id: string) {
    const node = this.byId.get(id)
    this.project()
    return node ? { x: node.sx, y: node.sy } : null
  }

  private layout() {
    const nodes = this.nodes
    const count = nodes.length
    if (count < 2) return
    const ideal = 70
    const reach = (4 * ideal) ** 2 // beyond this, nodes stop pushing; gravity then keeps loose nodes near the rest
    const iterations = Math.min(300, Math.max(120, Math.round(60000 / count)))
    const index = new Map(nodes.map((node, i) => [node.id, i]))
    for (let step = 0; step < iterations; step++) {
      const cooling = 1 - step / iterations
      const forces = nodes.map(() => [0, 0, 0])
      for (let i = 0; i < count; i++) {
        for (let j = i + 1; j < count; j++) {
          const a = nodes[i], b = nodes[j]
          let dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z
          const d2 = dx * dx + dy * dy + dz * dz + 0.01
          if (d2 > reach) continue
          const push = (ideal * ideal) / d2
          dx *= push; dy *= push; dz *= push
          forces[i][0] += dx; forces[i][1] += dy; forces[i][2] += dz
          forces[j][0] -= dx; forces[j][1] -= dy; forces[j][2] -= dz
        }
      }
      for (const edge of this.edges) {
        const i = index.get(edge.source)!, j = index.get(edge.target)!
        const a = nodes[i], b = nodes[j]
        const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z
        const d = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.01
        const pull = ((d - ideal) / d) * (edge.faint ? 0.05 : 0.1)
        forces[i][0] += dx * pull; forces[i][1] += dy * pull; forces[i][2] += dz * pull
        forces[j][0] -= dx * pull; forces[j][1] -= dy * pull; forces[j][2] -= dz * pull
      }
      nodes.forEach((node, i) => {
        // Gravity keeps unconnected parts of the graph from drifting apart.
        const [fx, fy, fz] = [forces[i][0] - node.x * 0.02, forces[i][1] - node.y * 0.02, forces[i][2] - node.z * 0.02]
        const length = Math.hypot(fx, fy, fz) || 1
        const limit = 12 * cooling + 0.5
        const move = Math.min(length, limit) / length
        node.x += fx * move
        node.y += fy * move
        node.z += fz * move
      })
    }
  }

  private project() {
    const { width, height } = this.canvas.getBoundingClientRect()
    const focal = height / 2 / Math.tan(FOV / 2)
    const cosY = Math.cos(this.yaw), sinY = Math.sin(this.yaw)
    const cosP = Math.cos(this.pitch), sinP = Math.sin(this.pitch)
    for (const node of this.nodes) {
      const x = node.x - this.centre.x, y = node.y - this.centre.y, z = node.z - this.centre.z
      const rx = x * cosY + z * sinY
      const rz = -x * sinY + z * cosY
      const ry = y * cosP - rz * sinP
      const depth = y * sinP + rz * cosP + this.distance
      const scale = focal / Math.max(depth, 1)
      node.sx = width / 2 + this.panX + rx * scale
      node.sy = height / 2 + this.panY + ry * scale
      node.scale = REFERENCE_DISTANCE / Math.max(depth, 1)
      node.depth = depth
    }
  }

  private nodeAt(x: number, y: number) {
    let best: Placed | null = null
    for (const node of this.nodes) {
      if (!this.visible.has(node.id) || node.depth <= 1) continue
      const radius = Math.max(6, node.size * node.scale) + 4
      if (Math.hypot(node.sx - x, node.sy - y) <= radius && (!best || node.depth < best.depth)) best = node
    }
    return best
  }

  private linkAt(x: number, y: number) {
    for (const edge of this.edges) {
      if (!edge.linkId) continue
      const a = this.byId.get(edge.source)!, b = this.byId.get(edge.target)!
      if (!this.visible.has(a.id) || !this.visible.has(b.id)) continue
      const dx = b.sx - a.sx, dy = b.sy - a.sy
      const t = Math.max(0, Math.min(1, ((x - a.sx) * dx + (y - a.sy) * dy) / (dx * dx + dy * dy || 1)))
      if (Math.hypot(a.sx + t * dx - x, a.sy + t * dy - y) <= 5) return edge.linkId
    }
    return null
  }

  private point(event: PointerEvent | MouseEvent) {
    const box = this.canvas.getBoundingClientRect()
    return { x: event.clientX - box.left, y: event.clientY - box.top }
  }

  private onDown = (event: PointerEvent) => {
    this.canvas.setPointerCapture(event.pointerId)
    const { x, y } = this.point(event)
    this.drag = { x, y, moved: false, pan: event.shiftKey }
  }

  private onMove = (event: PointerEvent) => {
    const { x, y } = this.point(event)
    if (this.drag) {
      const dx = x - this.drag.x, dy = y - this.drag.y
      if (!this.drag.moved && Math.hypot(dx, dy) < 4) return
      this.drag.moved = true
      if (this.drag.pan) {
        this.panX += dx
        this.panY += dy
      } else {
        this.yaw += dx * 0.006
        this.pitch = Math.max(-1.45, Math.min(1.45, this.pitch + dy * 0.006))
      }
      this.drag.x = x
      this.drag.y = y
      this.draw()
      return
    }
    this.project()
    const node = this.nodeAt(x, y)
    const id = node?.id ?? null
    this.canvas.style.cursor = node || this.linkAt(x, y) ? 'pointer' : 'grab'
    if (id !== this.hovered) {
      this.hovered = id
      this.draw()
    }
    this.events.hover(id, x, y)
  }

  private onUp = (event: PointerEvent) => {
    const drag = this.drag
    this.drag = null
    if (!drag || drag.moved) return
    const { x, y } = this.point(event)
    this.project()
    const node = this.nodeAt(x, y)
    if (node) {
      this.selected = node.id
      this.events.select(node.id)
      return this.draw()
    }
    const linkId = this.linkAt(x, y)
    if (linkId) return this.events.link(linkId)
    this.selected = null
    this.events.select(null)
    this.draw()
  }

  private onLeave = () => {
    if (this.hovered) {
      this.hovered = null
      this.draw()
    }
    this.events.hover(null, 0, 0)
  }

  private onDoubleClick = (event: MouseEvent) => {
    const { x, y } = this.point(event)
    this.project()
    const node = this.nodeAt(x, y)
    if (node) this.events.activate(node.id)
  }

  private onWheel = (event: WheelEvent) => {
    event.preventDefault()
    this.distance = Math.max(80, Math.min(8000, this.distance * Math.exp(event.deltaY * 0.0012)))
    this.draw()
  }

  private draw() {
    cancelAnimationFrame(this.frame)
    this.frame = requestAnimationFrame(() => this.paint())
  }

  private paint() {
    const canvas = this.canvas, ctx = this.context
    const { width, height } = canvas.getBoundingClientRect()
    const ratio = window.devicePixelRatio || 1
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio)
      canvas.height = Math.round(height * ratio)
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
    const background = ctx.createRadialGradient(width / 2, height / 2, 0, width / 2, height / 2, Math.max(width, height) * 0.7)
    background.addColorStop(0, '#16406f')
    background.addColorStop(1, '#081629')
    ctx.fillStyle = background
    ctx.fillRect(0, 0, width, height)
    this.project()

    const focus = this.selected ?? this.hovered
    const near = focus ? new Set([focus, ...(this.neighbors.get(focus) ?? [])]) : null
    const shown = this.nodes.filter((node) => this.visible.has(node.id) && node.depth > 1)
    const depths = shown.map((node) => node.depth)
    const nearest = Math.min(...depths), farthest = Math.max(...depths)
    const fade = (node: Placed) => (farthest > nearest ? 1 - 0.55 * ((node.depth - nearest) / (farthest - nearest)) : 1)

    for (const edge of this.edges) {
      const a = this.byId.get(edge.source)!, b = this.byId.get(edge.target)!
      if (!this.visible.has(a.id) || !this.visible.has(b.id) || a.depth <= 1 || b.depth <= 1) continue
      const lit = near ? near.has(a.id) && near.has(b.id) && (a.id === focus || b.id === focus) : false
      ctx.globalAlpha = (near && !lit ? 0.08 : edge.faint ? 0.22 : 0.5) * Math.min(fade(a), fade(b))
      ctx.strokeStyle = lit ? '#ff6b81' : '#9fb8d8'
      ctx.lineWidth = lit ? 1.6 : 1
      ctx.setLineDash(edge.style === 'dashed' ? [6, 4] : edge.style === 'dotted' ? [2, 4] : [])
      ctx.beginPath()
      ctx.moveTo(a.sx, a.sy)
      ctx.lineTo(b.sx, b.sy)
      ctx.stroke()
      if (edge.directed) {
        // An arrowhead just outside the target node shows which way the relationship reads.
        const angle = Math.atan2(b.sy - a.sy, b.sx - a.sx)
        const tip = Math.max(3, b.size * b.scale) + 3
        const x = b.sx - Math.cos(angle) * tip, y = b.sy - Math.sin(angle) * tip
        ctx.setLineDash([])
        ctx.fillStyle = ctx.strokeStyle
        ctx.beginPath()
        ctx.moveTo(x, y)
        ctx.lineTo(x - Math.cos(angle - 0.45) * 8, y - Math.sin(angle - 0.45) * 8)
        ctx.lineTo(x - Math.cos(angle + 0.45) * 8, y - Math.sin(angle + 0.45) * 8)
        ctx.fill()
      }
      if (lit && edge.label && !edge.faint) {
        ctx.setLineDash([])
        ctx.globalAlpha = 0.95
        ctx.font = '10px Inter, "Segoe UI", sans-serif'
        ctx.fillStyle = '#cfe0f5'
        ctx.textAlign = 'center'
        ctx.fillText(edge.label, (a.sx + b.sx) / 2, (a.sy + b.sy) / 2 - 4)
      }
    }
    ctx.setLineDash([])

    for (const node of [...shown].sort((a, b) => b.depth - a.depth)) {
      const radius = Math.max(3, node.size * node.scale)
      const dim = near && !near.has(node.id)
      ctx.globalAlpha = (dim ? 0.25 : 1) * fade(node)
      const glow = ctx.createRadialGradient(node.sx, node.sy, 0, node.sx, node.sy, radius * 2.4)
      glow.addColorStop(0, node.color)
      glow.addColorStop(0.45, node.color + '55')
      glow.addColorStop(1, node.color + '00')
      ctx.fillStyle = glow
      ctx.beginPath()
      ctx.arc(node.sx, node.sy, radius * 2.4, 0, Math.PI * 2)
      ctx.fill()
      ctx.fillStyle = node.color
      ctx.beginPath()
      ctx.arc(node.sx, node.sy, radius, 0, Math.PI * 2)
      ctx.fill()
      if (node.id === this.selected) {
        ctx.strokeStyle = '#ffffff'
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.arc(node.sx, node.sy, radius + 4, 0, Math.PI * 2)
        ctx.stroke()
      }
      if (node.labelled || node.id === this.hovered || near?.has(node.id)) {
        const size = Math.max(10, Math.min(17, 12 * node.scale))
        ctx.font = `${node.id === focus ? 600 : 500} ${size}px Inter, "Segoe UI", sans-serif`
        ctx.textAlign = 'center'
        ctx.shadowColor = 'rgba(4, 12, 26, 0.95)'
        ctx.shadowBlur = 4
        ctx.fillStyle = '#eef4fc'
        ctx.fillText(node.label, node.sx, node.sy + radius + size + 2)
        ctx.shadowBlur = 0
      }
    }
    ctx.globalAlpha = 1
  }
}
