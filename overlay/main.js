'use strict'

// Hermes WoW mode overlay: one always-on-top window that has two shapes and no
// chrome, plus a unix socket so a keybind can drive it.
//
// The window itself is deliberately dumb. Every fact comes from `hermes-wow
// board --json` (a child process, so a wedged interpreter cannot wedge the
// overlay) and every action goes back out through the CLI. Nothing here holds
// Hermes state, which is what keeps the overlay safe to kill at any moment.
//
// Wayland note: an app cannot place its own toplevel, so the geometry below is
// a request and the Hyprland pin step (`hermes-wow pin`) is what actually
// floats, pins, and moves the window.

const { app, BrowserWindow, ipcMain, screen } = require('electron')
const { execFile } = require('node:child_process')
const fs = require('node:fs')
const net = require('node:net')
const path = require('node:path')

const CLI = process.env.HERMES_WOW_CLI || path.join(__dirname, '..', 'bin', 'hermes-wow')
const START_MODE = process.env.HERMES_WOW_MODE === 'board' ? 'board' : 'badge'
const PIN_ENABLED = process.env.HERMES_WOW_PIN !== '0'
const SELF_TEST = process.env.HERMES_WOW_SELF_TEST === '1'
const HEADLESS = SELF_TEST || process.env.HERMES_WOW_HEADLESS === '1'
const REFRESH_MS = Number(process.env.HERMES_WOW_REFRESH_MS || 4000)
const RUNTIME_DIR = process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid()}`
const SOCKET_PATH = path.join(RUNTIME_DIR, 'hermes-wow.sock')
const LOG_PATH = path.join(RUNTIME_DIR, 'hermes-wow.log')

const GEOMETRY = {
  badge: { width: 196, height: 40 },
  board: { width: 440, height: 560 }
}
const MARGIN = 24

let win = null
let mode = START_MODE
let rosterTimer = null
let selfTestTimer = null
let latestRoster = null

const log = message => {
  try {
    fs.appendFileSync(LOG_PATH, `${new Date().toISOString()} ${message}\n`)
  } catch {
    // Logging must never be the thing that breaks the overlay.
  }
}

function cli(args, timeout = 20000) {
  return new Promise(resolve => {
    execFile(CLI, args, { timeout, env: process.env }, (error, stdout, stderr) =>
      resolve({ error, stdout: stdout || '', stderr: stderr || '' })
    )
  })
}

function send(channel, payload) {
  if (win && !win.isDestroyed()) {
    win.webContents.send(channel, payload)
  }
}

async function pinWindow(nextMode) {
  if (!PIN_ENABLED) {
    return
  }

  const geometry = GEOMETRY[nextMode] || GEOMETRY.board
  const anchor = process.env.HERMES_WOW_ANCHOR || 'game'
  const { error, stdout } = await cli([
    'pin',
    '--mode',
    nextMode,
    '--anchor',
    anchor,
    '--margin',
    String(MARGIN),
    '--width',
    String(geometry.width),
    '--height',
    String(geometry.height)
  ])

  if (error) {
    log(`pin(${nextMode}) failed: ${(stdout || error.message).trim()}`)
  }
}

function placeInitial() {
  if (!win) {
    return
  }

  const area = screen.getPrimaryDisplay().workArea
  const geometry = GEOMETRY[mode]
  win.setBounds({
    x: Math.max(area.x, area.x + area.width - geometry.width - MARGIN),
    y: area.y + MARGIN,
    width: geometry.width,
    height: geometry.height
  })
}

function applyMode(nextMode) {
  mode = nextMode
  const geometry = GEOMETRY[nextMode] || GEOMETRY.board

  if (!win || win.isDestroyed()) {
    return
  }

  // Badge is decoration: it must never take a click or a keystroke away from
  // the game. Board is interactive, but only because the user asked for it.
  win.setIgnoreMouseEvents(nextMode === 'badge', { forward: true })
  win.setFocusable(nextMode === 'board')

  const bounds = win.getBounds()
  win.setBounds({ ...bounds, width: geometry.width, height: geometry.height })
  send('wow:mode', { mode: nextMode })

  if (HEADLESS) {
    // Headless runs (self test, socket verification) never map a window: the
    // whole point is to exercise the wiring without touching the screen.
    return
  }

  win.show()

  if (nextMode === 'board') {
    win.focus()
  }

  void pinWindow(nextMode)
}

async function refreshRoster() {
  const { error, stdout } = await cli(['board', '--json', '--compact', '--limit', '15', '--days', '3'])

  if (error || !stdout.trim()) {
    return
  }

  let payload
  try {
    payload = JSON.parse(stdout.trim().split('\n').pop())
  } catch {
    log('roster payload was not JSON')
    return
  }

  latestRoster = payload
  send('wow:roster', payload)

  if (SELF_TEST && !selfTestTimer) {
    selfTestTimer = setTimeout(runSelfTest, 900)
  }
}
function startRosterLoop() {
  void refreshRoster()
  rosterTimer = setInterval(() => void refreshRoster(), REFRESH_MS)
}

function runSelfTest() {
  if (!win || win.isDestroyed()) {
    app.exit(1)
    return
  }

  win.webContents
    .executeJavaScript('document.body.innerText', true)
    .then(text => {
      process.stdout.write(`SELFTEST-OK\n${text}\n`)
      app.exit(0)
    })
    .catch(error => {
      process.stdout.write(`SELFTEST-FAIL ${error.message}\n`)
      app.exit(1)
    })
}

async function handleCommand(payload) {
  const command = String(payload.cmd || '')

  if (command.startsWith('mode:')) {
    const requested = command.slice('mode:'.length)
    applyMode(requested === 'toggle' ? (mode === 'board' ? 'badge' : 'board') : requested)
    return { ok: true, mode }
  }

  switch (command) {
    case 'ping':
      return { ok: true, mode }
    case 'toggle':
      applyMode(mode === 'board' ? 'badge' : 'board')
      return { ok: true, mode }
    case 'badge':
      applyMode('badge')
      return { ok: true, mode }
    case 'board':
      applyMode('board')
      return { ok: true, mode }
    case 'show':
      if (win) win.show()
      applyMode('board')
      return { ok: true, mode }
    case 'hide':
      if (win) win.hide()
      return { ok: true, hidden: true }
    case 'refresh':
      await refreshRoster()
      return { ok: true }
    case 'quit':
      setTimeout(() => {
        app.quit()
        // A transparent pinned toplevel on Wayland can outlive a polite quit;
        // the socket owner has to actually go away or the next launch sees a
        // ghost and refuses to start.
        setTimeout(() => app.exit(0), 1500)
      }, 50)
      return { ok: true, quitting: true }
    default:
      return { ok: false, error: `unknown command: ${command}` }
  }
}

function startControlServer() {
  try {
    fs.unlinkSync(SOCKET_PATH)
  } catch {
    // no stale socket
  }

  const server = net.createServer(socket => {
    let buffer = ''

    socket.on('data', chunk => {
      buffer += chunk.toString()

      let index
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index).trim()
        buffer = buffer.slice(index + 1)

        if (!line) {
          continue
        }

        let payload
        try {
          payload = JSON.parse(line)
        } catch {
          socket.write(`${JSON.stringify({ ok: false, error: 'bad json' })}\n`)
          continue
        }

        if (payload.session_id && payload.text) {
          void reply(payload.session_id, payload.text).then(result => {
            try {
              socket.write(`${JSON.stringify(result)}\n`)
            } catch {
              // peer went away
            }
          })
          continue
        }

        void handleCommand(payload).then(result => {
          try {
            socket.write(`${JSON.stringify(result)}\n`)
          } catch {
            // peer went away
          }
        })
      }
    })

    socket.on('error', () => {})
  })

  server.on('error', error => log(`control socket: ${error.message}`))
  server.listen(SOCKET_PATH)
}

async function reply(sessionId, text) {
  const { error, stdout, stderr } = await cli(['reply', String(sessionId), String(text)])
  const detail = (stdout || stderr || '').trim()

  if (error) {
    log(`reply failed: ${detail || error.message}`)
    return { ok: false, error: detail || error.message }
  }

  void refreshRoster()
  return { ok: true, detail }
}

async function alreadyRunning() {
  return new Promise(resolve => {
    const probe = net.connect(SOCKET_PATH)
    const done = value => {
      probe.destroy()
      resolve(value)
    }
    probe.once('connect', () => done(true))
    probe.once('error', () => done(false))
    setTimeout(() => done(false), 400)
  })
}

function createWindow() {
  const geometry = GEOMETRY[mode]

  win = new BrowserWindow({
    width: geometry.width,
    height: geometry.height,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    hasShadow: false,
    acceptFirstMouse: true,
    focusable: mode === 'board',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false
    }
  })

  win.setAlwaysOnTop(true, 'screen-saver')
  win.setIgnoreMouseEvents(mode === 'badge', { forward: true })
  placeInitial()

  win.loadFile(path.join(__dirname, 'index.html'))

  // The first poll usually wins the race against the renderer's own load, so
  // whatever we already know is replayed the moment the page can hear it.
  win.webContents.on('did-finish-load', () => {
    win.webContents.send('wow:mode', { mode })
    if (latestRoster) {
      win.webContents.send('wow:roster', latestRoster)
    }
  })

  win.once('ready-to-show', () => {
    if (HEADLESS) {
      // A self test renders the real markup without ever mapping a window over
      // whatever the user is doing.
      win.webContents.send('wow:mode', { mode })
      return
    }

    win.show()
    void pinWindow(mode)
  })

  win.on('closed', () => {
    win = null
  })
}

ipcMain.on('wow:reply', (_event, payload) => {
  void reply(payload?.sessionId, payload?.text)
})

ipcMain.on('wow:command', (_event, command) => {
  void handleCommand({ cmd: command })
})

void (async () => {
  await app.whenReady()

  // Two overlays would fight over the same socket and paint two bars over the
  // game. Electron's own lock is the authority; the socket probe stays as the
  // belt to that braces.
  if (!SELF_TEST && !app.requestSingleInstanceLock()) {
    log('another overlay owns the Electron lock; exiting')
    app.exit(0)
    return
  }

  if (!SELF_TEST && (await alreadyRunning())) {
    log('another overlay already owns the control socket; exiting')
    app.exit(0)
    return
  }

  createWindow()
  startControlServer()
  startRosterLoop()
})()

app.on('window-all-closed', () => app.quit())
