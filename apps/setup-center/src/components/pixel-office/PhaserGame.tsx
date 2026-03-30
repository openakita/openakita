import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import Phaser from 'phaser';
import { OfficeScene, type OrgData } from './OfficeScene';
import { EventBus } from './EventBus';

export interface GameRef {
  game: Phaser.Game | null;
  scene: OfficeScene | null;
}

export interface PhaserGameProps {
  themeId: string;
  orgData: OrgData | null;
  onSceneReady?: (scene: OfficeScene) => void;
  onEventLog?: (entry: unknown) => void;
}

export const PhaserGame = forwardRef<GameRef, PhaserGameProps>(function PhaserGame(
  { themeId, orgData, onSceneReady, onEventLog },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const gameRef = useRef<Phaser.Game | null>(null);
  const sceneRef = useRef<OfficeScene | null>(null);
  const orgDataRef = useRef<OrgData | null>(orgData);
  const themeIdRef = useRef(themeId);

  orgDataRef.current = orgData;
  themeIdRef.current = themeId;

  useImperativeHandle(ref, () => ({
    get game() { return gameRef.current; },
    get scene() { return sceneRef.current; },
  }));

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let game: Phaser.Game | null = null;
    let destroyed = false;

    const createGame = () => {
      if (destroyed) return;
      game = new Phaser.Game({
        type: Phaser.AUTO,
        parent: el,
        pixelArt: true,
        roundPixels: true,
        antialias: false,
        backgroundColor: '#1e1e2e',
        scene: [OfficeScene],
        scale: {
          mode: Phaser.Scale.RESIZE,
          autoCenter: Phaser.Scale.CENTER_BOTH,
        },
      });
      gameRef.current = game;
    };

    const onReady = (scene: OfficeScene) => {
      if (destroyed) return;
      sceneRef.current = scene;
      onSceneReady?.(scene);
      if (orgDataRef.current) scene.updateOrgData(orgDataRef.current);
      scene.changeTheme(themeIdRef.current);
    };

    EventBus.on('scene-ready', onReady);
    if (onEventLog) EventBus.on('event-log', onEventLog);

    if (el.clientWidth > 0 && el.clientHeight > 0) {
      createGame();
    } else {
      const raf = requestAnimationFrame(() => {
        if (!destroyed) createGame();
      });
      return () => {
        destroyed = true;
        cancelAnimationFrame(raf);
        EventBus.off('scene-ready', onReady);
        if (onEventLog) EventBus.off('event-log', onEventLog);
      };
    }

    return () => {
      destroyed = true;
      EventBus.off('scene-ready', onReady);
      if (onEventLog) EventBus.off('event-log', onEventLog);
      sceneRef.current?.shutdown();
      game?.destroy(true);
      gameRef.current = null;
      sceneRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (orgData && sceneRef.current) {
      sceneRef.current.updateOrgData(orgData);
    }
  }, [orgData]);

  useEffect(() => {
    if (sceneRef.current) {
      sceneRef.current.changeTheme(themeId);
    }
  }, [themeId]);

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%',
        height: '100%',
        imageRendering: 'pixelated',
        overflow: 'hidden',
      }}
    />
  );
});
