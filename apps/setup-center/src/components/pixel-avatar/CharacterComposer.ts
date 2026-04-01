import {
  type PixelAppearance,
  type CharacterRenderOptions,
  SKIN_TONES,
  HAIR_COLORS,
  BODY_DIMS,
} from './appearance-types';
import { AvatarCache } from './AvatarCache';

const SPRITE_SIZE = 64;

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

function px(ctx: CanvasRenderingContext2D, x: number, y: number, w = 1, h = 1) {
  ctx.fillRect(x, y, w, h);
}

interface ResolvedLook {
  skinColor: string;
  hairColor: string;
  hairStyle: number;
  bodyType: 'slim' | 'average' | 'stocky' | 'akita';
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

  const hairStyle = app?.hairStyle ?? ((hash >>> 4) % 6);

  const bodyTypes: Array<'slim' | 'average' | 'stocky' | 'akita'> = ['slim', 'average', 'stocky'];
  const bodyType = app?.bodyType ?? bodyTypes[(hash >>> 8) % 3];

  const outfitColor = app?.outfitColor ?? opts.color ?? '#4A90D9';
  const accessory = app?.accessory ?? 'none';

  return { skinColor, hairColor, hairStyle, bodyType, outfitColor, accessory };
}

function drawAkita(ctx: CanvasRenderingContext2D, look: ResolvedLook) {
  const { outfitColor, accessory } = look;
  const bodyColor = '#F5A623';
  const furShade = '#D88912';
  const whiteFace = '#FFFFFF';
  const bellyLight = '#FFF6E8';
  const bandGray = '#666666';
  const cupCyan = '#4EEDC4';
  const cupInner = darken(cupCyan, 0.82);
  const collarGray = '#777777';
  const codeLight = '#CCCCCC';

  // Shadow (chibi round)
  ctx.fillStyle = 'rgba(0,0,0,0.10)';
  ctx.beginPath();
  ctx.ellipse(32, 58, 15, 4, 0, 0, Math.PI * 2);
  ctx.fill();

  // Tail (small curl)
  ctx.fillStyle = bodyColor;
  px(ctx, 46, 22, 4, 3);
  px(ctx, 48, 19, 3, 3);
  px(ctx, 50, 17, 2, 2);
  ctx.fillStyle = bellyLight;
  px(ctx, 47, 20, 2, 2);

  // Body — round chibi
  ctx.fillStyle = bodyColor;
  px(ctx, 15, 36, 34, 12);
  px(ctx, 13, 40, 38, 8);
  ctx.fillStyle = bellyLight;
  px(ctx, 20, 40, 24, 6);

  // Short stub legs
  ctx.fillStyle = bodyColor;
  px(ctx, 17, 48, 9, 7);
  px(ctx, 38, 48, 9, 7);
  ctx.fillStyle = furShade;
  px(ctx, 17, 53, 9, 2);
  px(ctx, 38, 53, 9, 2);

  // Head — large round
  ctx.fillStyle = bodyColor;
  px(ctx, 13, 16, 38, 22);
  px(ctx, 11, 20, 42, 14);

  // Ears
  ctx.fillStyle = furShade;
  px(ctx, 9, 12, 9, 11);
  px(ctx, 11, 9, 5, 4);
  px(ctx, 46, 12, 9, 11);
  px(ctx, 48, 9, 5, 4);
  ctx.fillStyle = '#E87820';
  px(ctx, 11, 14, 5, 7);
  px(ctx, 48, 14, 5, 7);

  // Tech headphones — band over top (behind face details visually at strip)
  ctx.fillStyle = bandGray;
  px(ctx, 10, 10, 44, 3);

  // White cheeks + muzzle
  ctx.fillStyle = whiteFace;
  px(ctx, 16, 24, 11, 11);
  px(ctx, 37, 24, 11, 11);
  px(ctx, 23, 22, 18, 13);

  // Three white forehead dots
  px(ctx, 25, 17, 2, 2);
  px(ctx, 31, 16, 2, 2);
  px(ctx, 37, 17, 2, 2);

  // Forehead diamond (logo accent)
  ctx.fillStyle = cupCyan;
  px(ctx, 30, 13, 2, 1);
  px(ctx, 29, 14, 4, 1);
  px(ctx, 28, 15, 6, 1);
  px(ctx, 29, 16, 4, 1);
  px(ctx, 30, 17, 2, 1);
  ctx.fillStyle = cupInner;
  px(ctx, 30, 15, 2, 1);

  // Big kawaii eyes + large white highlights
  ctx.fillStyle = '#0a0a0a';
  px(ctx, 19, 21, 8, 8);
  px(ctx, 37, 21, 8, 8);
  ctx.fillStyle = '#ffffff';
  px(ctx, 20, 22, 4, 4);
  px(ctx, 21, 23, 3, 3);
  px(ctx, 38, 22, 4, 4);
  px(ctx, 39, 23, 3, 3);
  px(ctx, 23, 25, 2, 2);
  px(ctx, 41, 25, 2, 2);
  ctx.fillStyle = '#ffffff';
  px(ctx, 24, 26, 1, 1);
  px(ctx, 42, 26, 1, 1);

  // Black nose (rounded block)
  ctx.fillStyle = '#0a0a0a';
  px(ctx, 26, 30, 12, 5);
  ctx.fillStyle = '#141414';
  px(ctx, 27, 31, 10, 3);
  px(ctx, 30, 34, 4, 2);

  // Collar + </> code mark
  ctx.fillStyle = collarGray;
  px(ctx, 17, 35, 30, 3);
  ctx.fillStyle = codeLight;
  // <
  px(ctx, 21, 36, 1, 1);
  px(ctx, 20, 37, 1, 1);
  px(ctx, 21, 38, 1, 1);
  // /
  px(ctx, 29, 36, 1, 1);
  px(ctx, 30, 37, 1, 1);
  px(ctx, 31, 38, 1, 1);
  // >
  px(ctx, 40, 36, 1, 1);
  px(ctx, 41, 37, 1, 1);
  px(ctx, 40, 38, 1, 1);

  // Headphone ear cups + small antennas
  ctx.fillStyle = cupCyan;
  px(ctx, 5, 18, 7, 13);
  px(ctx, 52, 18, 7, 13);
  ctx.fillStyle = cupInner;
  px(ctx, 6, 20, 4, 8);
  px(ctx, 54, 20, 4, 8);
  ctx.fillStyle = bandGray;
  px(ctx, 4, 22, 2, 8);
  px(ctx, 58, 22, 2, 8);
  // Antenna sticks
  px(ctx, 7, 5, 2, 6);
  px(ctx, 55, 5, 2, 6);
  px(ctx, 7, 3, 2, 2);
  px(ctx, 55, 3, 2, 2);

  // Accessory
  if (accessory === 'crown') {
    ctx.fillStyle = '#FFD700';
    px(ctx, 18, 0, 24, 4);
    ctx.fillStyle = '#FFE44D';
    for (let i = 0; i < 5; i++) px(ctx, 20 + i * 5, -2, 3, 3);
    ctx.fillStyle = '#E74C3C';
    px(ctx, 28, 1, 3, 2);
  } else if (accessory === 'tie') {
    ctx.fillStyle = outfitColor || '#C0392B';
    px(ctx, 28, 28, 4, 12);
    px(ctx, 26, 28, 8, 3);
  } else if (accessory === 'glasses') {
    ctx.fillStyle = '#333';
    px(ctx, 20, 18, 8, 3);
    px(ctx, 32, 18, 8, 3);
    px(ctx, 28, 19, 4, 1);
    ctx.fillStyle = 'rgba(100,180,255,0.35)';
    px(ctx, 22, 19, 4, 2);
    px(ctx, 34, 19, 4, 2);
  }
}

