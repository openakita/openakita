import Phaser from 'phaser';
import { EventBus } from './EventBus';
import { TilesetManager, TILE_SIZE, TILESET_CELL } from './TilesetManager';
import { generateLayout, generateSoloLayout, type RoomDef, type LayoutResult } from './RoomGenerator';
import { AgentSprite, type AgentSpriteConfig } from './AgentSprite';
import { ActivitySystem, type Activity } from './ActivitySystem';
import { getTheme, type SceneTheme } from './SceneTheme';
import { STATUS_TO_AREA, type NodeStatus } from './StatusMapping';

export interface OrgData {
  orgId: string;
  nodes: Array<{
    id: string;
    role_title: string;
    department: string;
    status: string;
    agent_profile_id?: string;
    level?: number;
  }>;
  agentProfiles: Record<string, {
    name: string;
    color: string;
    icon?: string;
    pixel_appearance?: Record<string, unknown> | null;
  }>;
}

export class OfficeScene extends Phaser.Scene {
  private tilesetManager!: TilesetManager;
  private layout: LayoutResult | null = null;
  private agentSprites = new Map<string, AgentSprite>();
  private activitySystem: ActivitySystem | null = null;
  private currentTheme: SceneTheme = getTheme('office');
  private orgData: OrgData | null = null;
  private roomLabels: Phaser.GameObjects.Text[] = [];
  private tilemapImage: Phaser.GameObjects.Image | null = null;
  private tilemapTextureKey = 'tilemap_texture';

  private _hActStart?: (a: Activity) => void;
  private _hActEnd?: (a: Activity) => void;
  private _hZoom?: (id: string) => void;

  constructor() {
    super({ key: 'OfficeScene' });
  }

  init(data: { theme?: string }) {
    if (data.theme) {
      this.currentTheme = getTheme(data.theme);
    }
  }

  preload() {
    // No external assets to preload — all generated programmatically
  }

  create() {
    this.tilesetManager = new TilesetManager();
    this.cameras.main.setBackgroundColor(this.currentTheme.palette.background);

    this._hActStart = (activity: Activity) => this.onActivityStart(activity);
    this._hActEnd = (activity: Activity) => this.onActivityEnd(activity);
    this._hZoom = (nodeId: string) => this.onZoomToNode(nodeId);

    EventBus.on('activity-start', this._hActStart);
    EventBus.on('activity-end', this._hActEnd);
    EventBus.on('zoom-to-node', this._hZoom);

    this.input.on('wheel', (
      _pointer: Phaser.Input.Pointer,
      _over: Phaser.GameObjects.GameObject[],
      _dx: number,
      deltaY: number,
    ) => {
      const cam = this.cameras.main;
      const factor = deltaY > 0 ? 0.9 : 1.1;
      cam.zoom = Phaser.Math.Clamp(cam.zoom * factor, 0.25, 5);
    });

    let dragStart: { x: number; y: number } | null = null;
    this.input.on('pointerdown', (pointer: Phaser.Input.Pointer) => {
      dragStart = { x: pointer.x, y: pointer.y };
    });
    this.input.on('pointermove', (pointer: Phaser.Input.Pointer) => {
      if (dragStart && pointer.isDown) {
        const dx = pointer.x - dragStart.x;
        const dy = pointer.y - dragStart.y;
        this.cameras.main.scrollX -= dx / this.cameras.main.zoom;
        this.cameras.main.scrollY -= dy / this.cameras.main.zoom;
        dragStart = { x: pointer.x, y: pointer.y };
      }
    });
    this.input.on('pointerup', () => { dragStart = null; });
  }

  private isAlive(): boolean {
    return !!(this.sys?.game?.renderer && this.cameras?.main);
  }

  private hasInitializedCamera = false;
  private lastOrgId = '';

