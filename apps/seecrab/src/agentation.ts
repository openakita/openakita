import { createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { Agentation } from 'agentation'

const container = document.createElement('div')
container.id = 'agentation-root'
document.body.appendChild(container)

const root = createRoot(container)
root.render(createElement(Agentation))
