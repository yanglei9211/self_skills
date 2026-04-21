"""八卦、天干地支、五行、纳音等基础数据"""

TRIGRAMS = [
    {"number": 1, "name": "乾", "pinyin": "qián", "symbol": "☰", "nature": "天", "element": "金", "direction": "西北", "family": "父", "body": "首", "animal": "马", "binary": "111"},
    {"number": 2, "name": "兑", "pinyin": "duì", "symbol": "☱", "nature": "泽", "element": "金", "direction": "西", "family": "少女", "body": "口", "animal": "羊", "binary": "110"},
    {"number": 3, "name": "离", "pinyin": "lí", "symbol": "☲", "nature": "火", "element": "火", "direction": "南", "family": "中女", "body": "目", "animal": "雉", "binary": "101"},
    {"number": 4, "name": "震", "pinyin": "zhèn", "symbol": "☳", "nature": "雷", "element": "木", "direction": "东", "family": "长男", "body": "足", "animal": "龙", "binary": "100"},
    {"number": 5, "name": "巽", "pinyin": "xùn", "symbol": "☴", "nature": "风", "element": "木", "direction": "东南", "family": "长女", "body": "股", "animal": "鸡", "binary": "011"},
    {"number": 6, "name": "坎", "pinyin": "kǎn", "symbol": "☵", "nature": "水", "element": "水", "direction": "北", "family": "中男", "body": "耳", "animal": "豕", "binary": "010"},
    {"number": 7, "name": "艮", "pinyin": "gèn", "symbol": "☶", "nature": "山", "element": "土", "direction": "东北", "family": "少男", "body": "手", "animal": "狗", "binary": "001"},
    {"number": 8, "name": "坤", "pinyin": "kūn", "symbol": "☷", "nature": "地", "element": "土", "direction": "西南", "family": "母", "body": "腹", "animal": "牛", "binary": "000"},
]

HEAVENLY_STEMS = [
    {"index": 0, "name": "甲", "pinyin": "jiǎ", "element": "木", "yin_yang": "阳"},
    {"index": 1, "name": "乙", "pinyin": "yǐ", "element": "木", "yin_yang": "阴"},
    {"index": 2, "name": "丙", "pinyin": "bǐng", "element": "火", "yin_yang": "阳"},
    {"index": 3, "name": "丁", "pinyin": "dīng", "element": "火", "yin_yang": "阴"},
    {"index": 4, "name": "戊", "pinyin": "wù", "element": "土", "yin_yang": "阳"},
    {"index": 5, "name": "己", "pinyin": "jǐ", "element": "土", "yin_yang": "阴"},
    {"index": 6, "name": "庚", "pinyin": "gēng", "element": "金", "yin_yang": "阳"},
    {"index": 7, "name": "辛", "pinyin": "xīn", "element": "金", "yin_yang": "阴"},
    {"index": 8, "name": "壬", "pinyin": "rén", "element": "水", "yin_yang": "阳"},
    {"index": 9, "name": "癸", "pinyin": "guǐ", "element": "水", "yin_yang": "阴"},
]

EARTHLY_BRANCHES = [
    {"index": 0, "name": "子", "pinyin": "zǐ", "element": "水", "yin_yang": "阳", "animal": "鼠", "month": 11, "hour_start": 23, "hour_end": 1},
    {"index": 1, "name": "丑", "pinyin": "chǒu", "element": "土", "yin_yang": "阴", "animal": "牛", "month": 12, "hour_start": 1, "hour_end": 3},
    {"index": 2, "name": "寅", "pinyin": "yín", "element": "木", "yin_yang": "阳", "animal": "虎", "month": 1, "hour_start": 3, "hour_end": 5},
    {"index": 3, "name": "卯", "pinyin": "mǎo", "element": "木", "yin_yang": "阴", "animal": "兔", "month": 2, "hour_start": 5, "hour_end": 7},
    {"index": 4, "name": "辰", "pinyin": "chén", "element": "土", "yin_yang": "阳", "animal": "龙", "month": 3, "hour_start": 7, "hour_end": 9},
    {"index": 5, "name": "巳", "pinyin": "sì", "element": "火", "yin_yang": "阴", "animal": "蛇", "month": 4, "hour_start": 9, "hour_end": 11},
    {"index": 6, "name": "午", "pinyin": "wǔ", "element": "火", "yin_yang": "阳", "animal": "马", "month": 5, "hour_start": 11, "hour_end": 13},
    {"index": 7, "name": "未", "pinyin": "wèi", "element": "土", "yin_yang": "阴", "animal": "羊", "month": 6, "hour_start": 13, "hour_end": 15},
    {"index": 8, "name": "申", "pinyin": "shēn", "element": "金", "yin_yang": "阳", "animal": "猴", "month": 7, "hour_start": 15, "hour_end": 17},
    {"index": 9, "name": "酉", "pinyin": "yǒu", "element": "金", "yin_yang": "阴", "animal": "鸡", "month": 8, "hour_start": 17, "hour_end": 19},
    {"index": 10, "name": "戌", "pinyin": "xū", "element": "土", "yin_yang": "阳", "animal": "狗", "month": 9, "hour_start": 19, "hour_end": 21},
    {"index": 11, "name": "亥", "pinyin": "hài", "element": "水", "yin_yang": "阴", "animal": "猪", "month": 10, "hour_start": 21, "hour_end": 23},
]

