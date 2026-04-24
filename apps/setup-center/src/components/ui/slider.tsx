import * as React from "react"
import * as SliderPrimitive from "@radix-ui/react-slider"

import { cn } from "@/lib/utils"

function Slider({
  className,
  defaultValue,
  value,
  min = 0,
  max = 100,
  ...props
}: React.ComponentProps<typeof SliderPrimitive.Root>) {
  const _values = React.useMemo(
    () => (value && value.length > 0 ? value : defaultValue && defaultValue.length > 0 ? defaultValue : [min]),
    [value, defaultValue, min],
  )

  return (
    <SliderPrimitive.Root
      data-slot="slider"
      defaultValue={defaultValue}
      value={value}
      min={min}
      max={max}
      className={cn(
        "relative flex w-full touch-none items-center select-none data-[disabled]:opacity-50",
        className,
      )}
      {...props}
    >
      <SliderPrimitive.Track
        data-slot="slider-track"
        className="bg-muted relative h-1.5 w-full grow overflow-hidden rounded-full"
      >
        <SliderPrimitive.Range
          data-slot="slider-range"
          className="bg-primary absolute h-full"
        />
      </SliderPrimitive.Track>
      {_values.map((_, i) => (
        <SliderPrimitive.Thumb
          data-slot="slider-thumb"
          key={i}
          className="border-primary bg-background ring-ring/50 block size-4 shrink-0 rounded-full border-2 shadow-sm transition-[color,box-shadow] hover:ring-4 focus-visible:ring-4 focus-visible:outline-none disabled:pointer-events-none"
        />
      ))}
    </SliderPrimitive.Root>
  )
}

export { Slider }