  private rebuildScene() {
    if (!this.orgData || !this.isAlive()) return;

    const orgChanged = this.orgData.orgId !== this.lastOrgId;
    this.lastOrgId = this.orgData.orgId;
    if (orgChanged) this.hasInitializedCamera = false;

    const cam = this.cameras.main;
    const prevZoom = this.hasInitializedCamera ? cam.zoom : 0;
    const prevScrollX = this.hasInitializedCamera ? cam.scrollX : 0;
    const prevScrollY = this.hasInitializedCamera ? cam.scrollY : 0;

    // Clean up
    this.agentSprites.forEach(s => s.destroy());
    this.agentSprites.clear();
    this.roomLabels.forEach(l => l.destroy());
    this.roomLabels = [];
    if (this.tilemapImage) {
      this.tilemapImage.destroy();
      this.tilemapImage = null;
    }
    if (this.textures.exists(this.tilemapTextureKey)) {
      this.textures.remove(this.tilemapTextureKey);
    }
    this.activitySystem?.destroy();

    this.tilesetManager.generateTileset(this.currentTheme);

    const isSolo = this.orgData.nodes.length <= 1;
    if (isSolo) {
      const soloId = this.orgData.nodes[0]?.id ?? 'akita';
      this.layout = generateSoloLayout(soloId, this.currentTheme);
    } else {
      const deptMap = new Map<string, string[]>();
      for (const node of this.orgData.nodes) {
        const dept = node.department || '默认';
        if (!deptMap.has(dept)) deptMap.set(dept, []);
        deptMap.get(dept)!.push(node.id);
      }
      const departments = Array.from(deptMap.entries()).map(([name, nodeIds]) => ({ name, nodeIds }));
      this.layout = generateLayout(departments, this.currentTheme);
    }
    this.renderTilemap();
    this.renderRoomLabels();
    this.activitySystem = new ActivitySystem(this.layout.rooms);
    this.spawnAgents();

    if (this.hasInitializedCamera && prevZoom > 0) {
      cam.zoom = prevZoom;
      cam.scrollX = prevScrollX;
      cam.scrollY = prevScrollY;
    } else {
      const worldW = Math.max(this.layout.mapWidth * TILE_SIZE, 1);
      const worldH = Math.max(this.layout.mapHeight * TILE_SIZE, 1);
      cam.centerOn(worldW / 2, worldH / 2);
      const camW = cam.width || 800;
      const camH = cam.height || 600;
      const fitZoom = Math.min(camW / worldW, camH / worldH);
      cam.zoom = Phaser.Math.Clamp(fitZoom * 1.8, 0.5, 2.5);
      this.hasInitializedCamera = true;
    }
  }

  private renderTilemap() {
    if (!this.layout) return;

    const { tileData, mapWidth, mapHeight } = this.layout;
    const tilesetCanvas = this.tilesetManager.getCanvas();

    const worldW = mapWidth * TILE_SIZE;
    const worldH = mapHeight * TILE_SIZE;
    const offscreen = document.createElement('canvas');
    offscreen.width = worldW;
    offscreen.height = worldH;
    const ctx = offscreen.getContext('2d')!;

    ctx.imageSmoothingEnabled = false;

    for (let row = 0; row < mapHeight; row++) {
      for (let col = 0; col < mapWidth; col++) {
        const tileId = tileData[row]?.[col] ?? 0;
        if (tileId === 0) continue;
        ctx.drawImage(
          tilesetCanvas,
          tileId * TILESET_CELL, 0, TILESET_CELL, TILESET_CELL,
          col * TILE_SIZE, row * TILE_SIZE, TILE_SIZE, TILE_SIZE,
        );
      }
    }

    if (this.textures.exists(this.tilemapTextureKey)) {
      this.textures.remove(this.tilemapTextureKey);
    }
    this.textures.addCanvas(this.tilemapTextureKey, offscreen);

    this.tilemapImage = this.add.image(0, 0, this.tilemapTextureKey);
    this.tilemapImage.setOrigin(0, 0);
    this.tilemapImage.setDepth(0);
  }