FIVE_ELEMENTS = [
    {"name": "金", "generates": "水", "overcomes": "木", "generated_by": "土", "overcome_by": "火", "season": "秋", "color": "白", "organ": "肺", "emotion": "悲"},
    {"name": "木", "generates": "火", "overcomes": "土", "generated_by": "水", "overcome_by": "金", "season": "春", "color": "青", "organ": "肝", "emotion": "怒"},
    {"name": "水", "generates": "木", "overcomes": "火", "generated_by": "金", "overcome_by": "土", "season": "冬", "color": "黑", "organ": "肾", "emotion": "恐"},
    {"name": "火", "generates": "土", "overcomes": "金", "generated_by": "木", "overcome_by": "水", "season": "夏", "color": "赤", "organ": "心", "emotion": "喜"},
    {"name": "土", "generates": "金", "overcomes": "水", "generated_by": "火", "overcome_by": "木", "season": "长夏", "color": "黄", "organ": "脾", "emotion": "思"},
]

SIXTY_JIAZI = [
    "甲子","乙丑","丙寅","丁卯","戊辰","己巳","庚午","辛未","壬申","癸酉",
    "甲戌","乙亥","丙子","丁丑","戊寅","己卯","庚辰","辛巳","壬午","癸未",
    "甲申","乙酉","丙戌","丁亥","戊子","己丑","庚寅","辛卯","壬辰","癸巳",
    "甲午","乙未","丙申","丁酉","戊戌","己亥","庚子","辛丑","壬寅","癸卯",
    "甲辰","乙巳","丙午","丁未","戊申","己酉","庚戌","辛亥","壬子","癸丑",
    "甲寅","乙卯","丙辰","丁巳","戊午","己未","庚申","辛酉","壬戌","癸亥",
]

NAYIN = {
    "甲子": "海中金", "乙丑": "海中金", "丙寅": "炉中火", "丁卯": "炉中火",
    "戊辰": "大林木", "己巳": "大林木", "庚午": "路旁土", "辛未": "路旁土",
    "壬申": "剑锋金", "癸酉": "剑锋金", "甲戌": "山头火", "乙亥": "山头火",
    "丙子": "涧下水", "丁丑": "涧下水", "戊寅": "城头土", "己卯": "城头土",
    "庚辰": "白蜡金", "辛巳": "白蜡金", "壬午": "杨柳木", "癸未": "杨柳木",
    "甲申": "泉中水", "乙酉": "泉中水", "丙戌": "屋上土", "丁亥": "屋上土",
    "戊子": "霹雳火", "己丑": "霹雳火", "庚寅": "松柏木", "辛卯": "松柏木",
    "壬辰": "长流水", "癸巳": "长流水", "甲午": "沙中金", "乙未": "沙中金",
    "丙申": "山下火", "丁酉": "山下火", "戊戌": "平地木", "己亥": "平地木",
    "庚子": "壁上土", "辛丑": "壁上土", "壬寅": "金箔金", "癸卯": "金箔金",
    "甲辰": "覆灯火", "乙巳": "覆灯火", "丙午": "天河水", "丁未": "天河水",
    "戊申": "大驿土", "己酉": "大驿土", "庚戌": "钗钏金", "辛亥": "钗钏金",
    "壬子": "桑柘木", "癸丑": "桑柘木", "甲寅": "大溪水", "乙卯": "大溪水",
    "丙辰": "沙中土", "丁巳": "沙中土", "戊午": "天上火", "己未": "天上火",
    "庚申": "石榴木", "辛酉": "石榴木", "壬戌": "大海水", "癸亥": "大海水",
}

# 月柱天干推算：年干 -> 正月天干
MONTH_STEM_TABLE = {
    "甲": 2, "己": 2,  # 丙寅月起
    "乙": 4, "庚": 4,  # 戊寅月起
    "丙": 6, "辛": 6,  # 庚寅月起
    "丁": 8, "壬": 8,  # 壬寅月起
    "戊": 0, "癸": 0,  # 甲寅月起
}

# 时柱天干推算：日干 -> 子时天干
HOUR_STEM_TABLE = {
    "甲": 0, "己": 0,  # 甲子时起
    "乙": 2, "庚": 2,  # 丙子时起
    "丙": 4, "辛": 4,  # 戊子时起
    "丁": 6, "壬": 6,  # 庚子时起
    "戊": 8, "癸": 8,  # 壬子时起
}

