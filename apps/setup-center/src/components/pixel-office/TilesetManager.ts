import type { SceneTheme } from './SceneTheme';

export const TILE_SIZE = 32;
export const TILESET_CELL = 16;

export const TILE = {
  EMPTY: 0,
  FLOOR: 1,
  FLOOR_ALT: 2,
  WALL_TOP: 3,
  WALL_SIDE: 4,
  WALL_CORNER: 5,
  DESK: 6,
  CHAIR: 7,
  MEETING_TABLE: 8,
  SOFA: 9,
  SERVER: 10,
  DOOR: 11,
  PLANT: 12,
  WHITEBOARD: 13,
  PROJECTOR: 14,
  COFFEE: 15,
} as const;

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.substring(0, 2), 16),
    parseInt(h.substring(2, 4), 16),
    parseInt(h.substring(4, 6), 16),
  ];
}

function darken(r: number, g: number, b: number, f = 0.7): [number, number, number] {
  return [Math.round(r * f), Math.round(g * f), Math.round(b * f)];
}

function lighten(r: number, g: number, b: number, f = 0.3): [number, number, number] {
  return [
    Math.round(r + (255 - r) * f),
    Math.round(g + (255 - g) * f),
    Math.round(b + (255 - b) * f),
  ];
}

function fill(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, color: [number, number, number]) {
  ctx.fillStyle = `rgb(${color[0]},${color[1]},${color[2]})`;
  ctx.fillRect(x, y, w, h);
}

