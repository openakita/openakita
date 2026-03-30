import {
  type PixelAppearance,
  type CharacterRenderOptions,
  SKIN_TONES,
  HAIR_COLORS,
  BODY_DIMS,
} from './appearance-types';
import { AvatarCache } from './AvatarCache';

const SPRITE_SIZE = 32;

function djb2(str: string): number {
  let hash = 5381;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) + hash + str.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.substring(0, 2), 16),
    parseInt(h.substring(2, 4), 16),
    parseInt(h.substring(4, 6), 16),
  ];
}

function darken(hex: string, factor = 0.7): string {
  const [r, g, b] = hexToRgb(hex);
  const f = Math.max(0, Math.min(1, factor));
  return `rgb(${Math.round(r * f)},${Math.round(g * f)},${Math.round(b * f)})`;
}

function lighten(hex: string, factor = 0.3): string {
  const [r, g, b] = hexToRgb(hex);
  return `rgb(${Math.round(r + (255 - r) * factor)},${Math.round(g + (255 - g) * factor)},${Math.round(b + (255 - b) * factor)})`;
}

function drawPixel(ctx: CanvasRenderingContext2D, x: number, y: number, size = 1) {
  ctx.fillRect(x, y, size, size);
}

interface ResolvedLook {
  skinColor: string;
  hairColor: string;
  hairStyle: number;
  bodyType: 'slim' | 'average' | 'stocky';
  outfitColor: string;
  accessory: string;
}

function resolveLook(agentId: string, opts: CharacterRenderOptions): ResolvedLook {
  const hash = djb2(agentId);
  const app = opts.customAppearance;

  const skinToneIdx = app?.skinTone ?? (hash % SKIN_TONES.length);
  const skinColor = SKIN_TONES[skinToneIdx % SKIN_TONES.length];

  const hairColorIdx = hash % HAIR_COLORS.length;
  const hairColor = app?.hairColor ?? HAIR_COLORS[hairColorIdx];

  const hairStyle = app?.hairStyle ?? ((hash >> 4) % 6);

  const bodyTypes: Array<'slim' | 'average' | 'stocky'> = ['slim', 'average', 'stocky'];
  const bodyType = app?.bodyType ?? bodyTypes[(hash >> 8) % 3];

  const outfitColor = app?.outfitColor ?? opts.color ?? '#4A90D9';
  const accessory = app?.accessory ?? 'none';

  return { skinColor, hairColor, hairStyle, bodyType, outfitColor, accessory };
}

