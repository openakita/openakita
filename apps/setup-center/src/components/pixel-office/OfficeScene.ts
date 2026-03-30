import Phaser from 'phaser';
import { EventBus } from './EventBus';
import { TilesetManager, TILE_SIZE } from './TilesetManager';
import { generateLayout, type RoomDef, type LayoutResult } from './RoomGenerator';
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

    EventBus.on('update-org-data', this.onOrgDataUpdate, this);
    EventBus.on('update-theme', this.onThemeChange, this);
    EventBus.on('activity-start', this.onActivityStart, this);
    EventBus.on('activity-end', this.onActivityEnd, this);

    // Camera controls
    this.input.on('wheel', (_p: unknown, _gx: unknown, _gy: unknown, _gz: unknown, dy: number) => {
      const cam = this.cameras.main;
      cam.zoom = Phaser.Math.Clamp(cam.zoom + (dy > 0 ? -0.1 : 0.1), 0.5, 3);
    });

    let dragStart: { x: number; y: number } | null = null;
    this.input.on('pointerdown', (pointer: Phaser.Input.Pointer) => {
      if (pointer.middleButtonDown() || pointer.rightButtonDown()) {
        dragStart = { x: pointer.x, y: pointer.y };
      }
    });
    this.input.on('pointermove', (pointer: Phaser.Input.Pointer) => {
      if (dragStart && (pointer.middleButtonDown() || pointer.rightButtonDown())) {
        const dx = pointer.x - dragStart.x;
        const dy = pointer.y - dragStart.y;
        this.cameras.main.scrollX -= dx / this.cameras.main.zoom;
        this.cameras.main.scrollY -= dy / this.cameras.main.zoom;
        dragStart = { x: pointer.x, y: pointer.y };
      }
    });
    this.input.on('pointerup', () => { dragStart = null; });

    EventBus.emit('scene-ready', this);
  }

  private onOrgDataUpdate = (data: OrgData) => {
    this.orgData = data;
    this.rebuildScene();
  };

  private onThemeChange = (themeId: string) => {
    this.currentTheme = getTheme(themeId);
    this.cameras.main.setBackgroundColor(this.currentTheme.palette.background);
    this.rebuildScene();
  };

  private rebuildScene() {
    if (!this.orgData || !this.sys?.game?.renderer) return;

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

    // Build departments from org data
    const deptMap = new Map<string, string[]>();
    for (const node of this.orgData.nodes) {
      const dept = node.department || '默认';
      if (!deptMap.has(dept)) deptMap.set(dept, []);
      deptMap.get(dept)!.push(node.id);
    }
    const departments = Array.from(deptMap.entries()).map(([name, nodeIds]) => ({ name, nodeIds }));

    // Generate tileset
    this.tilesetManager.generateTileset(this.currentTheme);

    // Generate layout
    this.layout = generateLayout(departments, this.currentTheme);

    // Draw tilemap with Graphics
    this.renderTilemap();

    // Draw room labels
    this.renderRoomLabels();

    // Create activity system
    this.activitySystem = new ActivitySystem(this.layout.rooms);

    // Spawn agents
    this.spawnAgents();

    // Center camera
    const worldW = this.layout.mapWidth * TILE_SIZE;
    const worldH = this.layout.mapHeight * TILE_SIZE;
    this.cameras.main.centerOn(worldW / 2, worldH / 2);
    this.cameras.main.zoom = Math.min(
      this.cameras.main.width / worldW,
      this.cameras.main.height / worldH,
      2,
    );
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
          tileId * TILE_SIZE, 0, TILE_SIZE, TILE_SIZE,
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
        room.y * TILE_SIZE - 2,
        room.label,
        {
          fontSize: '7px',
          fontFamily: 'monospace',
          color: this.currentTheme.palette.accent,
          backgroundColor: 'rgba(0,0,0,0.6)',
          padding: { x: 3, y: 1 },
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

    for (const node of this.orgData.nodes) {
      const profile = this.orgData.agentProfiles[node.agent_profile_id || node.id];
      const config: AgentSpriteConfig = {
        nodeId: node.id,
        name: profile?.name ?? node.role_title,
        color: profile?.color ?? '#4A90D9',
        icon: profile?.icon,
        department: node.department,
        status: node.status,
        pixelAppearance: profile?.pixel_appearance,
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

  private onActivityStart = (activity: Activity) => {
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
  };

  private onActivityEnd = (activity: Activity) => {
    if (activity.type === 'meeting_end' || activity.type === 'meeting_gather') {
      // Return agents to default positions
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
  };

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

  // Public API for React bridge
  updateOrgData(data: OrgData) {
    EventBus.emit('update-org-data', data);
  }

  changeTheme(themeId: string) {
    EventBus.emit('update-theme', themeId);
  }

  getLayout(): LayoutResult | null {
    return this.layout;
  }

  shutdown() {
    EventBus.off('update-org-data', this.onOrgDataUpdate, this);
    EventBus.off('update-theme', this.onThemeChange, this);
    EventBus.off('activity-start', this.onActivityStart, this);
    EventBus.off('activity-end', this.onActivityEnd, this);
    this.activitySystem?.destroy();
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
  }
}
