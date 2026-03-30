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
    <div className="poPanel poThemePanel">
      <div className="poPanelHeader">场景主题</div>
      <div className="poPanelBody poThemeGrid">
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
    <button
      className={`poThemeCard${active ? ' active' : ''}`}
      onClick={onClick}
      title={theme.description}
    >
      <div
        className="poThemeSwatch"
        style={{
          background: `linear-gradient(135deg, ${theme.palette.floor[0]} 30%, ${theme.palette.wall[0]} 60%, ${theme.palette.accent} 100%)`,
        }}
      />
      <span className="poThemeName">{theme.name}</span>
    </button>
  );
}