# 节气（简化：每月大致节气日期，用于确定月柱）
SOLAR_TERMS = {
    1: (2, 4, "立春"),   2: (3, 6, "惊蛰"),
    3: (4, 5, "清明"),   4: (5, 6, "立夏"),
    5: (6, 6, "芒种"),   6: (7, 7, "小暑"),
    7: (8, 7, "立秋"),   8: (9, 8, "白露"),
    9: (10, 8, "寒露"),  10: (11, 7, "立冬"),
    11: (12, 7, "大雪"), 12: (1, 6, "小寒"),
}

# 地支藏干：本气 / 中气 / 余气
BRANCH_HIDDEN_STEMS = {
    "子": [("癸", "本气")],
    "丑": [("己", "本气"), ("癸", "中气"), ("辛", "余气")],
    "寅": [("甲", "本气"), ("丙", "中气"), ("戊", "余气")],
    "卯": [("乙", "本气")],
    "辰": [("戊", "本气"), ("乙", "中气"), ("癸", "余气")],
    "巳": [("丙", "本气"), ("庚", "中气"), ("戊", "余气")],
    "午": [("丁", "本气"), ("己", "中气")],
    "未": [("己", "本气"), ("丁", "中气"), ("乙", "余气")],
    "申": [("庚", "本气"), ("壬", "中气"), ("戊", "余气")],
    "酉": [("辛", "本气")],
    "戌": [("戊", "本气"), ("辛", "中气"), ("丁", "余气")],
    "亥": [("壬", "本气"), ("甲", "中气")],
}

# 十神名称映射（日主对其他天干的关系）
# key=(日主阴阳, 对方五行关系, 对方阴阳同否)
TEN_GODS_TABLE = {
    ("同类", True):  "比肩",   ("同类", False): "劫财",
    ("生我", False): "正印",   ("生我", True):  "偏印",
    ("我生", False): "伤官",   ("我生", True):  "食神",
    ("克我", False): "正官",   ("克我", True):  "七杀",
    ("我克", False): "正财",   ("我克", True):  "偏财",
}

ELEMENT_OF_STEM = {"甲":"木","乙":"木","丙":"火","丁":"火","戊":"土","己":"土","庚":"金","辛":"金","壬":"水","癸":"水"}
YINYANG_OF_STEM = {"甲":"阳","乙":"阴","丙":"阳","丁":"阴","戊":"阳","己":"阴","庚":"阳","辛":"阴","壬":"阳","癸":"阴"}

GENERATE_MAP = {"木":"火","火":"土","土":"金","金":"水","水":"木"}
OVERCOME_MAP = {"木":"土","火":"金","土":"水","金":"木","水":"火"}
GENERATED_BY = {v: k for k, v in GENERATE_MAP.items()}
OVERCOME_BY  = {v: k for k, v in OVERCOME_MAP.items()}

def get_ten_god(day_master, other_stem):
    """计算 other_stem 相对于 day_master 的十神"""
    if day_master == other_stem:
        return "比肩"
    dm_e = ELEMENT_OF_STEM[day_master]
    ot_e = ELEMENT_OF_STEM[other_stem]
    dm_yy = YINYANG_OF_STEM[day_master]
    ot_yy = YINYANG_OF_STEM[other_stem]
    same_yy = (dm_yy == ot_yy)

    if dm_e == ot_e:
        return TEN_GODS_TABLE[("同类", same_yy)]
    elif GENERATE_MAP[ot_e] == dm_e:
        return TEN_GODS_TABLE[("生我", same_yy)]
    elif GENERATE_MAP[dm_e] == ot_e:
        return TEN_GODS_TABLE[("我生", same_yy)]
    elif OVERCOME_MAP[ot_e] == dm_e:
        return TEN_GODS_TABLE[("克我", same_yy)]
    elif OVERCOME_MAP[dm_e] == ot_e:
        return TEN_GODS_TABLE[("我克", same_yy)]
    return "?"

# 干支合冲刑害
TIANGAN_HEHUA = [("甲","己","土"), ("乙","庚","金"), ("丙","辛","水"), ("丁","壬","木"), ("戊","癸","火")]
DIZHI_LIUHE = [("子","丑","土"), ("寅","亥","木"), ("卯","戌","火"), ("辰","酉","金"), ("巳","申","水"), ("午","未","火")]
DIZHI_SANHE = [("申","子","辰","水"), ("寅","午","戌","火"), ("巳","酉","丑","金"), ("亥","卯","未","木")]
DIZHI_SANHUI = [("寅","卯","辰","木"), ("巳","午","未","火"), ("申","酉","戌","金"), ("亥","子","丑","水")]
DIZHI_LIUCHONG = [("子","午"), ("丑","未"), ("寅","申"), ("卯","酉"), ("辰","戌"), ("巳","亥")]
DIZHI_XING = [("寅","巳","申","无恩之刑"), ("丑","戌","未","恃势之刑"), ("子","卯","","无礼之刑"), ("辰","辰","","自刑"), ("午","午","","自刑"), ("酉","酉","","自刑"), ("亥","亥","","自刑")]
DIZHI_HAI = [("子","未"), ("丑","午"), ("寅","巳"), ("卯","辰"), ("申","亥"), ("酉","戌")]
