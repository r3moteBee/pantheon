import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'
import { setBackendUrl } from './api/client'

async function init() {
  if (window.__TAURI_INTERNALS__) {
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      const port = await invoke('get_backend_port')
      window.BACKEND_API_URL = `http://127.0.0.1:${port}`
      window.BACKEND_WS_URL = `ws://127.0.0.1:${port}`
      setBackendUrl(window.BACKEND_API_URL)
      console.log('Tauri backend port resolved:', port)
    } catch (e) {
      console.error('Failed to get backend port from Tauri:', e)
    }
  }

  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}

init()
