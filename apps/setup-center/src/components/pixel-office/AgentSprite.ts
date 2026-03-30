import Phaser from 'phaser';
import { CharacterComposer } from '../pixel-avatar/CharacterComposer';
import { TILE_SIZE } from './TilesetManager';

const SPRITE_KEY_PREFIX = 'agent_';
const MOVE_SPEED = 80;

export interface AgentSpriteConfig {
  nodeId: string;
  name: string;
  color: string;
  icon?: string;
  department?: string;
  status?: string;
  pixelAppearance?: Record<string, unknown> | null;
}

export class AgentSprite {
  readonly nodeId: string;
  private sprite: Phaser.GameObjects.Image;
  private nameLabel: Phaser.GameObjects.Text;
  private bubbleText: Phaser.GameObjects.Text | null = null;
  private bubbleTimer: Phaser.Time.TimerEvent | null = null;
  private scene: Phaser.Scene;
  private config: AgentSpriteConfig;
  private isMoving = false;
  private moveTarget: { x: number; y: number } | null = null;

  constructor(scene: Phaser.Scene, config: AgentSpriteConfig, x: number, y: number) {
    this.scene = scene;
    this.nodeId = config.nodeId;
    this.config = config;

    const textureKey = SPRITE_KEY_PREFIX + config.nodeId;
    this.ensureTexture(textureKey, config);

    this.sprite = scene.add.image(x, y, textureKey);
    this.sprite.setScale(1.5);
    this.sprite.setDepth(10);

    this.nameLabel = scene.add.text(x, y + 28, config.name, {
      fontSize: '11px',
      fontFamily: '"Microsoft YaHei", "PingFang SC", monospace',
      color: '#ffffff',
      backgroundColor: 'rgba(0,0,0,0.55)',
      padding: { x: 3, y: 1 },
      align: 'center',
    });
    this.nameLabel.setOrigin(0.5, 0);
    this.nameLabel.setDepth(11);

    this.addIdleFloat();
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
        this.nameLabel.setPosition(this.sprite.x, this.sprite.y + 28);
        if (this.bubbleText) {
          this.bubbleText.setPosition(this.sprite.x, this.sprite.y - 35);
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
    this.bubbleText = this.scene.add.text(this.sprite.x, this.sprite.y - 35, truncated, {
      fontSize: '11px',
      fontFamily: '"Microsoft YaHei", "PingFang SC", monospace',
      color: '#333333',
      backgroundColor: '#FFFFFFEE',
      padding: { x: 5, y: 3 },
      align: 'center',
      wordWrap: { width: 140 },
    });
    this.bubbleText.setOrigin(0.5, 1);
    this.bubbleText.setDepth(20);

    this.scene.tweens.add({
      targets: this.bubbleText,
      alpha: { from: 0, to: 1 },
      y: this.sprite.y - 42,
      duration: 200,
    });

    this.bubbleTimer = this.scene.time.delayedCall(duration, () => {
      this.clearBubble();
    });
  }

  showEmote(emote: string) {
    const emoteText = this.scene.add.text(this.sprite.x, this.sprite.y - 30, emote, {
      fontSize: '18px',
    });
    emoteText.setOrigin(0.5, 1);
    emoteText.setDepth(25);

    this.scene.tweens.add({
      targets: emoteText,
      y: this.sprite.y - 55,
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
    this.nameLabel.setPosition(x, y + 28);
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
    this.scene.tweens.killTweensOf(this.sprite);
    this.sprite.destroy();
    this.nameLabel.destroy();
  }
}
