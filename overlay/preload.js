'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// The renderer is a view: it can read the roster, ask for a reply, and send a
// window command. It gets no Node, no filesystem, no Hermes credentials.
contextBridge.exposeInMainWorld('wow', {
  onRoster: callback => ipcRenderer.on('wow:roster', (_event, data) => callback(data)),
  onMode: callback => ipcRenderer.on('wow:mode', (_event, data) => callback(data)),
  reply: (sessionId, text) => ipcRenderer.send('wow:reply', { sessionId, text }),
  command: command => ipcRenderer.send('wow:command', command)
})