  private renderRoomLabels() {
    if (!this.layout) return;

    for (const room of this.layout.rooms) {
      const label = this.add.text(
        (room.x + room.w / 2) * TILE_SIZE,
        room.y * TILE_SIZE - 6,
        room.label,
        {
          fontSize: '15px',
          fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
          color: '#f0f0f0',
          backgroundColor: '#000000cc',
          padding: { x: 10, y: 5 },
          align: 'center',
        },
      );
      label.setOrigin(0.5, 1);
      label.setDepth(5);
      this.roomLabels.push(label);
    }
  }

  private spawnAgents() {
    if (!this.orgData || !this.layout) return;

    // Solo mode — spawn mascot if no nodes
    if (this.orgData.nodes.length === 0) {
      const seat = this.layout.rooms[0]?.seats[0];
      if (seat) {
        const mascot = new AgentSprite(this, {
          nodeId: 'akita',
          name: 'OpenAkita',
          color: '#F5A623',
          pixelAppearance: { bodyType: 'akita' },
        }, seat.x + TILE_SIZE / 2, seat.y + TILE_SIZE / 2);
        this.agentSprites.set('akita', mascot);
      }
      return;
    }

    for (const node of this.orgData.nodes) {
      const profile = this.orgData.agentProfiles[node.agent_profile_id || node.id];
      const isCeo = /ceo|首席|总裁|总经理/i.test(node.role_title ?? '');
      let appearance = profile?.pixel_appearance ?? null;
      if (isCeo && !appearance) {
        appearance = { bodyType: 'akita' };
      }
      const config: AgentSpriteConfig = {
        nodeId: node.id,
        name: profile?.name ?? node.role_title,
        color: profile?.color ?? '#4A90D9',
        icon: profile?.icon,
        department: node.department,
        status: node.status,
        pixelAppearance: appearance,
      };

      // Find initial position based on status
      const areaType = STATUS_TO_AREA[(node.status as NodeStatus) ?? 'idle'] ?? 'department';
      const pos = this.findPositionForNode(node.id, node.department, areaType);

      const agentSprite = new AgentSprite(this, config, pos.x, pos.y);

      if (node.status === 'offline') {
        agentSprite.setVisible(false);
      }

      this.agentSprites.set(node.id, agentSprite);
    }
  }

  private findPositionForNode(nodeId: string, department: string, areaType: string): { x: number; y: number } {
    if (!this.layout) return { x: 100, y: 100 };

    // Try to find a seat in the matching room
    for (const room of this.layout.rooms) {
      if (areaType === 'department' && room.type === 'department' && room.department === department) {
        const seat = room.seats.find(s => s.id === nodeId);
        if (seat) return { x: seat.x + TILE_SIZE / 2, y: seat.y + TILE_SIZE / 2 };
      }
      if (room.type === areaType) {
        const availableSeat = room.seats.find(s =>
          !Array.from(this.agentSprites.values()).some(
            as => as.nodeId !== nodeId &&
              Math.abs(as.getPosition().x - (s.x + TILE_SIZE / 2)) < 4 &&
              Math.abs(as.getPosition().y - (s.y + TILE_SIZE / 2)) < 4,
          ),
        );
        if (availableSeat) return { x: availableSeat.x + TILE_SIZE / 2, y: availableSeat.y + TILE_SIZE / 2 };
      }
    }

    // Fallback: random position in a matching room
    const matchingRoom = this.layout.rooms.find(r => r.type === areaType)
      ?? this.layout.rooms.find(r => r.type === 'public');
    if (matchingRoom) {
      return {
        x: (matchingRoom.x + 2 + Math.random() * (matchingRoom.w - 4)) * TILE_SIZE,
        y: (matchingRoom.y + 2 + Math.random() * (matchingRoom.h - 3)) * TILE_SIZE,
      };
    }
    return { x: 100, y: 100 };
  }

