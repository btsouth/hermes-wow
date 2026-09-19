'use strict'

// Renderer for the overlay. Owns presentation and the keyboard, nothing else:
// no timers of its own (main pushes a roster), no state worth persisting.

const body = document.body
const badge = document.getElementById('badge')
const badgeText = badge.querySelector('.badge-text')
const badgeDots = badge.querySelector('.badge-dots')
const rowsEl = document.getElementById('rows')
const emptyEl = document.getElementById('empty')
const countsEl = document.getElementById('board-counts')
const statusEl = document.getElementById('board-status')
const composerEl = document.getElementById('composer')
const composerLabel = document.getElementById('composer-label')
const composerInput = document.getElementById('composer-input')

const STATUS_ORDER = ['needs', 'error', 'working', 'reply', 'idle', 'finished']
const LABELS = {
  needs: 'needs you',
  error: 'error',
  working: 'working',
  reply: 'new reply',
  idle: 'idle',
  finished: 'finished'
}

let sessions = []
let selected = 0
let replyTarget = null
let mode = 'badge'

function age(seconds) {
  if (seconds < 60) return 'now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

function renderBadge(data) {
  const counts = data.counts || {}
  const hot = (counts.needs || 0) + (counts.error || 0)
  const working = counts.working || 0
  const reply = counts.reply || 0

  badge.classList.toggle('attention', hot > 0)
  badgeText.classList.toggle('hot', hot > 0)

  const parts = []
  if (hot) parts.push(`${hot} need${hot === 1 ? 's' : ''} you`)
  else if (working) parts.push(`${working} working`)
  else if (reply) parts.push(`${reply} replied`)
  else parts.push('idle')
  badgeText.textContent = parts.join(' \u00b7 ')

  badgeDots.replaceChildren()
  for (const status of STATUS_ORDER) {
    const count = counts[status] || 0
    if (!count || status === 'idle' || status === 'finished') continue
    const dot = document.createElement('i')
    dot.className = status
    dot.style.background = `var(--${status === 'reply' ? 'reply' : status})`
    badgeDots.append(dot)
  }
}

function renderCounts(data) {
  const counts = data.counts || {}
  countsEl.replaceChildren()

  for (const status of STATUS_ORDER) {
    const count = counts[status] || 0
    if (!count) continue
    const chip = document.createElement('span')
    chip.className = `chip${status === 'needs' || status === 'error' ? ' hot' : ''}`
    chip.textContent = `${count} ${LABELS[status]}`
    countsEl.append(chip)
  }
}

function renderRows() {
  rowsEl.replaceChildren()
  emptyEl.hidden = sessions.length > 0

  sessions.forEach((session, index) => {
    const row = document.createElement('div')
    row.className = `row${index === selected ? ' selected' : ''}`
    row.setAttribute('role', 'option')
    row.setAttribute('aria-selected', index === selected ? 'true' : 'false')

    const dot = document.createElement('i')
    dot.className = `dot ${session.status}`

    const left = document.createElement('div')
    const title = document.createElement('div')
    title.className = 'title'
    title.textContent = session.title
    const meta = document.createElement('div')
    meta.className = 'meta'
    meta.textContent = [session.status_label, session.project || session.source, session.activity]
      .filter(Boolean)
      .join(' \u00b7 ')
    left.append(title, meta)

    const right = document.createElement('div')
    right.style.textAlign = 'right'
    const status = document.createElement('div')
    status.className = `status ${session.status}`
    status.textContent = session.status_label
    const time = document.createElement('div')
    time.className = 'age'
    time.textContent = age(session.age_s)
    right.append(time)

    row.append(dot, left, right)

    row.addEventListener('click', () => {
      if (selected === index && session.status !== 'finished') {
        openComposer(session)
        return
      }
      selected = index
      renderRows()
    })

    rowsEl.append(row)
  })

  const current = sessions[selected]
  if (current) {
    rowsEl.querySelector('.row.selected')?.scrollIntoView({ block: 'nearest' })
  }
}

function openComposer(session) {
  replyTarget = session
  composerEl.hidden = false
  composerLabel.textContent = `Reply to ${session.title}`
  composerInput.value = ''
  composerInput.focus()
}

function closeComposer() {
  replyTarget = null
  composerEl.hidden = true
  composerInput.value = ''
}

function setStatus(text) {
  statusEl.textContent = text
  if (!text) return
  setTimeout(() => {
    if (statusEl.textContent === text) statusEl.textContent = ''
  }, 4000)
}

function move(delta) {
  if (!sessions.length) return
  selected = Math.min(sessions.length - 1, Math.max(0, selected + delta))
  renderRows()
}

document.addEventListener('keydown', event => {
  if (!composerEl.hidden) {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeComposer()
      return
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      const text = composerInput.value.trim()
      if (text && replyTarget) {
        window.wow.reply(replyTarget.id, text)
        setStatus(`sent to ${replyTarget.title.slice(0, 28)}`)
      }
      closeComposer()
      return
    }
    return
  }

  switch (event.key) {
    case 'ArrowDown':
    case 'j':
      event.preventDefault()
      move(1)
      break
    case 'ArrowUp':
    case 'k':
      event.preventDefault()
      move(-1)
      break
    case 'Enter':
      if (sessions[selected]) {
        event.preventDefault()
        openComposer(sessions[selected])
      }
      break
    case 'Escape':
      event.preventDefault()
      window.wow.command('badge')
      break
    case 'r':
      window.wow.command('refresh')
      setStatus('refreshing')
      break
    default:
      break
  }
})

document.getElementById('board-close').addEventListener('click', () => window.wow.command('badge'))

window.wow.onRoster(data => {
  sessions = data.sessions || []
  selected = Math.min(selected, Math.max(0, sessions.length - 1))
  renderBadge(data)
  renderCounts(data)
  renderRows()
})

window.wow.onMode(({ mode: next }) => {
  mode = next
  body.className = `mode-${next}`
  if (next === 'board') {
    renderRows()
  } else {
    closeComposer()
  }
})
