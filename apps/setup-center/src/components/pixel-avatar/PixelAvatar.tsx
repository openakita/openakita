import { useEffect, useRef, memo } from 'react';
import type { PixelAppearance } from './appearance-types';
import { CharacterComposer } from './CharacterComposer';

export interface PixelAvatarProps {
  agentId: string;
  profileColor?: string;
  profileIcon?: string;
  profileName?: string;
  appearance?: PixelAppearance | null;
  size?: number;
  theme?: string;
  style?: React.CSSProperties;
  className?: string;
}

export const PixelAvatar = memo(function PixelAvatar({
  agentId,
  profileColor,
  profileIcon,
  profileName,
  appearance,
  size = 32,
  style,
  className,
}: PixelAvatarProps) {
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    const composer = CharacterComposer.getInstance();
    const dataUrl = composer.getAvatar(agentId, {
      color: profileColor,
      icon: profileIcon,
      name: profileName,
      customAppearance: appearance,
    });
    if (imgRef.current) imgRef.current.src = dataUrl;
  }, [agentId, profileColor, profileIcon, profileName, appearance]);

  return (
    <img
      ref={imgRef}
      width={size}
      height={size}
      alt={profileName || agentId}
      className={className}
      style={{
        imageRendering: 'pixelated',
        width: size,
        height: size,
        ...style,
      }}
    />
  );
});
