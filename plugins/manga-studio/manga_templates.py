"""Quick-start story templates for the manga-studio plugin.

These are read-only seeds the UI offers to first-time users so they
can fire off a full pipeline without writing a story from scratch. A
template carries (a) a story prompt, (b) a sensible default visual
style + ratio + panel layout, and (c) a short bilingual blurb the UI
shows in the picker.

The list is intentionally a hand-curated literal rather than coming
from the database — these are conventions, not user data, and we want
them to ship pinned to the plugin version. Updating the catalogue
means editing this file and bumping the plugin version.

The shape mirrors the GET /templates response so the UI can use the
record verbatim when applying a template to the Studio form.
"""

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────────
#   Schema
# ─────────────────────────────────────────────────────────────────────────
#
#   id              str  — stable identifier (snake_case, used as React key)
#   title_zh / _en  str  — picker label; pick by current UI language
#   blurb_zh / _en  str  — one-line description shown under the title
#   tag             str  — visual badge ("少年" / "悬疑" / ...)
#   visual_style    str  — must match an entry from manga_models.VISUAL_STYLES
#   ratio           str  — must match an entry from manga_models.RATIOS
#   n_panels        int  — 1 ≤ n ≤ 30
#   seconds_per_panel int — 2 ≤ s ≤ 15
#   story_zh        str  — Chinese prompt, ≤ 8000 chars
#   story_en        str  — English prompt, ≤ 8000 chars
#
#   The UI picks the language-appropriate fields. Backends only see the
#   resolved ``story`` (already a single string) — they don't care about
#   zh/en split.

TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "kendo_swordspirit",
        "title_zh": "剑灵觉醒",
        "title_en": "Sword Spirit Awakening",
        "blurb_zh": "高校剑道部少年意外觉醒上古剑灵 · 少年战斗番",
        "blurb_en": "A kendo-club teen awakens an ancient sword spirit · Shonen action",
        "tag": "少年",
        "visual_style": "shonen",
        "ratio": "9:16",
        "n_panels": 6,
        "seconds_per_panel": 5,
        "story_zh": (
            "高一少年李雷是剑道部里最不起眼的成员。一次校际比赛中，他被对手"
            "重击倒地，脑海里却传来千年剑灵韩雪的低语。剑灵告诉他：他是上古"
            "剑仙在现代的转世，必须在十八岁前唤醒全部剑诀，否则将永远沉睡。"
            "李雷起身、握紧木剑，在全场惊呼中以一个不可能的姿势接下了对手的"
            "必杀招——剑光乍现，他的修行才真正开始。"
        ),
        "story_en": (
            "Li Lei is the most unremarkable member of his high-school kendo "
            "club. During an inter-school match, an opponent knocks him to "
            "the ground — but a thousand-year-old sword spirit named Han "
            "Xue speaks in his mind. She tells him he is the reincarnation "
            "of an ancient sword saint, and he must awaken all sword "
            "techniques before he turns eighteen or sleep forever. Li Lei "
            "rises, grips his bokken, and in a stance no one believed "
            "possible blocks the opponent's killing strike — a flash of "
            "sword light, and his real training begins."
        ),
    },
    {
        "id": "midnight_train",
        "title_zh": "末班地铁",
        "title_en": "The Last Train",
        "blurb_zh": "深夜末班地铁的诡异乘客 · 都市悬疑",
        "blurb_en": "Eerie passengers on the last metro of the night · Urban mystery",
        "tag": "悬疑",
        "visual_style": "seinen",
        "ratio": "9:16",
        "n_panels": 6,
        "seconds_per_panel": 5,
        "story_zh": (
            "凌晨两点的末班地铁，林夕本以为车厢空无一人。直到她抬头，发现"
            "对面坐着六个穿同款黑色雨衣的乘客，每个人都盯着她，一动不动。"
            "下一站还有四十分钟，手机没有信号。她数着每一节车厢的灯——"
            "一盏接一盏熄灭。当最后一盏灯灭下时，她身边的座位上多出了一个"
            "穿黑色雨衣的乘客。"
        ),
        "story_en": (
            "Two in the morning, last train. Lin Xi thinks the carriage is "
            "empty until she looks up and finds six passengers in identical "
            "black raincoats sitting opposite, all staring at her, none "
            "moving. The next station is forty minutes away. Her phone has "
            "no signal. She counts the lamps in the cabin — they go out "
            "one by one. When the last one dies, a seventh passenger in a "
            "black raincoat is sitting beside her."
        ),
    },
    {
        "id": "starter_cafe",
        "title_zh": "深夜小吃店",
        "title_en": "Late-Night Diner",
        "blurb_zh": "下班后的小吃店相遇 · 治愈日常",
        "blurb_en": "After-work encounters at a tiny diner · Healing slice-of-life",
        "tag": "治愈",
        "visual_style": "shoujo",
        "ratio": "9:16",
        "n_panels": 5,
        "seconds_per_panel": 5,
        "story_zh": (
            "城市的角落里有一家只在凌晨开门的小吃店，老板沉默寡言，菜单只"
            "有一道蛋包饭。每天晚上都有不同的客人推门而入：失恋的实习生、"
            "加班的程序员、半夜逃出公司的会计——他们坐在同一张吧台前，吃完"
            "蛋包饭再各自回到生活。今晚走进店里的，是一个抱着小猫、眼角带"
            "泪的年轻女孩。"
        ),
        "story_en": (
            "In a corner of the city sits a tiny diner that only opens past "
            "midnight. The owner barely speaks, and the menu has a single "
            "item: omurice. Every night a different customer pushes the "
            "door open — a heartbroken intern, a programmer pulling an "
            "all-nighter, an accountant escaping her firm at 3 AM. They sit "
            "at the same counter, finish the omurice, then return to their "
            "own life. Tonight, the door opens for a young woman holding a "
            "kitten, tears in the corners of her eyes."
        ),
    },
    {
        "id": "mecha_pilot",
        "title_zh": "驾驶舱里的少女",
        "title_en": "The Pilot in the Cockpit",
        "blurb_zh": "末日机甲战 · 青少年成长 · 科幻战斗",
        "blurb_en": "Apocalypse mecha combat · Coming-of-age sci-fi action",
        "tag": "科幻",
        "visual_style": "cyberpunk",
        "ratio": "16:9",
        "n_panels": 7,
        "seconds_per_panel": 5,
        "story_zh": (
            "二十二世纪，地球被来自深空的「环形者」围困了八十年。十四岁的"
            "少女艾莉在第三防线的地下机库被选为新一任「夜枭」机甲驾驶员。"
            "今天是她第一次出击——四吨重的金属在她身体周围闭合，HUD亮起，"
            "通讯频道里传来她哥哥的声音：「妹妹，你只需要回来。」"
        ),
        "story_en": (
            "It is the 22nd century and Earth has held the line against the "
            'deep-space "Ringers" for eighty years. In the underground '
            "hangar of the third defensive belt, fourteen-year-old Ailey is "
            "chosen as the next pilot of the Owl mecha. Today is her first "
            "sortie — four tons of metal closes around her, the HUD lights "
            'up, and over the comms her older brother says: "Just come '
            "back, sis. That's all you have to do.\""
        ),
    },
    {
        "id": "office_legend",
        "title_zh": "996的传说",
        "title_en": "Legend of 996",
        "blurb_zh": "互联网公司里的奇幻冒险 · 都市奇幻",
        "blurb_en": "A fantasy adventure inside a tech company · Urban fantasy",
        "tag": "奇幻",
        "visual_style": "webtoon",
        "ratio": "9:16",
        "n_panels": 6,
        "seconds_per_panel": 5,
        "story_zh": (
            "周五凌晨三点，互联网大厂的代码农工小张在第一千次重构祖传屎山"
            "时按下回车，整个写字楼的灯光骤然变红。他面前的屏幕弹出一个"
            "对话框：「检测到史诗级技术债，是否进入「程序员次元」？」"
            "他下意识点了「确定」——下一秒，他握着键盘剑，从工位上掉进了"
            "一片由 stack overflow 评论构成的无尽深渊。"
        ),
        "story_en": (
            "Friday, 3 AM. In a sprawling tech company a junior engineer "
            "named Zhang hits Enter on his thousandth refactor of legacy "
            "spaghetti code. Every light in the building turns red. A "
            'dialog box pops up on his monitor: "Epic-tier tech debt '
            'detected — enter the Programmer Dimension?" He clicks '
            "Confirm out of habit. The next second, keyboard-sword in "
            "hand, he tumbles out of his cubicle into an endless abyss "
            "made of Stack Overflow comments."
        ),
    },
]


def list_templates() -> list[dict[str, Any]]:
    """Return the canonical template catalogue.

    Defensive copy: callers can mutate the returned dicts without
    polluting the module-level constant. We don't deep-copy individual
    string fields because they're immutable in Python.
    """
    return [dict(item) for item in TEMPLATES]


def find_template(template_id: str) -> dict[str, Any] | None:
    """Locate a template by id; returns ``None`` if absent.

    The plugin doesn't currently expose a ``GET /templates/{id}``
    endpoint (the UI fetches the whole list once and indexes locally),
    but this helper keeps the surface symmetric with the other
    catalogue modules.
    """
    for item in TEMPLATES:
        if item.get("id") == template_id:
            return dict(item)
    return None
