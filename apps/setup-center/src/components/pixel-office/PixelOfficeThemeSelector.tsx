import { listThemes, type SceneTheme } from './SceneTheme';

export function PixelOfficeThemeSelector({
  currentThemeId,
  onSelectTheme,
}: {
  currentThemeId: string;
  onSelectTheme: (themeId: string) => void;
}) {
  const themes = listThemes();

  return (
    <div className="pixelPanel" style={{ maxWidth: 280 }}>
      <div className="pixelPanelTitle">🎨 场景主题</div>
      <div className="pixelPanelContent" style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignContent: 'flex-start' }}>
        {themes.map(theme => (
          <ThemeCard
            key={theme.id}
            theme={theme}
            active={theme.id === currentThemeId}
            onClick={() => onSelectTheme(theme.id)}
          />
        ))}
      </div>
    </div>
  );
}

function ThemeCard({ theme, active, onClick }: { theme: SceneTheme; active: boolean; onClick: () => void }) {
  return (
    <div
      className={`themeCard ${active ? 'active' : ''}`}
      onClick={onClick}
      title={theme.description}
    >
      <div
        className="themeCardSwatch"
        style={{
          background: `linear-gradient(135deg, ${theme.palette.floor[0]} 25%, ${theme.palette.wall[0]} 50%, ${theme.palette.accent} 75%)`,
        }}
      />
      <div className="themeCardLabel">{theme.name}</div>
    </div>
  );
}
