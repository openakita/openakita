---
name: widgets-ui
description: "Declarative UI widgets from JSON for React/Next.js from ui.inference.sh. Render rich interactive UIs from structured agent responses. Capabilities: forms, buttons, cards, layouts, inputs, selects, checkboxes. Use for: agent-generated UIs, dynamic forms, data display, interactive cards. Triggers: widgets, declarative ui, json ui, widget renderer, agent widgets,"  dynamic ui, form widgets, card widgets, shadcn widgets, structured output ui
---

# Widget Renderer

Declarative UI from JSON via [ui.inference.sh](https://ui.inference.sh).

![Widget Renderer](https://cloud.inference.sh/u/33sqbmzt3mrg2xxphnhw5g5ear/01kah2rcteycyxw8ffmtpq1zec.png)

## Quick Start

```bash
npx shadcn@latest add https://ui.inference.sh/r/widgets.json
```

## Basic Usage

```tsx
import { WidgetRenderer } from "@/registry/blocks/widgets/widget-renderer"
import type { Widget, WidgetAction } from "@/registry/blocks/widgets/types"

const widget: Widget = {
  type: 'ui',
  title: 'My Widget',
  children: [
    { type: 'text', value: 'Hello World' },
    { type: 'button', label: 'Click me', onClickAction: { type: 'click' } },
  ],
}

<WidgetRenderer
  widget={widget}
  onAction={(action, formData) => console.log(action, formData)}
/>
```

## Widget Types

### Layout

```json
{ "type": "row", "gap": 2, "children": [...] }
{ "type": "col", "gap": 2, "children": [...] }
{ "type": "box", "background": "gradient-ocean", "padding": 4, "radius": "lg", "children": [...] }
```

### Typography

```json
{ "type": "title", "value": "Heading", "size": "2xl", "weight": "bold" }
{ "type": "text", "value": "Body text", "variant": "bold" }
{ "type": "caption", "value": "Small text" }
{ "type": "label", "value": "Field label", "fieldName": "email" }
```

### Interactive

```json
{ "type": "button", "label": "Submit", "variant": "default", "onClickAction": { "type": "submit" } }
{ "type": "input", "name": "email", "placeholder": "Enter email" }
{ "type": "textarea", "name": "message", "placeholder": "Your message" }
{ "type": "select", "name": "plan", "options": [{ "value": "pro", "label": "Pro" }] }
{ "type": "checkbox", "name": "agree", "label": "I agree", "defaultChecked": false }
```

### Display

```json
{ "type": "badge", "label": "New", "variant": "secondary" }
{ "type": "icon", "iconName": "check", "size": "lg" }
{ "type": "image", "src": "https://...", "alt": "Image", "width": 100, "height": 100 }
{ "type": "divider" }
```

## Example: Flight Card

```tsx
const flightWidget: Widget = {
  type: 'ui',
  children: [
    {
      type: 'box', background: 'gradient-ocean', padding: 4, radius: 'lg', children: [
        {
          type: 'row', justify: 'between', children: [
            {
              type: 'col', gap: 1, children: [
                { type: 'caption', value: 'departure' },
                { type: 'title', value: 'SFO', size: '2xl', weight: 'bold' },
              ]
            },
            { type: 'icon', iconName: 'plane', size: 'lg' },
            {
              type: 'col', gap: 1, align: 'end', children: [
                { type: 'caption', value: 'arrival' },
                { type: 'title', value: 'JFK', size: '2xl', weight: 'bold' },
              ]
            },
          ]
        },
      ]
    },
  ],
}
```

## Example: Form

```tsx
const formWidget: Widget = {
  type: 'ui',
  title: 'Contact Form',
  asForm: true,
  children: [
    {
      type: 'col', gap: 3, children: [
        { type: 'input', name: 'name', placeholder: 'Your name' },
        { type: 'input', name: 'email', placeholder: 'Email address' },
        { type: 'textarea', name: 'message', placeholder: 'Message' },
        { type: 'button', label: 'Send', variant: 'default', onClickAction: { type: 'submit' } },
      ]
    },
  ],
}
```

## Gradients

| Name | Description |
|------|-------------|
| `gradient-ocean` | Blue teal gradient |
| `gradient-sunset` | Orange pink gradient |
| `gradient-purple` | Purple gradient |
| `gradient-cool` | Cool blue gradient |
| `gradient-midnight` | Dark blue gradient |

## Handling Actions

```tsx
const handleAction = (action: WidgetAction, formData?: WidgetFormData) => {
  switch (action.type) {
    case 'submit':
      console.log('Form data:', formData)
      break
    case 'click':
      console.log('Button clicked')
      break
  }
}
```

## Related Skills

```bash
# Full agent component
npx skills add inference-sh/skills@agent-ui

# Chat UI blocks
npx skills add inference-sh/skills@chat-ui

# Tool UI
npx skills add inference-sh/skills@tools-ui
```

## Documentation

- [Widgets Overview](https://inference.sh/docs/agents/widgets/overview) - Understanding widgets
- [Widget Schema](https://inference.sh/docs/agents/widgets/schema) - Widget JSON structure
- [Agents That Generate UI](https://inference.sh/blog/ux/generative-ui) - Building generative UIs

Component docs: [ui.inference.sh/blocks/widgets](https://ui.inference.sh/blocks/widgets)