  private onActivityStart(activity: Activity) {
    switch (activity.type) {
      case 'meeting_gather':
        this.handleMeetingGather(activity);
        break;
      case 'meeting_speak':
        this.handleMeetingSpeak(activity);
        break;
      case 'meeting_end':
        this.handleMeetingEnd(activity);
        break;
      case 'task_delegate':
        this.handleTaskDelegate(activity);
        break;
      case 'task_deliver':
        this.handleTaskDeliver(activity);
        break;
      case 'task_accept':
        this.handleTaskAccept(activity);
        break;
      case 'task_reject':
        this.handleTaskReject(activity);
        break;
      case 'escalation':
        this.handleEscalation(activity);
        break;
      case 'broadcast':
        this.handleBroadcast(activity);
        break;
      case 'message':
        this.handleMessage(activity);
        break;
      case 'status_change':
        this.handleStatusChange(activity);
        break;
    }

    EventBus.emit('event-log', {
      type: activity.type,
      participants: activity.participants,
      data: activity.data,
      time: Date.now(),
    });
  }

  private onActivityEnd(activity: Activity) {
    if (activity.type === 'meeting_end' || activity.type === 'meeting_gather') {
      for (const nodeId of activity.participants) {
        const sprite = this.agentSprites.get(nodeId);
        if (!sprite || !this.orgData) continue;
        const node = this.orgData.nodes.find(n => n.id === nodeId);
        if (!node) continue;
        const areaType = STATUS_TO_AREA[(node.status as NodeStatus) ?? 'busy'];
        const pos = this.findPositionForNode(nodeId, node.department, areaType);
        sprite.moveTo(pos.x, pos.y);
      }
    }
  }

  private handleMeetingGather(activity: Activity) {
    const meetingRoom = this.layout?.rooms.find(r => r.type === 'meeting');
    if (!meetingRoom) return;

    activity.participants.forEach((nodeId, idx) => {
      const sprite = this.agentSprites.get(nodeId);
      if (!sprite) return;
      const seat = meetingRoom.seats[idx % meetingRoom.seats.length];
      sprite.moveTo(seat.x + TILE_SIZE / 2, seat.y + TILE_SIZE / 2, () => {
        sprite.showEmote('🪑');
      });
    });
  }

  private handleMeetingSpeak(activity: Activity) {
    const speaker = this.agentSprites.get(activity.participants[0]);
    if (!speaker) return;
    speaker.showEmote('💬');
    const content = activity.data?.content as string ?? '';
    if (content) speaker.showBubble(content, 2500);
  }

  private handleMeetingEnd(activity: Activity) {
    activity.participants.forEach(nodeId => {
      const sprite = this.agentSprites.get(nodeId);
      sprite?.showEmote('✅');
    });
  }

  private handleTaskDelegate(activity: Activity) {
    const [fromId, toId] = activity.participants;
    const fromSprite = this.agentSprites.get(fromId);
    const toSprite = this.agentSprites.get(toId);
    if (!fromSprite || !toSprite) return;

    const toPos = toSprite.getPosition();
    fromSprite.moveTo(toPos.x - 20, toPos.y, () => {
      fromSprite.showEmote('📋');
      toSprite.showEmote('📝');
    });
  }

  private handleTaskDeliver(activity: Activity) {
    const [fromId, toId] = activity.participants;
    const fromSprite = this.agentSprites.get(fromId);
    const toSprite = this.agentSprites.get(toId);
    if (!fromSprite || !toSprite) return;

    const toPos = toSprite.getPosition();
    fromSprite.moveTo(toPos.x - 20, toPos.y, () => {
      fromSprite.showEmote('📦');
      toSprite.showEmote('👀');
    });
  }

  private handleTaskAccept(activity: Activity) {
    const sprite = this.agentSprites.get(activity.participants[0]);
    sprite?.showEmote('👍');
  }

  private handleTaskReject(activity: Activity) {
    const sprite = this.agentSprites.get(activity.participants[0]);
    sprite?.showEmote('❌');
  }

