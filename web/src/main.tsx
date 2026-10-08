import { createRoot } from 'react-dom/client'
import { Router } from '@/app/router'
import { Providers } from '@/app/providers'
import '@/styles/index.css'

const container = document.getElementById('root')
if (!container) throw new Error('#root not found')

createRoot(container).render(
  <Providers>
    <Router />
  </Providers>,
)
