import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './styles.css'

const container = document.getElementById('root')
if (!container) {
  // Fail loudly at startup rather than rendering nothing. A blank page with a
  // null-root error in the console is the hardest kind of frontend bug to
  // report.
  throw new Error('#root is missing from index.html')
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)