  private handleEscalation(activity: Activity) {
    const [fromId, toId] = activity.participants;
    const fromSprite = this.agentSprites.get(fromId);
    const toSprite = toId ? this.agentSprites.get(toId) : null;

    fromSprite?.showEmote('❗');
    if (toSprite) {
      const toPos = toSprite.getPosition();
      fromSprite?.moveTo(toPos.x - 15, toPos.y);
    }
  }

  private handleBroadcast(activity: Activity) {
    const sprite = this.agentSprites.get(activity.participants[0]);
    if (!sprite) return;

    const publicRoom = this.layout?.rooms.find(r => r.type === 'public');
    if (publicRoom) {
      const centerX = (publicRoom.x + publicRoom.w / 2) * TILE_SIZE;
      const centerY = (publicRoom.y + 1) * TILE_SIZE;
      sprite.moveTo(centerX, centerY, () => {
        sprite.showEmote('📢');
        const content = activity.data?.content as string ?? '';
        if (content) sprite.showBubble(content, 3000);
      });
    }
  }

  private handleMessage(activity: Activity) {
    const [fromId, toId] = activity.participants;
    const fromSprite = this.agentSprites.get(fromId);
    const toSprite = this.agentSprites.get(toId);
    if (!fromSprite || !toSprite) return;

    const toPos = toSprite.getPosition();
    fromSprite.moveTo(toPos.x - 15, toPos.y, () => {
      fromSprite.showEmote('💬');
      const content = activity.data?.content as string ?? '';
      if (content) fromSprite.showBubble(content, 2000);
    });
  }

  private handleStatusChange(activity: Activity) {
    const nodeId = activity.participants[0];
    const sprite = this.agentSprites.get(nodeId);
    if (!sprite || !this.orgData || !this.layout) return;

    const newStatus = activity.data?.status as string ?? 'idle';
    const node = this.orgData.nodes.find(n => n.id === nodeId);
    if (!node) return;

    node.status = newStatus;

    if (newStatus === 'offline') {
      sprite.setVisible(false);
      return;
    }
    sprite.setVisible(true);

    const areaType = STATUS_TO_AREA[(newStatus as NodeStatus) ?? 'idle'];
    const pos = this.findPositionForNode(nodeId, node.department, areaType);
    sprite.moveTo(pos.x, pos.y);
  }

  private onZoomToNode(nodeId: string) {
    if (!this.isAlive()) return;
    const sprite = this.agentSprites.get(nodeId);
    if (!sprite) return;
    const pos = sprite.getPosition();
    const cam = this.cameras.main;
    this.tweens.add({
      targets: cam,
      scrollX: pos.x - cam.width / 2,
      scrollY: pos.y - cam.height / 2,
      zoom: 2.5,
      duration: 500,
      ease: 'Quad.easeInOut',
    });
  }

  // Public API — called directly from PhaserGame.tsx, no EventBus indirection
  updateOrgData(data: OrgData) {
    this.orgData = data;
    this.rebuildScene();
  }

  changeTheme(themeId: string) {
    this.currentTheme = getTheme(themeId);
    if (!this.isAlive()) return;
    this.cameras.main.setBackgroundColor(this.currentTheme.palette.background);
    this.rebuildScene();
  }

  getLayout(): LayoutResult | null {
    return this.layout;
  }

  shutdown() {
    if (this._hActStart) EventBus.off('activity-start', this._hActStart);
    if (this._hActEnd) EventBus.off('activity-end', this._hActEnd);
    if (this._hZoom) EventBus.off('zoom-to-node', this._hZoom);
    this._hActStart = undefined;
    this._hActEnd = undefined;
    this._hZoom = undefined;

    this.activitySystem?.destroy();
    this.agentSprites.forEach(s => s.destroy());
    this.agentSprites.clear();
    this.roomLabels.forEach(l => l.destroy());
    this.roomLabels = [];
    if (this.tilemapImage) {
      this.tilemapImage.destroy();
      this.tilemapImage = null;
    }
    if (this.textures?.exists(this.tilemapTextureKey)) {
      this.textures.remove(this.tilemapTextureKey);
    }
  }
}