export class TilesetManager {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  readonly tileCount = 16;

  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.width = TILESET_CELL * this.tileCount;
    this.canvas.height = TILESET_CELL;
    this.ctx = this.canvas.getContext('2d')!;
  }

  generateTileset(theme: SceneTheme): HTMLCanvasElement {
    const ctx = this.ctx;
    const S = TILESET_CELL;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    const floorRgb = hexToRgb(theme.palette.floor[0]);
    const floorAlt = hexToRgb(theme.palette.floor[1]);
    const wallRgb = hexToRgb(theme.palette.wall[0]);
    const wallAlt = hexToRgb(theme.palette.wall[1]);
    const accentRgb = hexToRgb(theme.palette.accent);

    // TILE 0: empty (transparent)

    // TILE 1: floor
    fill(ctx, S * TILE.FLOOR, 0, S, S, floorRgb);
    fill(ctx, S * TILE.FLOOR + S - 1, 0, 1, S, darken(...floorRgb, 0.85));
    fill(ctx, S * TILE.FLOOR, S - 1, S, 1, darken(...floorRgb, 0.85));

    // TILE 2: floor alt (checkerboard)
    fill(ctx, S * TILE.FLOOR_ALT, 0, S, S, floorAlt);
    fill(ctx, S * TILE.FLOOR_ALT + S - 1, 0, 1, S, darken(...floorAlt, 0.85));
    fill(ctx, S * TILE.FLOOR_ALT, S - 1, S, 1, darken(...floorAlt, 0.85));

    // TILE 3: wall top
    fill(ctx, S * TILE.WALL_TOP, 0, S, S, wallRgb);
    fill(ctx, S * TILE.WALL_TOP, S - 2, S, 2, darken(...wallRgb, 0.6));

    // TILE 4: wall side
    fill(ctx, S * TILE.WALL_SIDE, 0, S, S, wallAlt);
    fill(ctx, S * TILE.WALL_SIDE + S - 2, 0, 2, S, darken(...wallAlt, 0.6));

    // TILE 5: wall corner
    fill(ctx, S * TILE.WALL_CORNER, 0, S, S, wallRgb);
    fill(ctx, S * TILE.WALL_CORNER, S - 2, S, 2, darken(...wallRgb, 0.5));
    fill(ctx, S * TILE.WALL_CORNER + S - 2, 0, 2, S, darken(...wallRgb, 0.5));

    // TILE 6: desk
    const deskC = hexToRgb('#' + (theme.furniture.workstation.tint).toString(16).padStart(6, '0'));
    fill(ctx, S * TILE.DESK, 0, S, S, floorRgb);
    fill(ctx, S * TILE.DESK + 2, 2, S - 4, S - 6, deskC);
    fill(ctx, S * TILE.DESK + 2, 2, S - 4, 2, lighten(...deskC, 0.2));
    // Monitor on desk
    fill(ctx, S * TILE.DESK + 5, 4, 6, 4, [40, 40, 50]);
    fill(ctx, S * TILE.DESK + 6, 5, 4, 2, [100, 180, 255]);

    // TILE 7: chair
    const chairC = hexToRgb('#' + (theme.furniture.chair.tint).toString(16).padStart(6, '0'));
    fill(ctx, S * TILE.CHAIR, 0, S, S, floorRgb);
    fill(ctx, S * TILE.CHAIR + 4, 4, 8, 8, chairC);
    fill(ctx, S * TILE.CHAIR + 5, 5, 6, 6, lighten(...chairC, 0.2));

    // TILE 8: meeting table
    const mtC = hexToRgb('#' + (theme.furniture.meetingTable.tint).toString(16).padStart(6, '0'));
    fill(ctx, S * TILE.MEETING_TABLE, 0, S, S, floorRgb);
    fill(ctx, S * TILE.MEETING_TABLE + 1, 3, S - 2, S - 6, mtC);
    fill(ctx, S * TILE.MEETING_TABLE + 1, 3, S - 2, 2, lighten(...mtC, 0.15));

    // TILE 9: sofa/rest
    const sofaC = hexToRgb('#' + (theme.furniture.restArea.tint).toString(16).padStart(6, '0'));
    fill(ctx, S * TILE.SOFA, 0, S, S, floorRgb);
    fill(ctx, S * TILE.SOFA + 2, 4, S - 4, S - 6, sofaC);
    fill(ctx, S * TILE.SOFA + 2, 4, 2, S - 6, darken(...sofaC, 0.7));
    fill(ctx, S * TILE.SOFA + S - 4, 4, 2, S - 6, darken(...sofaC, 0.7));

    // TILE 10: server/debug
    const srvC = hexToRgb('#' + (theme.furniture.debugStation.tint).toString(16).padStart(6, '0'));
    fill(ctx, S * TILE.SERVER, 0, S, S, floorRgb);
    fill(ctx, S * TILE.SERVER + 2, 1, S - 4, S - 2, srvC);
    // Blinking lights
    fill(ctx, S * TILE.SERVER + 4, 3, 2, 2, [0, 255, 100]);
    fill(ctx, S * TILE.SERVER + 8, 3, 2, 2, [255, 200, 0]);
    fill(ctx, S * TILE.SERVER + 4, 7, 2, 2, accentRgb);

    // TILE 11: door
    const doorC = hexToRgb('#' + (theme.furniture.entrance.tint).toString(16).padStart(6, '0'));
    fill(ctx, S * TILE.DOOR, 0, S, S, floorRgb);
    fill(ctx, S * TILE.DOOR + 3, 0, S - 6, S, doorC);
    fill(ctx, S * TILE.DOOR + 3, 0, S - 6, 2, lighten(...doorC, 0.3));
    fill(ctx, S * TILE.DOOR + S - 5, S / 2 - 1, 2, 2, [255, 215, 0]);

    // TILE 12: plant
    fill(ctx, S * TILE.PLANT, 0, S, S, floorRgb);
    fill(ctx, S * TILE.PLANT + 6, 8, 4, 6, [100, 70, 40]);
    fill(ctx, S * TILE.PLANT + 3, 2, 10, 8, [50, 150, 50]);
    fill(ctx, S * TILE.PLANT + 5, 0, 6, 4, [70, 180, 70]);

    // TILE 13: whiteboard
    fill(ctx, S * TILE.WHITEBOARD, 0, S, S, wallRgb);
    fill(ctx, S * TILE.WHITEBOARD + 2, 2, S - 4, S - 4, [240, 240, 240]);
    fill(ctx, S * TILE.WHITEBOARD + 2, 2, S - 4, 1, [180, 180, 180]);
    fill(ctx, S * TILE.WHITEBOARD + 4, 5, 3, 1, accentRgb);
    fill(ctx, S * TILE.WHITEBOARD + 4, 7, 5, 1, [200, 80, 80]);

    // TILE 14: projector
    fill(ctx, S * TILE.PROJECTOR, 0, S, S, floorRgb);
    fill(ctx, S * TILE.PROJECTOR + 4, 0, 8, 4, [80, 80, 80]);
    fill(ctx, S * TILE.PROJECTOR + 6, 4, 4, 2, [60, 60, 60]);
    fill(ctx, S * TILE.PROJECTOR + 7, 6, 2, 8, [70, 70, 70]);

    // TILE 15: coffee machine
    fill(ctx, S * TILE.COFFEE, 0, S, S, floorRgb);
    fill(ctx, S * TILE.COFFEE + 4, 4, 8, 10, [80, 60, 40]);
    fill(ctx, S * TILE.COFFEE + 5, 2, 6, 3, [100, 80, 60]);
    fill(ctx, S * TILE.COFFEE + 9, 8, 3, 2, [60, 40, 20]);

    return this.canvas;
  }

  getCanvas(): HTMLCanvasElement {
    return this.canvas;
  }
}