function drawCharacter(ctx: CanvasRenderingContext2D, look: ResolvedLook) {
  if (look.bodyType === 'akita') {
    drawAkita(ctx, look);
    return;
  }

  const { skinColor, hairColor, hairStyle, bodyType, outfitColor, accessory } = look;
  const dims = BODY_DIMS[bodyType] ?? BODY_DIMS.average;
  const cx = Math.floor((SPRITE_SIZE - dims.w) / 2);

  const headY = 6;
  const headW = 18;
  const headH = 18;
  const headCx = Math.floor((SPRITE_SIZE - headW) / 2);
  const bodyY = headY + headH;
  const bodyH = dims.h - headH - 4;

  // Shadow
  ctx.fillStyle = 'rgba(0,0,0,0.12)';
  ctx.beginPath();
  ctx.ellipse(SPRITE_SIZE / 2, 58, dims.w / 2 + 2, 3, 0, 0, Math.PI * 2);
  ctx.fill();

  // Legs
  ctx.fillStyle = darken(outfitColor, 0.45);
  const legW = 5;
  const legGap = 4;
  const legX1 = cx + Math.floor(dims.w / 2) - legW - Math.floor(legGap / 2);
  const legX2 = cx + Math.floor(dims.w / 2) + Math.floor(legGap / 2);
  const legY = bodyY + bodyH;
  px(ctx, legX1, legY, legW, 8);
  px(ctx, legX2, legY, legW, 8);

  // Shoes
  ctx.fillStyle = '#2a2a2a';
  px(ctx, legX1 - 1, legY + 6, legW + 2, 3);
  px(ctx, legX2 - 1, legY + 6, legW + 2, 3);

  // Body
  ctx.fillStyle = outfitColor;
  px(ctx, cx, bodyY, dims.w, bodyH);
  // Collar highlight
  ctx.fillStyle = lighten(outfitColor, 0.35);
  px(ctx, cx + 4, bodyY, dims.w - 8, 3);

  // Arms
  ctx.fillStyle = outfitColor;
  px(ctx, cx - 5, bodyY + 2, 5, bodyH - 3);
  px(ctx, cx + dims.w, bodyY + 2, 5, bodyH - 3);
  // Hands
  ctx.fillStyle = skinColor;
  px(ctx, cx - 5, bodyY + bodyH - 3, 5, 3);
  px(ctx, cx + dims.w, bodyY + bodyH - 3, 5, 3);

  // Head
  ctx.fillStyle = skinColor;
  px(ctx, headCx + 1, headY, headW - 2, headH);
  px(ctx, headCx, headY + 2, headW, headH - 4);

  // Eyes
  ctx.fillStyle = '#fff';
  px(ctx, headCx + 4, headY + 7, 4, 4);
  px(ctx, headCx + headW - 8, headY + 7, 4, 4);
  ctx.fillStyle = '#222';
  px(ctx, headCx + 5, headY + 8, 3, 3);
  px(ctx, headCx + headW - 7, headY + 8, 3, 3);
  ctx.fillStyle = '#fff';
  px(ctx, headCx + 5, headY + 8, 1, 1);
  px(ctx, headCx + headW - 7, headY + 8, 1, 1);

  // Mouth
  ctx.fillStyle = darken(skinColor, 0.55);
  px(ctx, headCx + 7, headY + 14, 4, 1);

  // Nose
  ctx.fillStyle = darken(skinColor, 0.7);
  px(ctx, headCx + 8, headY + 12, 2, 2);

  // Hair
  ctx.fillStyle = hairColor;
  switch (hairStyle % 6) {
    case 0: // short flat
      px(ctx, headCx, headY, headW, 5);
      px(ctx, headCx - 1, headY, 2, 7);
      px(ctx, headCx + headW - 1, headY, 2, 7);
      break;
    case 1: // spiky
      px(ctx, headCx, headY, headW, 4);
      for (let i = 0; i < headW; i += 3) px(ctx, headCx + i, headY - 3, 2, 4);
      break;
    case 2: // side part
      px(ctx, headCx, headY, headW, 5);
      px(ctx, headCx - 2, headY, 3, 10);
      break;
    case 3: // long
      px(ctx, headCx, headY, headW, 5);
      px(ctx, headCx - 2, headY, 2, headH);
      px(ctx, headCx + headW, headY, 2, headH);
      break;
    case 4: // bald
      px(ctx, headCx + 2, headY, headW - 4, 2);
      break;
    case 5: // pompadour
      px(ctx, headCx, headY, headW, 4);
      px(ctx, headCx + 2, headY - 4, headW - 4, 4);
      px(ctx, headCx + 4, headY - 6, headW - 8, 3);
      break;
  }

  // Accessories
  switch (accessory) {
    case 'glasses':
      ctx.fillStyle = '#333';
      px(ctx, headCx + 3, headY + 7, 5, 3);
      px(ctx, headCx + headW - 8, headY + 7, 5, 3);
      px(ctx, headCx + 8, headY + 8, headW - 16, 1);
      ctx.fillStyle = 'rgba(100,180,255,0.4)';
      px(ctx, headCx + 4, headY + 8, 3, 2);
      px(ctx, headCx + headW - 7, headY + 8, 3, 2);
      break;
    case 'headphones':
      ctx.fillStyle = '#444';
      px(ctx, headCx - 3, headY + 4, 3, 8);
      px(ctx, headCx + headW, headY + 4, 3, 8);
      px(ctx, headCx, headY - 2, headW, 2);
      break;
    case 'hardhat':
      ctx.fillStyle = '#FFD700';
      px(ctx, headCx - 2, headY - 2, headW + 4, 5);
      px(ctx, headCx + 2, headY - 4, headW - 4, 3);
      break;
    case 'beret':
      ctx.fillStyle = '#C0392B';
      px(ctx, headCx, headY - 2, headW, 4);
      px(ctx, headCx + 4, headY - 4, headW - 4, 3);
      break;
    case 'crown':
      ctx.fillStyle = '#FFD700';
      px(ctx, headCx + 2, headY - 4, headW - 4, 4);
      ctx.fillStyle = '#FFE44D';
      for (let i = 0; i < 3; i++) px(ctx, headCx + 3 + i * 5, headY - 6, 3, 3);
      ctx.fillStyle = '#E74C3C';
      px(ctx, headCx + Math.floor(headW / 2) - 1, headY - 3, 2, 2);
      break;
    case 'tie':
      ctx.fillStyle = '#C0392B';
      px(ctx, cx + Math.floor(dims.w / 2) - 1, bodyY + 3, 3, bodyH - 5);
      px(ctx, cx + Math.floor(dims.w / 2) - 2, bodyY + 3, 5, 3);
      break;
    case 'mask':
      ctx.fillStyle = '#EEE';
      px(ctx, headCx + 2, headY + 10, headW - 4, 6);
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