function drawCharacter(ctx: CanvasRenderingContext2D, look: ResolvedLook) {
  const { skinColor, hairColor, hairStyle, bodyType, outfitColor, accessory } = look;
  const dims = BODY_DIMS[bodyType];
  const cx = Math.floor((SPRITE_SIZE - dims.w) / 2);
  const headY = 4;
  const headSize = 10;
  const bodyY = headY + headSize;
  const bodyH = dims.h - headSize - 2;

  // Shadow
  ctx.fillStyle = 'rgba(0,0,0,0.15)';
  ctx.beginPath();
  ctx.ellipse(SPRITE_SIZE / 2, 29, dims.w / 2 + 1, 2, 0, 0, Math.PI * 2);
  ctx.fill();

  // Legs
  ctx.fillStyle = darken(outfitColor, 0.5);
  const legW = 3;
  const legGap = 2;
  const legX1 = cx + Math.floor(dims.w / 2) - legW - Math.floor(legGap / 2);
  const legX2 = cx + Math.floor(dims.w / 2) + Math.floor(legGap / 2);
  ctx.fillRect(legX1, bodyY + bodyH, legW, 4);
  ctx.fillRect(legX2, bodyY + bodyH, legW, 4);

  // Shoes
  ctx.fillStyle = '#333';
  ctx.fillRect(legX1 - 1, bodyY + bodyH + 3, legW + 1, 2);
  ctx.fillRect(legX2, bodyY + bodyH + 3, legW + 1, 2);

  // Body (torso)
  ctx.fillStyle = outfitColor;
  ctx.fillRect(cx, bodyY, dims.w, bodyH);
  // Collar
  ctx.fillStyle = lighten(outfitColor, 0.3);
  ctx.fillRect(cx + 3, bodyY, dims.w - 6, 2);

  // Arms
  ctx.fillStyle = outfitColor;
  ctx.fillRect(cx - 3, bodyY + 1, 3, bodyH - 2);
  ctx.fillRect(cx + dims.w, bodyY + 1, 3, bodyH - 2);
  // Hands
  ctx.fillStyle = skinColor;
  ctx.fillRect(cx - 3, bodyY + bodyH - 2, 3, 2);
  ctx.fillRect(cx + dims.w, bodyY + bodyH - 2, 3, 2);

  // Head
  ctx.fillStyle = skinColor;
  ctx.fillRect(cx + 1, headY, headSize - 2, headSize);
  ctx.fillRect(cx, headY + 1, headSize, headSize - 2);

  // Eyes
  ctx.fillStyle = '#222';
  drawPixel(ctx, cx + 3, headY + 4);
  drawPixel(ctx, cx + headSize - 4, headY + 4);

  // Mouth
  ctx.fillStyle = darken(skinColor, 0.6);
  ctx.fillRect(cx + 4, headY + 7, 2, 1);

  // Hair
  ctx.fillStyle = hairColor;
  switch (hairStyle % 6) {
    case 0: // short flat
      ctx.fillRect(cx, headY, headSize, 3);
      ctx.fillRect(cx - 1, headY, 1, 4);
      ctx.fillRect(cx + headSize, headY, 1, 4);
      break;
    case 1: // spiky
      ctx.fillRect(cx, headY, headSize, 2);
      for (let i = 0; i < headSize; i += 2) drawPixel(ctx, cx + i, headY - 1);
      break;
    case 2: // side part
      ctx.fillRect(cx, headY, headSize, 3);
      ctx.fillRect(cx - 1, headY, 2, 6);
      break;
    case 3: // long
      ctx.fillRect(cx, headY, headSize, 3);
      ctx.fillRect(cx - 1, headY, 1, headSize);
      ctx.fillRect(cx + headSize, headY, 1, headSize);
      break;
    case 4: // bald (no hair, just top line)
      ctx.fillRect(cx + 1, headY, headSize - 2, 1);
      break;
    case 5: // pompadour
      ctx.fillRect(cx, headY, headSize, 2);
      ctx.fillRect(cx + 1, headY - 2, headSize - 2, 2);
      ctx.fillRect(cx + 2, headY - 3, headSize - 4, 1);
      break;
  }

  // Accessory
  switch (accessory) {
    case 'glasses':
      ctx.fillStyle = '#333';
      ctx.fillRect(cx + 2, headY + 4, 3, 2);
      ctx.fillRect(cx + headSize - 5, headY + 4, 3, 2);
      ctx.fillRect(cx + 5, headY + 4, headSize - 10, 1);
      ctx.fillStyle = 'rgba(100,180,255,0.4)';
      drawPixel(ctx, cx + 3, headY + 4);
      drawPixel(ctx, cx + headSize - 4, headY + 4);
      break;
    case 'headphones':
      ctx.fillStyle = '#555';
      ctx.fillRect(cx - 2, headY + 2, 2, 5);
      ctx.fillRect(cx + headSize, headY + 2, 2, 5);
      ctx.fillRect(cx, headY - 1, headSize, 1);
      break;
    case 'hardhat':
      ctx.fillStyle = '#FFD700';
      ctx.fillRect(cx - 1, headY - 1, headSize + 2, 3);
      ctx.fillRect(cx + 1, headY - 2, headSize - 2, 1);
      break;
    case 'beret':
      ctx.fillStyle = '#C0392B';
      ctx.fillRect(cx, headY - 1, headSize, 2);
      ctx.fillRect(cx + 2, headY - 2, headSize - 2, 1);
      break;
    case 'crown':
      ctx.fillStyle = '#FFD700';
      ctx.fillRect(cx + 1, headY - 2, headSize - 2, 2);
      for (let i = 0; i < 3; i++) drawPixel(ctx, cx + 2 + i * 3, headY - 3);
      break;
    case 'tie':
      ctx.fillStyle = '#C0392B';
      ctx.fillRect(cx + Math.floor(dims.w / 2) - 1, bodyY + 2, 2, bodyH - 3);
      ctx.fillRect(cx + Math.floor(dims.w / 2) - 2, bodyY + 2, 4, 2);
      break;
    case 'mask':
      ctx.fillStyle = '#EEE';
      ctx.fillRect(cx + 1, headY + 5, headSize - 2, 4);
      break;
  }
}

let _instance: CharacterComposer | null = null;

export class CharacterComposer {
  private offscreen: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;

  private constructor() {
    this.offscreen = document.createElement('canvas');
    this.offscreen.width = SPRITE_SIZE;
    this.offscreen.height = SPRITE_SIZE;
    this.ctx = this.offscreen.getContext('2d')!;
  }

  static getInstance(): CharacterComposer {
    if (!_instance) _instance = new CharacterComposer();
    return _instance;
  }

  getAvatar(agentId: string, opts: CharacterRenderOptions = {}, version = 0): string {
    const cached = AvatarCache.get(agentId, version);
    if (cached) return cached;

    const look = resolveLook(agentId, opts);
    this.ctx.clearRect(0, 0, SPRITE_SIZE, SPRITE_SIZE);
    drawCharacter(this.ctx, look);

    const dataUrl = this.offscreen.toDataURL('image/png');
    AvatarCache.set(agentId, dataUrl, version);
    return dataUrl;
  }

  /** Render an idle-frame character onto the given canvas context at (dx,dy). */
  renderTo(
    targetCtx: CanvasRenderingContext2D,
    agentId: string,
    dx: number, dy: number,
    opts: CharacterRenderOptions = {},
  ): void {
    const look = resolveLook(agentId, opts);
    this.ctx.clearRect(0, 0, SPRITE_SIZE, SPRITE_SIZE);
    drawCharacter(this.ctx, look);
    targetCtx.drawImage(this.offscreen, dx, dy);
  }

  static getSpriteSize(): number {
    return SPRITE_SIZE;
  }
}
