import React, { useEffect, useState } from "react"
import { createRoot } from "react-dom/client"
import App from "./App"
import Login from "./Login"
import { isAuthed, AUTH_CHANGE } from "./lib/auth"
import "./App.css"

function Root() {
  const [authed, setAuthed] = useState<boolean>(() => isAuthed())
  useEffect(() => {
    const onChange = () => setAuthed(isAuthed())
    window.addEventListener(AUTH_CHANGE, onChange)
    return () => window.removeEventListener(AUTH_CHANGE, onChange)
  }, [])
  if (!authed) return <Login />
  return <App />
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
)
