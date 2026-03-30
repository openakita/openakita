export interface PixelAppearance {
  bodyType?: 'slim' | 'average' | 'stocky';
  skinTone?: number;
  hairStyle?: number;
  hairColor?: string;
  outfitColor?: string;
  outfitStyle?: string;
  accessory?: string;
  facialFeature?: string;
  description?: string;
}

export interface CharacterRenderOptions {
  color?: string;
  icon?: string;
  name?: string;
  customAppearance?: PixelAppearance | null;
  theme?: string;
}

export const SKIN_TONES = [
  '#FFDBB4', '#F5C999', '#D4A574', '#C68642', '#8D5524', '#5C3317',
];

export const HAIR_COLORS = [
  '#2C1B0E', '#5A3825', '#8B6914', '#C4A35A', '#E8D5B7', '#D44',
  '#FF6B6B', '#4A90D9', '#7B68EE', '#2ECC71',
];

export const BODY_DIMS: Record<string, { w: number; h: number }> = {
  slim: { w: 10, h: 16 },
  average: { w: 12, h: 16 },
  stocky: { w: 14, h: 15 },
};

export const ACCESSORY_LIST = [
  'none', 'glasses', 'headphones', 'hardhat', 'beret', 'crown', 'tie', 'mask',
] as const;
