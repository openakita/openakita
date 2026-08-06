import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.openakita.mobile",
  appName: "OpenAkita",
  webDir: "dist-web",
  server: {
    androidScheme: "http",
    iosScheme: "http",
    allowNavigation: ["*"],
    cleartext: true,
  },
  android: {
    allowMixedContent: false,
    webContentsDebuggingEnabled: true,
  },
  plugins: {
    CapacitorCookies: {
      enabled: true,
    },
  },
};

export default config;
