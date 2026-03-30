import type { SceneTheme } from './SceneTheme';
import { TILE, TILE_SIZE } from './TilesetManager';
import type { AreaType } from './StatusMapping';

export interface RoomDef {
  id: string;
  type: AreaType;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  seats: Array<{ x: number; y: number; id: string }>;
  department?: string;
}

export interface LayoutResult {
  mapWidth: number;
  mapHeight: number;
  tileData: number[][];
  rooms: RoomDef[];
}

interface DepartmentInfo {
  name: string;
  nodeIds: string[];
}

const ROOM_PADDING = 1;
const MIN_ROOM_W = 8;
const MIN_ROOM_H = 7;
const HALL_HEIGHT = 2;

function createEmptyMap(w: number, h: number): number[][] {
  return Array.from({ length: h }, () => new Array(w).fill(TILE.EMPTY));
}

function fillRect(map: number[][], x: number, y: number, w: number, h: number, tile: number) {
  for (let row = y; row < y + h && row < map.length; row++) {
    for (let col = x; col < x + w && col < map[0].length; col++) {
      map[row][col] = tile;
    }
  }
}

function drawRoom(map: number[][], rx: number, ry: number, rw: number, rh: number) {
  // Floor
  for (let row = ry + 1; row < ry + rh; row++) {
    for (let col = rx + 1; col < rx + rw - 1; col++) {
      map[row][col] = ((row + col) % 2 === 0) ? TILE.FLOOR : TILE.FLOOR_ALT;
    }
  }
  // Walls top
  fillRect(map, rx, ry, rw, 1, TILE.WALL_TOP);
  // Walls sides
  for (let row = ry + 1; row < ry + rh; row++) {
    map[row][rx] = TILE.WALL_SIDE;
    map[row][rx + rw - 1] = TILE.WALL_SIDE;
  }
  // Corners
  map[ry][rx] = TILE.WALL_CORNER;
  map[ry][rx + rw - 1] = TILE.WALL_CORNER;
}

function placeDoor(map: number[][], rx: number, ry: number, rw: number, rh: number) {
  const doorX = rx + Math.floor(rw / 2);
  const doorY = ry + rh - 1;
  if (doorY < map.length && doorX < map[0].length) {
    map[doorY][doorX] = TILE.DOOR;
  }
}

