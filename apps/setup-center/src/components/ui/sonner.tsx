import { createPortal } from "react-dom"
import {
  CircleCheckIcon,
  InfoIcon,
  Loader2Icon,
  OctagonXIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { Toaster as Sonner, type ToasterProps } from "sonner"

function useDocTheme() {
  const attr = document.documentElement.getAttribute("data-theme")
  return (attr === "dark" ? "dark" : "light") as ToasterProps["theme"]
}

const Toaster = ({ ...props }: ToasterProps) => {
  const theme = useDocTheme()

  const toaster = (
    <Sonner
      theme={theme}
      className="toaster group"
      icons={{
        success: <CircleCheckIcon className="size-4" />,
        info: <InfoIcon className="size-4" />,
        warning: <TriangleAlertIcon className="size-4" />,
        error: <OctagonXIcon className="size-4" />,
        loading: <Loader2Icon className="size-4 animate-spin" />,
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
          zIndex: 99999,
        } as React.CSSProperties
      }
      {...props}
    />
  )

  return createPortal(toaster, document.body)
}

export { Toaster }

