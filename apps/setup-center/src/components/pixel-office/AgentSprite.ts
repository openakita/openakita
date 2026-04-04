import Phaser from 'phaser';
import { CharacterComposer } from '../pixel-avatar/CharacterComposer';
import { TILE_SIZE } from './TilesetManager';

const SPRITE_KEY_PREFIX = 'agent_';
const MOVE_SPEED = 100;
const LABEL_OFFSET_Y = 36;
const BUBBLE_OFFSET_Y = -40;
const EMOTE_OFFSET_Y = -38;

export interface AgentSpriteConfig {
  nodeId: string;
  name: string;
  color: string;
  icon?: string;
  department?: string;
  status?: string;
  pixelAppearance?: Record<string, unknown> | null;
}

export type AgentClickHandler = (nodeId: string, config: AgentSpriteConfig, screenX: number, screenY: number) => void;
export type AgentContextMenuHandler = (nodeId: string, config: AgentSpriteConfig, screenX: number, screenY: number) => void;

export class AgentSprite {
  readonly nodeId: string;
  private sprite: Phaser.GameObjects.Image;
  private nameLabel: Phaser.GameObjects.Text;
  private bubbleText: Phaser.GameObjects.Text | null = null;
  private bubbleTimer: Phaser.Time.TimerEvent | null = null;
  private tooltipText: Phaser.GameObjects.Text | null = null;
  private scene: Phaser.Scene;
  private config: AgentSpriteConfig;
  private isMoving = false;
  private moveTarget: { x: number; y: number } | null = null;
  private _currentTask: string = '';
  private _toolCalls: string[] = [];

  onAgentClick: AgentClickHandler | null = null;
  onAgentContextMenu: AgentContextMenuHandler | null = null;

  constructor(scene: Phaser.Scene, config: AgentSpriteConfig, x: number, y: number) {
    this.scene = scene;
    this.nodeId = config.nodeId;
    this.config = config;

    const textureKey = SPRITE_KEY_PREFIX + config.nodeId;
    this.ensureTexture(textureKey, config);

    this.sprite = scene.add.image(x, y, textureKey);
    this.sprite.setDepth(10);
    this.sprite.setInteractive({ useHandCursor: true });

    this.sprite.on('pointerover', () => { this._showTooltip(); });
    this.sprite.on('pointerout', () => { this._hideTooltip(); });
    this.sprite.on('pointerdown', (pointer: Phaser.Input.Pointer) => {
      const nativeEvent = pointer.event;
      const clientX =
        nativeEvent instanceof MouseEvent ? nativeEvent.clientX :
        nativeEvent instanceof TouchEvent ? nativeEvent.changedTouches[0]?.clientX ?? pointer.x :
        pointer.x;
      const clientY =
        nativeEvent instanceof MouseEvent ? nativeEvent.clientY :
        nativeEvent instanceof TouchEvent ? nativeEvent.changedTouches[0]?.clientY ?? pointer.y :
        pointer.y;
      if (pointer.rightButtonDown()) {
        pointer.event.preventDefault();
        this.onAgentContextMenu?.(this.nodeId, this.config, clientX, clientY);
      } else {
        this.onAgentClick?.(this.nodeId, this.config, clientX, clientY);
      }
    });

    this.nameLabel = scene.add.text(x, y + LABEL_OFFSET_Y, config.name, {
      fontSize: '13px',
      fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
      color: '#f0f0f0',
      backgroundColor: '#000000bb',
      padding: { x: 5, y: 2 },
      align: 'center',
    });
    this.nameLabel.setOrigin(0.5, 0);
    this.nameLabel.setDepth(11);

    this.addIdleFloat();
  }

  setCurrentTask(task: string) { this._currentTask = task; }
  addToolCall(tool: string) { this._toolCalls = [...this._toolCalls.slice(-9), tool]; }

  private _showTooltip() {
    this._hideTooltip();
    const lines = [
      `${this.config.name} (${this.nodeId})`,
      `Status: ${this.config.status || 'idle'}`,
    ];
    if (this.config.department) lines.push(`Dept: ${this.config.department}`);
    if (this._currentTask) lines.push(`Task: ${this._currentTask.slice(0, 40)}`);
    if (this._toolCalls.length) lines.push(`Tools: ${this._toolCalls.slice(-3).join(', ')}`);

    this.tooltipText = this.scene.add.text(
      this.sprite.x, this.sprite.y - 60, lines.join('\n'),
      {
        fontSize: '11px',
        fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
        color: '#e0e0e0',
        backgroundColor: '#1a1a2edd',
        padding: { x: 8, y: 6 },
        align: 'left',
        wordWrap: { width: 220 },
      },
    );
    this.tooltipText.setOrigin(0.5, 1);
    this.tooltipText.setDepth(100);
  }

  private _hideTooltip() {
    if (this.tooltipText) {
      this.tooltipText.destroy();
      this.tooltipText = null;
    }
  }

