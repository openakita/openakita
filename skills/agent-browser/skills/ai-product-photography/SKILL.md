---
name: ai-product-photography
description: "Generate professional AI product photography and commercial images. Models: FLUX, Imagen 3, Grok, Seedream for product shots, lifestyle images, mockups. Capabilities: studio lighting, lifestyle scenes, packaging, e-commerce photos. Use for: e-commerce, Amazon listings, Shopify, marketing, advertising, mockups. Triggers: product photography, product shot, commercial photography, e-commerce images, amazon product photo, shopify images, product mockup, studio product shot, lifestyle product image, advertising photo, packshot, product render, product image ai"
allowed-tools: Bash(infsh *)
---

# AI Product Photography

Generate professional product photography via [inference.sh](https://inference.sh) CLI.

![AI Product Photography](https://cloud.inference.sh/app/files/u/4mg21r6ta37mpaz6ktzwtt8krr/01kg0v0nz7wv0qwqjtq1cam52z.jpeg)

## Quick Start

```bash
curl -fsSL https://cli.inference.sh | sh && infsh login

# Generate product shot
infsh app run falai/flux-dev --input '{
  "prompt": "Professional product photo of wireless earbuds on white surface, soft studio lighting, commercial photography, high detail"
}'
```

> **Install note:** The [install script](https://cli.inference.sh) only detects your OS/architecture, downloads the matching binary from `dist.inference.sh`, and verifies its SHA-256 checksum. No elevated permissions or background processes. [Manual install & verification](https://dist.inference.sh/cli/checksums.txt) available.

## Available Models

| Model | App ID | Best For |
|-------|--------|----------|
| FLUX Dev | `falai/flux-dev` | High quality, detailed |
| FLUX Schnell | `falai/flux-schnell` | Fast iterations |
| Imagen 3 | `google/imagen-3` | Photorealistic |
| Grok | `xai/grok-imagine-image` | Creative variations |
| Seedream | `bytedance/seedream-3-0` | Commercial quality |

## Product Photography Styles

### Studio White Background

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Product photography of a luxury watch on pure white background, professional studio lighting, sharp focus, e-commerce style, high resolution"
}'
```

### Lifestyle Context

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Lifestyle product photo of coffee mug on wooden desk, morning sunlight through window, cozy home office setting, Instagram aesthetic"
}'
```

### Hero Shot

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Hero product shot of smartphone floating at angle, dramatic lighting, gradient background, tech advertising style, premium feel"
}'
```

### Flat Lay

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Flat lay product photography of skincare products arranged aesthetically, marble surface, eucalyptus leaves as props, beauty brand style"
}'
```

### In-Use / Action

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Action shot of running shoes mid-stride, motion blur background, athletic lifestyle, Nike advertisement style"
}'
```

## Product Categories

### Electronics

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Professional product photo of wireless headphones, matte black finish, floating on dark gradient background, rim lighting, tech product photography"
}'
```

### Fashion / Apparel

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Fashion product photography of leather handbag, studio setting, soft shadows, luxury brand aesthetic, Vogue style"
}'
```

### Beauty / Cosmetics

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Beauty product photography of lipstick with color swatches, clean white background, soft lighting, high-end cosmetics advertising"
}'
```

### Food & Beverage

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Food photography of craft beer bottle with condensation, rustic wooden table, warm lighting, artisanal brand aesthetic"
}'
```

### Home & Furniture

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Interior product photo of modern armchair in minimalist living room, natural lighting, Scandinavian design style, lifestyle context"
}'
```

### Jewelry

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Jewelry product photography of diamond ring, black velvet surface, dramatic spotlight, sparkle and reflection, luxury advertising"
}'
```

## Lighting Techniques

### Soft Studio Light

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Product photo with soft diffused studio lighting, minimal shadows, clean and professional, commercial photography"
}'
```

### Dramatic / Rim Light

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Product photo with dramatic rim lighting, dark background, glowing edges, premium tech aesthetic"
}'
```

### Natural Window Light

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Product photo with natural window light, soft shadows, lifestyle setting, warm and inviting"
}'
```

### Hard Light / High Contrast

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Product photo with hard directional lighting, strong shadows, bold contrast, editorial style"
}'
```

## E-Commerce Templates

### Amazon Main Image

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Amazon product listing main image, pure white background RGB 255 255 255, product fills 85% of frame, professional studio lighting, no text or graphics"
}'
```

### Amazon Lifestyle Image

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Amazon lifestyle product image, product in natural use context, relatable setting, shows scale and use case"
}'
```

### Shopify Hero

```bash
infsh app run falai/flux-dev --input '{
  "prompt": "Shopify hero banner product image, lifestyle context, space for text overlay on left, premium brand aesthetic"
}'
```

## Batch Generation

```bash
# Generate multiple angles
PRODUCT="luxury watch"
ANGLES=("front view" "45 degree angle" "side profile" "detail shot of face")

for angle in "${ANGLES[@]}"; do
  infsh app run falai/flux-dev --input "{
    \"prompt\": \"Professional product photography of $PRODUCT, $angle, white background, studio lighting\"
  }" > "product_${angle// /_}.json"
done
```

## Post-Processing Workflow

```bash
# 1. Generate base product image
infsh app run falai/flux-dev --input '{
  "prompt": "Product photo of headphones..."
}' > product.json

# 2. Upscale for high resolution
infsh app run falai/topaz-image-upscaler --input '{
  "image_url": "<product-url>",
  "scale": 2
}' > upscaled.json

# 3. Remove background if needed
infsh app run falai/birefnet --input '{
  "image_url": "<upscaled-url>"
}' > cutout.json
```

## Prompt Formula

```
[Product Type] + [Setting/Background] + [Lighting] + [Style] + [Technical]
```

### Examples

```
"Wireless earbuds on white marble surface, soft studio lighting, Apple advertising style, 8K, sharp focus"

"Sneakers floating on gradient background, dramatic rim lighting, Nike campaign aesthetic, commercial photography"

"Skincare bottle with water droplets, spa setting with stones, natural lighting, luxury beauty brand style"
```

## Best Practices

1. **Consistent style** - Match brand aesthetic across all images
2. **High resolution** - Use quality models, upscale if needed
3. **Multiple angles** - Generate front, side, detail views
4. **Context matters** - Lifestyle images convert better than plain white
5. **Props and staging** - Add relevant props for visual interest
6. **Lighting consistency** - Same lighting style across product line

## Related Skills

```bash
# Image generation models
npx skills add inference-sh/skills@ai-image-generation

# FLUX specific
npx skills add inference-sh/skills@flux-image

# Image upscaling
npx skills add inference-sh/skills@image-upscaling

# Background removal
npx skills add inference-sh/skills@background-removal

# Full platform skill
npx skills add inference-sh/skills@inference-sh
```

Browse all image apps: `infsh app list --category image`