export function generateLayout(
  departments: DepartmentInfo[],
  theme: SceneTheme,
): LayoutResult {
  const rooms: RoomDef[] = [];
  const deptCount = Math.max(departments.length, 1);

  const maxNodesInDept = Math.max(...departments.map(d => d.nodeIds.length), 2);
  const deptRoomW = Math.max(MIN_ROOM_W, Math.ceil(maxNodesInDept * 2.5) + 4);
  const deptRoomH = Math.max(MIN_ROOM_H, Math.ceil(maxNodesInDept / 2) + 5);

  const meetingW = Math.max(MIN_ROOM_W + 2, Math.ceil(deptCount * 3) + 4);
  const meetingH = 7;

  const specialW = Math.max(MIN_ROOM_W, 8);
  const specialH = 6;

  // Layout grid: top row = entrance + break, middle = departments, bottom = meeting + debug
  const deptRowW = deptCount * (deptRoomW + ROOM_PADDING) + ROOM_PADDING;
  const totalW = Math.max(deptRowW, meetingW + specialW * 2 + ROOM_PADDING * 4) + 4;

  const entranceRow = ROOM_PADDING;
  const deptRow = entranceRow + specialH + HALL_HEIGHT + ROOM_PADDING;
  const meetingRow = deptRow + deptRoomH + HALL_HEIGHT + ROOM_PADDING;
  const totalH = meetingRow + meetingH + ROOM_PADDING + 2;

  const map = createEmptyMap(totalW, totalH);

  // Hallways (floor between rows)
  fillRect(map, 0, entranceRow + specialH, totalW, HALL_HEIGHT, TILE.FLOOR);
  fillRect(map, 0, deptRow + deptRoomH, totalW, HALL_HEIGHT, TILE.FLOOR);

  // Entrance room (top-left)
  const entranceX = ROOM_PADDING + 1;
  drawRoom(map, entranceX, entranceRow, specialW, specialH);
  placeDoor(map, entranceX, entranceRow, specialW, specialH);
  map[entranceRow + 3][entranceX + Math.floor(specialW / 2)] = TILE.DOOR;
  rooms.push({
    id: 'entrance',
    type: 'entrance',
    label: theme.roomLabels.entrance,
    x: entranceX, y: entranceRow, w: specialW, h: specialH,
    seats: [{ x: (entranceX + 2) * TILE_SIZE, y: (entranceRow + 3) * TILE_SIZE, id: 'entrance_0' }],
  });

  // Break room (top-right)
  const breakX = totalW - specialW - ROOM_PADDING - 1;
  drawRoom(map, breakX, entranceRow, specialW, specialH);
  placeDoor(map, breakX, entranceRow, specialW, specialH);
  map[entranceRow + 2][breakX + 2] = TILE.SOFA;
  map[entranceRow + 2][breakX + specialW - 3] = TILE.COFFEE;
  rooms.push({
    id: 'break',
    type: 'break',
    label: theme.roomLabels.breakRoom,
    x: breakX, y: entranceRow, w: specialW, h: specialH,
    seats: Array.from({ length: 4 }, (_, i) => ({
      x: (breakX + 2 + i) * TILE_SIZE,
      y: (entranceRow + 3) * TILE_SIZE,
      id: `break_${i}`,
    })),
  });

  // Department rooms (middle row)
  departments.forEach((dept, idx) => {
    const rx = ROOM_PADDING + 1 + idx * (deptRoomW + ROOM_PADDING);
    const ry = deptRow;
    drawRoom(map, rx, ry, deptRoomW, deptRoomH);
    placeDoor(map, rx, ry, deptRoomW, deptRoomH);

    const seats: RoomDef['seats'] = [];
    dept.nodeIds.forEach((nid, si) => {
      const seatCol = rx + 2 + (si % Math.floor((deptRoomW - 3) / 2)) * 2;
      const seatRow = ry + 2 + Math.floor(si / Math.floor((deptRoomW - 3) / 2)) * 2;

      if (seatRow < ry + deptRoomH - 1 && seatCol < rx + deptRoomW - 1) {
        map[seatRow][seatCol] = TILE.DESK;
        if (seatRow + 1 < ry + deptRoomH - 1) {
          map[seatRow + 1][seatCol] = TILE.CHAIR;
        }
        seats.push({ x: seatCol * TILE_SIZE, y: (seatRow + 1) * TILE_SIZE, id: nid });
      }
    });

    // Whiteboard on wall
    if (ry + 1 < map.length && rx + deptRoomW - 3 < map[0].length) {
      map[ry + 1][rx + deptRoomW - 3] = TILE.WHITEBOARD;
    }
    // Plant in corner
    if (ry + 1 < map.length && rx + 1 < map[0].length) {
      map[ry + 1][rx + 1] = TILE.PLANT;
    }

    rooms.push({
      id: `dept_${dept.name}`,
      type: 'department',
      label: `${theme.roomLabels.department} · ${dept.name}`,
      x: rx, y: ry, w: deptRoomW, h: deptRoomH,
      seats,
      department: dept.name,
    });
  });

  // Meeting room (bottom center)
  const meetingX = Math.floor((totalW - meetingW) / 2);
  drawRoom(map, meetingX, meetingRow, meetingW, meetingH);
  placeDoor(map, meetingX, meetingRow, meetingW, meetingH);

  // Meeting table in center
  const tableStartX = meetingX + Math.floor((meetingW - 4) / 2);
  for (let i = 0; i < 4; i++) {
    map[meetingRow + 3][tableStartX + i] = TILE.MEETING_TABLE;
  }
  map[meetingRow + 1][meetingX + meetingW - 3] = TILE.PROJECTOR;

  const meetingSeats: RoomDef['seats'] = [];
  const seatCount = Math.min(deptCount * 3, meetingW - 4);
  for (let i = 0; i < seatCount; i++) {
    const sx = meetingX + 2 + i;
    const sy = (i % 2 === 0) ? meetingRow + 2 : meetingRow + 4;
    meetingSeats.push({ x: sx * TILE_SIZE, y: sy * TILE_SIZE, id: `meeting_${i}` });
  }
  rooms.push({
    id: 'meeting',
    type: 'meeting',
    label: theme.roomLabels.meetingRoom,
    x: meetingX, y: meetingRow, w: meetingW, h: meetingH,
    seats: meetingSeats,
  });

  // Debug/server room (bottom-right)
  const debugX = totalW - specialW - ROOM_PADDING - 1;
  drawRoom(map, debugX, meetingRow, specialW, specialH);
  placeDoor(map, debugX, meetingRow, specialW, specialH);
  map[meetingRow + 2][debugX + 2] = TILE.SERVER;
  map[meetingRow + 2][debugX + 4] = TILE.SERVER;
  rooms.push({
    id: 'debug',
    type: 'debug',
    label: theme.roomLabels.debugArea,
    x: debugX, y: meetingRow, w: specialW, h: specialH,
    seats: [{ x: (debugX + 3) * TILE_SIZE, y: (meetingRow + 3) * TILE_SIZE, id: 'debug_0' }],
  });

  // Public area: fill remaining hallway with floor
  for (let row = 0; row < totalH; row++) {
    for (let col = 0; col < totalW; col++) {
      if (map[row][col] === TILE.EMPTY) {
        // Leave outer border empty for visual margin
        if (row > 0 && row < totalH - 1 && col > 0 && col < totalW - 1) {
          map[row][col] = TILE.FLOOR;
        }
      }
    }
  }

  rooms.push({
    id: 'public',
    type: 'public',
    label: '公共区域',
    x: ROOM_PADDING, y: entranceRow + specialH,
    w: totalW - ROOM_PADDING * 2, h: HALL_HEIGHT,
    seats: Array.from({ length: 6 }, (_, i) => ({
      x: (4 + i * 3) * TILE_SIZE,
      y: (entranceRow + specialH + 1) * TILE_SIZE,
      id: `public_${i}`,
    })),
  });

  return {
    mapWidth: totalW,
    mapHeight: totalH,
    tileData: map,
    rooms,
  };
}