  private ensureTexture(key: string, config: AgentSpriteConfig) {
    if (!this.scene.sys?.game?.renderer) return;
    if (this.scene.textures.exists(key)) return;

    const composer = CharacterComposer.getInstance();
    const size = CharacterComposer.getSpriteSize();
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d')!;

    composer.renderTo(ctx, config.nodeId, 0, 0, {
      color: config.color,
      icon: config.icon,
      name: config.name,
      customAppearance: config.pixelAppearance as never,
    });

    this.scene.textures.addCanvas(key, canvas);
  }

  private addIdleFloat() {
    this.scene.tweens.add({
      targets: this.sprite,
      y: this.sprite.y - 3,
      duration: 1200,
      yoyo: true,
      repeat: -1,
      ease: 'Sine.easeInOut',
    });
  }

  moveTo(targetX: number, targetY: number, onComplete?: () => void) {
    this.scene.tweens.killTweensOf(this.sprite);
    this.isMoving = true;
    this.moveTarget = { x: targetX, y: targetY };

    const dist = Phaser.Math.Distance.Between(this.sprite.x, this.sprite.y, targetX, targetY);
    const duration = (dist / MOVE_SPEED) * 1000;

    this.scene.tweens.add({
      targets: this.sprite,
      x: targetX,
      y: targetY,
      duration: Math.max(duration, 200),
      ease: 'Quad.easeInOut',
      onUpdate: () => {
        this.nameLabel.setPosition(this.sprite.x, this.sprite.y + LABEL_OFFSET_Y);
        if (this.bubbleText) {
          this.bubbleText.setPosition(this.sprite.x, this.sprite.y + BUBBLE_OFFSET_Y);
        }
      },
      onComplete: () => {
        this.isMoving = false;
        this.moveTarget = null;
        this.addIdleFloat();
        onComplete?.();
      },
    });
  }

  showBubble(text: string, duration = 3000) {
    this.clearBubble();

    const truncated = text.length > 20 ? text.slice(0, 20) + '…' : text;
    this.bubbleText = this.scene.add.text(
      this.sprite.x, this.sprite.y + BUBBLE_OFFSET_Y, truncated,
      {
        fontSize: '12px',
        fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
        color: '#222',
        backgroundColor: '#ffffffee',
        padding: { x: 6, y: 3 },
        align: 'center',
        wordWrap: { width: 160 },
      },
    );
    this.bubbleText.setOrigin(0.5, 1);
    this.bubbleText.setDepth(20);

    this.scene.tweens.add({
      targets: this.bubbleText,
      alpha: { from: 0, to: 1 },
      y: this.sprite.y + BUBBLE_OFFSET_Y - 8,
      duration: 200,
    });

    this.bubbleTimer = this.scene.time.delayedCall(duration, () => {
      this.clearBubble();
    });
  }

  showEmote(emote: string) {
    const emoteText = this.scene.add.text(
      this.sprite.x, this.sprite.y + EMOTE_OFFSET_Y, emote,
      { fontSize: '22px' },
    );
    emoteText.setOrigin(0.5, 1);
    emoteText.setDepth(25);

    this.scene.tweens.add({
      targets: emoteText,
      y: this.sprite.y + EMOTE_OFFSET_Y - 30,
      alpha: 0,
      duration: 1500,
      onComplete: () => emoteText.destroy(),
    });
  }

  private clearBubble() {
    if (this.bubbleText) {
      this.bubbleText.destroy();
      this.bubbleText = null;
    }
    if (this.bubbleTimer) {
      this.bubbleTimer.remove();
      this.bubbleTimer = null;
    }
  }

  getPosition(): { x: number; y: number } {
    return { x: this.sprite.x, y: this.sprite.y };
  }

  setPosition(x: number, y: number) {
    this.sprite.setPosition(x, y);
    this.nameLabel.setPosition(x, y + LABEL_OFFSET_Y);
  }

  updateConfig(config: Partial<AgentSpriteConfig>) {
    Object.assign(this.config, config);
    const textureKey = SPRITE_KEY_PREFIX + this.config.nodeId;
    if (this.scene.textures.exists(textureKey)) {
      this.scene.textures.remove(textureKey);
    }
    this.ensureTexture(textureKey, this.config);
    this.sprite.setTexture(textureKey);
    this.nameLabel.setText(this.config.name);
  }

  setVisible(visible: boolean) {
    this.sprite.setVisible(visible);
    this.nameLabel.setVisible(visible);
  }

  getSeatTarget(rooms: Array<{ seats: Array<{ x: number; y: number; id: string }> }>, seatId: string): { x: number; y: number } | null {
    for (const room of rooms) {
      const seat = room.seats.find(s => s.id === seatId || s.id === this.nodeId);
      if (seat) return { x: seat.x + TILE_SIZE / 2, y: seat.y + TILE_SIZE / 2 };
    }
    return null;
  }

  destroy() {
    this.clearBubble();
    this._hideTooltip();
    this.scene.tweens.killTweensOf(this.sprite);
    this.sprite.destroy();
    this.nameLabel.destroy();
  }
}
