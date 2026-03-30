export interface FurnitureStyle {
  label: string;
  tint: number;
}

export interface SceneTheme {
  id: string;
  name: string;
  description: string;

  palette: {
    background: string;
    floor: [string, string];
    wall: [string, string];
    accent: string;
  };

  furniture: {
    workstation: FurnitureStyle;
    chair: FurnitureStyle;
    meetingTable: FurnitureStyle;
    restArea: FurnitureStyle;
    debugStation: FurnitureStyle;
    entrance: FurnitureStyle;
  };

  roomLabels: {
    department: string;
    meetingRoom: string;
    breakRoom: string;
    debugArea: string;
    entrance: string;
  };

  characterStyle: {
    outfitBase: string;
    accessoryMap: Record<string, string>;
  };

  ambiance: {
    particles?: 'none' | 'dust' | 'snow' | 'leaves' | 'stars' | 'sparks' | 'bubbles';
    musicHint?: string;
  };
}

function hex(c: string): number {
  return parseInt(c.replace('#', ''), 16);
}

export const THEME_PRESETS: Record<string, SceneTheme> = {
  office: {
    id: 'office',
    name: '现代办公室',
    description: '标准企业办公环境',
    palette: {
      background: '#2C2C3A',
      floor: ['#C4A882', '#B89B72'],
      wall: ['#8899AA', '#6B7A8A'],
      accent: '#4A90D9',
    },
    furniture: {
      workstation: { label: '办公桌', tint: hex('#8B7355') },
      chair: { label: '办公椅', tint: hex('#555555') },
      meetingTable: { label: '会议桌', tint: hex('#6B5B4B') },
      restArea: { label: '沙发', tint: hex('#7B6BA5') },
      debugStation: { label: '服务器', tint: hex('#3A4A5A') },
      entrance: { label: '大门', tint: hex('#6B5B4B') },
    },
    roomLabels: { department: '部门', meetingRoom: '会议室', breakRoom: '休息室', debugArea: '机房', entrance: '入口' },
    characterStyle: { outfitBase: 'suit', accessoryMap: {} },
    ambiance: { particles: 'dust' },
  },

  tech_lab: {
    id: 'tech_lab',
    name: '科技实验室',
    description: '高科技研发实验室',
    palette: {
      background: '#1A1A2E',
      floor: ['#3A3A5A', '#2A2A4A'],
      wall: ['#4A4A6A', '#3A3A5A'],
      accent: '#00D4FF',
    },
    furniture: {
      workstation: { label: '实验台', tint: hex('#4A5A6A') },
      chair: { label: '实验椅', tint: hex('#3A4A5A') },
      meetingTable: { label: '白板桌', tint: hex('#5A6A7A') },
      restArea: { label: '休息舱', tint: hex('#2A3A4A') },
      debugStation: { label: '服务器阵列', tint: hex('#1A2A3A') },
      entrance: { label: '安全门', tint: hex('#4A5A6A') },
    },
    roomLabels: { department: '实验室', meetingRoom: '研讨室', breakRoom: '休息舱', debugArea: '数据中心', entrance: '安全入口' },
    characterStyle: { outfitBase: 'labcoat', accessoryMap: {} },
    ambiance: { particles: 'sparks' },
  },

  school: {
    id: 'school',
    name: '学校',
    description: '教育机构环境',
    palette: {
      background: '#F5F0E8',
      floor: ['#D4C4A8', '#C4B498'],
      wall: ['#E8DCC8', '#D8CCB8'],
      accent: '#FF8C42',
    },
    furniture: {
      workstation: { label: '课桌', tint: hex('#A0855C') },
      chair: { label: '椅子', tint: hex('#8B7355') },
      meetingTable: { label: '讲台', tint: hex('#6B5B4B') },
      restArea: { label: '图书角', tint: hex('#8B6914') },
      debugStation: { label: '电脑室', tint: hex('#555555') },
      entrance: { label: '校门', tint: hex('#6B5B4B') },
    },
    roomLabels: { department: '教室', meetingRoom: '阶梯教室', breakRoom: '食堂', debugArea: '电脑室', entrance: '校门' },
    characterStyle: { outfitBase: 'casual', accessoryMap: {} },
    ambiance: { particles: 'none' },
  },

  spaceship: {
    id: 'spaceship',
    name: '太空站',
    description: '星际飞船指挥中心',
    palette: {
      background: '#0A0A1A',
      floor: ['#2A2A3A', '#1A1A2A'],
      wall: ['#3A3A4A', '#2A2A3A'],
      accent: '#FF4444',
    },
    furniture: {
      workstation: { label: '控制台', tint: hex('#3A4A5A') },
      chair: { label: '座椅', tint: hex('#2A3A4A') },
      meetingTable: { label: '舰桥', tint: hex('#4A5A6A') },
      restArea: { label: '生活舱', tint: hex('#3A4A3A') },
      debugStation: { label: '引擎舱', tint: hex('#5A3A2A') },
      entrance: { label: '气闸', tint: hex('#5A5A6A') },
    },
    roomLabels: { department: '舱室', meetingRoom: '指挥室', breakRoom: '生活舱', debugArea: '引擎舱', entrance: '气闸' },
    characterStyle: { outfitBase: 'spacesuit', accessoryMap: {} },
    ambiance: { particles: 'stars' },
  },

  medieval_guild: {
    id: 'medieval_guild',
    name: '中世纪公会',
    description: '奇幻冒险者公会大厅',
    palette: {
      background: '#2A1F14',
      floor: ['#8B7355', '#7A6345'],
      wall: ['#6B5B4B', '#5B4B3B'],
      accent: '#FFD700',
    },
    furniture: {
      workstation: { label: '木桌', tint: hex('#6B5B4B') },
      chair: { label: '木凳', tint: hex('#5B4B3B') },
      meetingTable: { label: '圆桌', tint: hex('#4B3B2B') },
      restArea: { label: '酒馆长凳', tint: hex('#7A6345') },
      debugStation: { label: '炼金台', tint: hex('#5A4A3A') },
      entrance: { label: '吊桥门', tint: hex('#4B3B2B') },
    },
    roomLabels: { department: '分会', meetingRoom: '议事厅', breakRoom: '酒馆', debugArea: '铸造间', entrance: '吊桥门' },
    characterStyle: { outfitBase: 'armor', accessoryMap: {} },
    ambiance: { particles: 'sparks' },
  },

  game_studio: {
    id: 'game_studio',
    name: '游戏工作室',
    description: '创意游戏开发工作室',
    palette: {
      background: '#1E1E2E',
      floor: ['#6B5B8D', '#5B4B7D'],
      wall: ['#4A3A6A', '#3A2A5A'],
      accent: '#FF6BFF',
    },
    furniture: {
      workstation: { label: '双屏工位', tint: hex('#4A4A6A') },
      chair: { label: '电竞椅', tint: hex('#FF4444') },
      meetingTable: { label: '头脑风暴桌', tint: hex('#5A5A7A') },
      restArea: { label: '街机区', tint: hex('#3A3A5A') },
      debugStation: { label: '测试机房', tint: hex('#2A2A4A') },
      entrance: { label: '入口', tint: hex('#5A4A6A') },
    },
    roomLabels: { department: '团队', meetingRoom: '脑暴室', breakRoom: '街机区', debugArea: '测试间', entrance: '入口' },
    characterStyle: { outfitBase: 'casual', accessoryMap: {} },
    ambiance: { particles: 'dust' },
  },

  garden: {
    id: 'garden',
    name: '花园',
    description: '自然户外花园工作空间',
    palette: {
      background: '#87CEEB',
      floor: ['#5A8A3A', '#4A7A2A'],
      wall: ['#8B7355', '#7A6345'],
      accent: '#FF6B6B',
    },
    furniture: {
      workstation: { label: '石桌', tint: hex('#999999') },
      chair: { label: '木椅', tint: hex('#8B7355') },
      meetingTable: { label: '凉亭', tint: hex('#6B5B4B') },
      restArea: { label: '吊床', tint: hex('#4A7A2A') },
      debugStation: { label: '温室', tint: hex('#3A6A2A') },
      entrance: { label: '花门', tint: hex('#FF6B6B') },
    },
    roomLabels: { department: '花园区', meetingRoom: '凉亭', breakRoom: '树荫下', debugArea: '温室', entrance: '花门' },
    characterStyle: { outfitBase: 'casual', accessoryMap: {} },
    ambiance: { particles: 'leaves' },
  },

  hospital: {
    id: 'hospital',
    name: '医疗机构',
    description: '现代医院环境',
    palette: {
      background: '#E8F0F8',
      floor: ['#D8E8D8', '#C8D8C8'],
      wall: ['#F0F0F0', '#E0E0E0'],
      accent: '#27AE60',
    },
    furniture: {
      workstation: { label: '护士站', tint: hex('#CCCCCC') },
      chair: { label: '轮椅', tint: hex('#888888') },
      meetingTable: { label: '会诊桌', tint: hex('#AAAAAA') },
      restArea: { label: '候诊区', tint: hex('#99BBAA') },
      debugStation: { label: '检验室', tint: hex('#88AACC') },
      entrance: { label: '急诊入口', tint: hex('#CC4444') },
    },
    roomLabels: { department: '科室', meetingRoom: '会诊室', breakRoom: '候诊区', debugArea: '检验室', entrance: '急诊入口' },
    characterStyle: { outfitBase: 'labcoat', accessoryMap: {} },
    ambiance: { particles: 'none' },
  },
};

export function getTheme(id: string): SceneTheme {
  return THEME_PRESETS[id] ?? THEME_PRESETS.office;
}

export function listThemes(): SceneTheme[] {
  return Object.values(THEME_PRESETS);
}
