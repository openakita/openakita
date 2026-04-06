const pptxgen = require('pptxgenjs');
const path = require('path');

async function build() {
  const pptx = new pptxgen();
  pptx.layout = 'LAYOUT_16x9';
  pptx.author = 'OpenAkita';
  pptx.title = '国产 AI Agent 推荐';

  const slide = pptx.addSlide();
  slide.background = { color: '111827' };

  // Top accent bar
  slide.addShape(pptx.shapes.RECTANGLE, {
    x: 0, y: 0, w: 10, h: 0.06,
    fill: { color: '2563EB' }
  });

  // Title
  slide.addText('OpenClaw 之外：国产 AI Agent 推荐', {
    x: 0.5, y: 0.15, w: 9, h: 0.4,
    fontSize: 20, fontFace: 'Arial',
    color: 'FFFFFF', bold: true
  });

  // Subtitle
  slide.addText('除了原版龙虾 OpenClaw，这些国产框架同样强大、更适合国内场景', {
    x: 0.5, y: 0.5, w: 9, h: 0.3,
    fontSize: 10, fontFace: 'Arial',
    color: '94A3B8'
  });

  // Mascot illustration (1376x768, aspect ~1.79:1)
  const mascotPath = path.join(__dirname, 'mascots.png');
  const imgW = 5.5;
  const imgH = imgW / 1.79;
  const imgX = (10 - imgW) / 2;
  slide.addImage({
    path: mascotPath,
    x: imgX, y: 0.85, w: imgW, h: imgH
  });

  // Cards row
  const cardY = 0.85 + imgH + 0.1;
  const cardH = 1.1;
  const cardW = 2.12;
  const gap = 0.17;
  const startX = 0.5;

  const agents = [
    {
      name: 'OpenAkita',
      nameColor: 'EA580C',
      accentColor: 'EA580C',
      desc: '开源多Agent AI助手\n支持30+大模型\n飞书/钉钉/企微/QQ全通道\n内置自进化引擎'
    },
    {
      name: 'CoPaw',
      nameColor: '60A5FA',
      accentColor: '2563EB',
      desc: '阿里通义出品\n个人智能体工作台\n长期记忆+定时任务\n分层安全防护机制'
    },
    {
      name: 'LobsterAI',
      nameColor: 'F87171',
      accentColor: 'DC2626',
      desc: '网易有道推出\n7x24 桌面 Agent\nGUI交互+16项内置技能\n跨设备远程协作'
    },
    {
      name: 'WorkBuddy',
      nameColor: '34D399',
      accentColor: '059669',
      desc: '腾讯云AI智能体引擎\n兼容OpenClaw Skills生态\n接入企微/微信/飞书/QQ\n支持混元/DeepSeek等模型'
    }
  ];

  agents.forEach((agent, i) => {
    const x = startX + i * (cardW + gap);

    // Card background
    slide.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
      x: x, y: cardY, w: cardW, h: cardH,
      fill: { color: 'FFFFFF', transparency: 92 },
      line: { color: 'FFFFFF', width: 0.5, transparency: 85 },
      rectRadius: 0.08
    });

    // Left accent line
    slide.addShape(pptx.shapes.RECTANGLE, {
      x: x, y: cardY + 0.05, w: 0.04, h: cardH - 0.1,
      fill: { color: agent.accentColor }
    });

    // Agent name
    slide.addText(agent.name, {
      x: x + 0.15, y: cardY + 0.05, w: cardW - 0.2, h: 0.3,
      fontSize: 11, fontFace: 'Arial',
      color: agent.nameColor, bold: true,
      valign: 'middle'
    });

    // Description
    slide.addText(agent.desc, {
      x: x + 0.15, y: cardY + 0.35, w: cardW - 0.25, h: cardH - 0.4,
      fontSize: 8, fontFace: 'Arial',
      color: 'CBD5E1', lineSpacingMultiple: 1.3,
      valign: 'top'
    });
  });

  // Footer bar
  const footerY = cardY + cardH + 0.08;
  slide.addShape(pptx.shapes.RECTANGLE, {
    x: 0, y: footerY, w: 10, h: 0.25,
    fill: { color: 'FFFFFF', transparency: 96 }
  });

  slide.addText(
    '推荐在培训龙虾 OpenClaw 的同时，引导学员了解国产生态  |  OpenAkita 为首选推荐',
    {
      x: 0.5, y: footerY, w: 9, h: 0.25,
      fontSize: 7.5, fontFace: 'Arial',
      color: '64748B', align: 'center', valign: 'middle'
    }
  );

  const outFile = path.join(__dirname, 'domestic_ai_agents_v2.pptx');
  await pptx.writeFile({ fileName: outFile });
  console.log('PPTX created:', outFile);
}

build().catch(err => {
  console.error('Build failed:', err.message);
  process.exit(1);
});
