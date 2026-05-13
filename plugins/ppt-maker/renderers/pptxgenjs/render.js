const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const [, , inputPath, outputPath] = process.argv;

if (!inputPath || !outputPath) {
  console.error("Usage: node render.js <render_model.json> <output.pptx>");
  process.exit(2);
}

const model = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "OpenAkita ppt-maker";
pptx.subject = model.title || "Generated presentation";
pptx.title = model.title || "Generated presentation";
pptx.company = "OpenAkita";
pptx.lang = "zh-CN";
pptx.theme = {
  headFontFace: model.design_system?.font_heading || "Microsoft YaHei",
  bodyFontFace: model.design_system?.font_body || "Microsoft YaHei",
  lang: "zh-CN"
};

const theme = model.design_system || {};
const primary = cleanColor(theme.primary_color || "#3457D5");
const secondary = cleanColor(theme.secondary_color || "#172033");
const accent = cleanColor(theme.accent_color || "#FFB000");
const bg = cleanColor(theme.background_color || "#FFFFFF");

for (const slideModel of model.slides || []) {
  const slide = pptx.addSlide();
  slide.background = { color: bg };
  addTitle(slide, slideModel.title || "", primary, accent);
  const bodyComponent = (slideModel.components || []).find((item) => item.role !== "title");
  renderBody(slide, slideModel.layout_id || "content", bodyComponent?.content || {}, secondary, primary, accent);
  if (slideModel.notes) {
    slide.addNotes(String(slideModel.notes));
  }
}

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
pptx.writeFile({ fileName: outputPath });

function cleanColor(value) {
  return String(value || "").replace(/^#/, "").slice(0, 6) || "000000";
}

function addTitle(slide, text, primaryColor, accentColor) {
  slide.addText(String(text || ""), {
    x: 0.55,
    y: 0.35,
    w: 12.25,
    h: 0.55,
    fontFace: "Microsoft YaHei",
    fontSize: 24,
    bold: true,
    color: primaryColor,
    margin: 0.02,
    fit: "shrink"
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: 0.58,
    y: 1.02,
    w: 0.65,
    h: 0.05,
    fill: { color: accentColor },
    line: { color: accentColor }
  });
}

function renderBody(slide, layoutId, content, secondaryColor, primaryColor, accentColor) {
  if (layoutId.includes("chart") && content.categories && content.series) {
    renderChart(slide, content, secondaryColor, primaryColor);
    return;
  }
  if (layoutId.includes("data_table") && content.headers) {
    renderTable(slide, content, secondaryColor, primaryColor);
    return;
  }
  if (layoutId.includes("comparison") && content.left && content.right) {
    renderComparison(slide, content, primaryColor, accentColor);
    return;
  }
  if (layoutId.includes("timeline") && content.milestones) {
    renderTimeline(slide, content, primaryColor, secondaryColor);
    return;
  }
  if (layoutId.includes("metric_cards") && content.metrics) {
    renderMetrics(slide, content, primaryColor, secondaryColor);
    return;
  }
  renderText(slide, content, secondaryColor);
}

function renderText(slide, content, secondaryColor) {
  const body = content.body || content.subtitle || "";
  const bullets = content.bullets || content.items || content.findings || [];
  if (body) {
    slide.addText(String(body), { x: 0.7, y: 1.35, w: 11.9, h: 0.75, fontSize: 16, color: secondaryColor, fit: "shrink" });
  }
  if (bullets.length) {
    slide.addText(bullets.map((item) => ({ text: String(item), options: { bullet: { type: "bullet" } } })), {
      x: 0.75,
      y: body ? 2.25 : 1.45,
      w: 11.6,
      h: 4.7,
      fontSize: 15,
      color: secondaryColor,
      breakLine: false,
      fit: "shrink"
    });
  }
}

function renderMetrics(slide, content, primaryColor, secondaryColor) {
  const metrics = (content.metrics || []).slice(0, 4);
  const cardW = 11.8 / Math.max(metrics.length, 1);
  metrics.forEach((metric, idx) => {
    const x = 0.7 + idx * cardW;
    slide.addShape(pptx.ShapeType.roundRect, {
      x,
      y: 1.55,
      w: cardW - 0.25,
      h: 2.25,
      rectRadius: 0.12,
      fill: { color: primaryColor },
      line: { color: primaryColor }
    });
    slide.addText(String(metric.value || "—"), { x: x + 0.15, y: 2.0, w: cardW - 0.55, h: 0.55, fontSize: 28, bold: true, color: "FFFFFF", align: "center", fit: "shrink" });
    slide.addText(String(metric.label || ""), { x: x + 0.15, y: 2.75, w: cardW - 0.55, h: 0.45, fontSize: 12, color: "FFFFFF", align: "center", fit: "shrink" });
  });
  if (content.bullets?.length) {
    slide.addText(content.bullets.map((item) => ({ text: String(item), options: { bullet: { type: "bullet" } } })), {
      x: 0.85,
      y: 4.35,
      w: 11.3,
      h: 1.8,
      fontSize: 14,
      color: secondaryColor,
      fit: "shrink"
    });
  }
}

function renderComparison(slide, content, primaryColor, accentColor) {
  [content.left, content.right].forEach((side, idx) => {
    const x = idx === 0 ? 0.75 : 6.85;
    const color = idx === 0 ? primaryColor : accentColor;
    slide.addShape(pptx.ShapeType.roundRect, { x, y: 1.45, w: 5.75, h: 4.65, fill: { color }, line: { color } });
    slide.addText(String(side.title || ""), { x: x + 0.25, y: 1.7, w: 5.2, h: 0.45, fontSize: 19, bold: true, color: "FFFFFF", fit: "shrink" });
    slide.addText((side.bullets || []).map((item) => ({ text: String(item), options: { bullet: { type: "bullet" } } })), {
      x: x + 0.35,
      y: 2.35,
      w: 5.0,
      h: 3.3,
      fontSize: 14,
      color: "FFFFFF",
      fit: "shrink"
    });
  });
}

function renderTimeline(slide, content, primaryColor, secondaryColor) {
  const milestones = (content.milestones || []).slice(0, 6);
  slide.addShape(pptx.ShapeType.line, { x: 0.8, y: 3.65, w: 11.8, h: 0, line: { color: primaryColor, width: 2 } });
  milestones.forEach((m, idx) => {
    const x = 0.9 + idx * (11.2 / Math.max(milestones.length - 1, 1));
    slide.addShape(pptx.ShapeType.ellipse, { x: x - 0.1, y: 3.52, w: 0.22, h: 0.22, fill: { color: primaryColor }, line: { color: primaryColor } });
    slide.addText(String(m.label || `M${idx + 1}`), { x: x - 0.4, y: 2.8, w: 0.8, h: 0.28, fontSize: 12, bold: true, color: primaryColor, align: "center" });
    slide.addText(String(m.title || ""), { x: x - 0.75, y: 4.05, w: 1.5, h: 0.95, fontSize: 12, bold: true, color: secondaryColor, align: "center", fit: "shrink" });
  });
}

function renderChart(slide, content, secondaryColor, primaryColor) {
  const data = [
    {
      name: content.series[0]?.name || "Series",
      labels: content.categories || [],
      values: content.series[0]?.values || []
    }
  ];
  slide.addChart(pptx.ChartType.bar, data, { x: 0.75, y: 1.45, w: 8.3, h: 4.9, showLegend: false, showValue: true, valAxisLabelColor: secondaryColor, catAxisLabelColor: secondaryColor });
  const bullets = content.bullets || [];
  if (bullets.length) {
    slide.addText(bullets.map((item) => ({ text: String(item), options: { bullet: { type: "bullet" } } })), { x: 9.4, y: 1.65, w: 3.1, h: 4.2, fontSize: 13, color: secondaryColor, fit: "shrink" });
  }
}

function renderTable(slide, content, secondaryColor, primaryColor) {
  const headers = content.headers || [];
  const rows = (content.rows || []).slice(0, 9);
  slide.addTable([headers, ...rows], {
    x: 0.65,
    y: 1.45,
    w: 12.0,
    h: 4.9,
    border: { color: "CBD5E1", pt: 1 },
    fontSize: 10,
    color: secondaryColor,
    fill: { color: "FFFFFF" },
    autoFit: true,
    margin: 0.05,
    rowH: 0.35
  });
}

