"""国家/地区 → 城市：语言仍用于姓名与文案，地理选择先选文化圈/国家再选城市。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# labels: 界面展示名；country: ISO 风格短码；cities: 该国/地区下城市
_REGIONS: Dict[str, List[Dict[str, Any]]] = {
    "zh": [
        {
            "key": "cn_mainland",
            "country": "CN",
            "labels": {
                "zh": "中国大陆", "en": "Mainland China", "ja": "中国大陸", "ko": "중국 본토",
                "pt": "China continental", "es": "China continental", "id": "Tiongkok Daratan",
                "ar": "الصين القارية", "fa": "سرزمین اصلی چین", "ru": "Континентальный Китай", "bg": "Континентален Китай", "tr": "Çin anakarası",
            },
            "cities": [
                "北京", "上海", "成都", "广州", "深圳", "杭州", "武汉", "西安", "南京", "重庆",
                "天津", "苏州", "长沙", "郑州", "青岛", "大连", "厦门", "福州", "合肥", "昆明",
                "贵阳", "南宁", "南昌", "哈尔滨", "长春", "沈阳", "石家庄", "太原", "兰州", "银川",
                "乌鲁木齐", "海口", "三亚",
            ],
        },
        {
            "key": "hk",
            "country": "HK",
            "labels": {
                "zh": "中国香港", "en": "Hong Kong", "ja": "香港", "ko": "홍콩",
                "pt": "Hong Kong", "es": "Hong Kong", "id": "Hong Kong",
                "ar": "هونغ كونغ", "fa": "هنگ‌کنگ", "ru": "Гонконг", "bg": "Хонконг", "tr": "Hong Kong",
            },
            "cities": ["香港"],
        },
        {
            "key": "tw",
            "country": "TW",
            "labels": {
                "zh": "中国台湾", "en": "Taiwan", "ja": "台湾", "ko": "대만",
                "pt": "Taiwan", "es": "Taiwán", "id": "Taiwan",
                "ar": "تايوان", "fa": "تایوان", "ru": "Тайвань", "bg": "Тайван", "tr": "Tayvan",
            },
            "cities": ["台北"],
        },
    ],
    "en": [
        {
            "key": "us",
            "country": "US",
            "labels": {
                "zh": "美国", "en": "United States", "ja": "アメリカ", "ko": "미국",
                "pt": "Estados Unidos", "es": "Estados Unidos", "id": "Amerika Serikat",
                "ar": "الولايات المتحدة", "fa": "ایالات متحده", "ru": "США", "bg": "Съединени щати", "tr": "ABD",
            },
            "cities": [
                "New York", "Los Angeles", "San Francisco", "Seattle", "Chicago", "Boston", "Austin",
                "Portland", "Denver", "Miami", "Atlanta", "Philadelphia", "Minneapolis", "San Diego",
                "Houston", "Dallas",
            ],
        },
        {
            "key": "gb",
            "country": "GB",
            "labels": {
                "zh": "英国", "en": "United Kingdom", "ja": "イギリス", "ko": "영국",
                "pt": "Reino Unido", "es": "Reino Unido", "id": "Britania Raya",
                "ar": "المملكة المتحدة", "fa": "بریتانیا", "ru": "Великобритания", "bg": "Обединено кралство", "tr": "Birleşik Krallık",
            },
            "cities": ["London", "Manchester", "Edinburgh"],
        },
        {
            "key": "ca",
            "country": "CA",
            "labels": {
                "zh": "加拿大", "en": "Canada", "ja": "カナダ", "ko": "캐나다",
                "pt": "Canadá", "es": "Canadá", "id": "Kanada",
                "ar": "كندا", "fa": "کانادا", "ru": "Канада", "bg": "Канада", "tr": "Kanada",
            },
            "cities": ["Toronto", "Vancouver"],
        },
        {
            "key": "au",
            "country": "AU",
            "labels": {
                "zh": "澳大利亚", "en": "Australia", "ja": "オーストラリア", "ko": "호주",
                "pt": "Austrália", "es": "Australia", "id": "Australia",
                "ar": "أستراليا", "fa": "استرالیا", "ru": "Австралия", "bg": "Австралия", "tr": "Avustralya",
            },
            "cities": ["Melbourne", "Sydney"],
        },
        {
            "key": "ie",
            "country": "IE",
            "labels": {
                "zh": "爱尔兰", "en": "Ireland", "ja": "アイルランド", "ko": "아일랜드",
                "pt": "Irlanda", "es": "Irlanda", "id": "Irlandia",
                "ar": "أيرلندا", "fa": "ایرلند", "ru": "Ирландия", "bg": "Ирландия", "tr": "İrlanda",
            },
            "cities": ["Dublin"],
        },
        {
            "key": "sg",
            "country": "SG",
            "labels": {
                "zh": "新加坡", "en": "Singapore", "ja": "シンガポール", "ko": "싱가포르",
                "pt": "Singapura", "es": "Singapur", "id": "Singapura",
                "ar": "سنغافورة", "fa": "سنگاپور", "ru": "Сингапур", "bg": "Сингапур", "tr": "Singapur",
            },
            "cities": ["Singapore"],
        },
        {
            "key": "nz",
            "country": "NZ",
            "labels": {
                "zh": "新西兰", "en": "New Zealand", "ja": "ニュージーランド", "ko": "뉴질랜드",
                "pt": "Nova Zelândia", "es": "Nueva Zelanda", "id": "Selandia Baru",
                "ar": "نيوزيلندا", "fa": "نیوزیلند", "ru": "Новая Зеландия", "bg": "Нова Зеландия", "tr": "Yeni Zelanda",
            },
            "cities": ["Auckland"],
        },
        {
            "key": "za",
            "country": "ZA",
            "labels": {
                "zh": "南非", "en": "South Africa", "ja": "南アフリカ", "ko": "남아프리카",
                "pt": "África do Sul", "es": "Sudáfrica", "id": "Afrika Selatan",
                "ar": "جنوب أفريقيا", "fa": "آفریقای جنوبی", "ru": "Южная Африка", "bg": "Южна Африка", "tr": "Güney Afrika",
            },
            "cities": ["Cape Town"],
        },
    ],
    "ja": [
        {
            "key": "jp",
            "country": "JP",
            "labels": {
                "zh": "日本", "en": "Japan", "ja": "日本", "ko": "일본",
                "pt": "Japão", "es": "Japón", "id": "Jepang",
                "ar": "اليابان", "fa": "ژاپن", "ru": "Япония", "bg": "Япония", "tr": "Japonya",
            },
            "cities": [
                "東京", "大阪", "京都", "札幌", "福岡", "名古屋", "横浜", "神戸", "仙台", "広島",
                "千葉", "埼玉", "静岡", "金沢", "新潟", "岡山", "熊本", "鹿児島", "那覇", "松山",
                "高松", "富山", "長野", "宇都宮", "岐阜", "奈良", "和歌山", "青森",
            ],
        },
    ],
    "ko": [
        {
            "key": "kr",
            "country": "KR",
            "labels": {
                "zh": "韩国", "en": "South Korea", "ja": "韓国", "ko": "대한민국",
                "pt": "Coreia do Sul", "es": "Corea del Sur", "id": "Korea Selatan",
                "ar": "كوريا الجنوبية", "fa": "کره جنوبی", "ru": "Южная Корея", "bg": "Южна Корея", "tr": "Güney Kore",
            },
            "cities": [
                "서울", "부산", "인천", "대구", "광주", "대전", "울산", "제주", "수원", "창원",
                "성남", "고양", "용인", "청주", "전주", "천안", "포항", "안산", "부천",
                "남양주", "화성", "평택", "김해", "진주", "원주", "춘천", "강릉", "여수", "순천",
            ],
        },
    ],
    "pt": [
        {
            "key": "br",
            "country": "BR",
            "labels": {
                "zh": "巴西", "en": "Brazil", "ja": "ブラジル", "ko": "브라질",
                "pt": "Brasil", "es": "Brasil", "id": "Brasil",
                "ar": "البرازيل", "fa": "برزیل", "ru": "Бразилия", "bg": "Бразилия", "tr": "Brezilya",
            },
            "cities": [
                "São Paulo", "Rio de Janeiro", "Salvador", "Brasília", "Belo Horizonte", "Fortaleza",
                "Curitiba", "Porto Alegre", "Recife", "Manaus", "Belém", "Goiânia", "Campinas",
                "São Luís", "Maceió", "Natal", "Teresina", "João Pessoa", "Florianópolis", "Vitória",
                "Santos", "Ribeirão Preto", "Uberlândia",
            ],
        },
        {
            "key": "pt",
            "country": "PT",
            "labels": {
                "zh": "葡萄牙", "en": "Portugal", "ja": "ポルトガル", "ko": "포르투갈",
                "pt": "Portugal", "es": "Portugal", "id": "Portugal",
                "ar": "البرتغال", "fa": "پرتغال", "ru": "Португалия", "bg": "Португалия", "tr": "Portekiz",
            },
            "cities": ["Lisboa", "Porto", "Coimbra"],
        },
    ],
    "es": [
        {
            "key": "es",
            "country": "ES",
            "labels": {
                "zh": "西班牙", "en": "Spain", "ja": "スペイン", "ko": "스페인",
                "pt": "Espanha", "es": "España", "id": "Spanyol",
                "ar": "إسبانيا", "fa": "اسپانیا", "ru": "Испания", "bg": "Испания", "tr": "İspanya",
            },
            "cities": [
                "Madrid", "Barcelona", "Valencia", "Sevilla", "Málaga", "Bilbao",
                "Zaragoza", "Murcia", "Palma", "Granada",
            ],
        },
        {
            "key": "mx",
            "country": "MX",
            "labels": {
                "zh": "墨西哥", "en": "Mexico", "ja": "メキシコ", "ko": "멕시코",
                "pt": "México", "es": "México", "id": "Meksiko",
                "ar": "المكسيك", "fa": "مکزیک", "ru": "Мексика", "bg": "Мексико", "tr": "Meksika",
            },
            "cities": ["México City", "Guadalajara", "Monterrey", "Puebla", "Tijuana", "Cancún"],
        },
        {
            "key": "ar",
            "country": "AR",
            "labels": {
                "zh": "阿根廷", "en": "Argentina", "ja": "アルゼンチン", "ko": "아르헨티나",
                "pt": "Argentina", "es": "Argentina", "id": "Argentina",
                "ar": "الأرجنتين", "fa": "آرژانتین", "ru": "Аргентина", "bg": "Аржентина", "tr": "Arjantin",
            },
            "cities": ["Buenos Aires", "Córdoba", "Rosario"],
        },
        {
            "key": "co",
            "country": "CO",
            "labels": {
                "zh": "哥伦比亚", "en": "Colombia", "ja": "コロンビア", "ko": "콜롬비아",
                "pt": "Colômbia", "es": "Colombia", "id": "Kolombia",
                "ar": "كولومبيا", "fa": "کلمبیا", "ru": "Колумбия", "bg": "Колумбия", "tr": "Kolombiya",
            },
            "cities": ["Bogotá", "Medellín"],
        },
        {
            "key": "pe",
            "country": "PE",
            "labels": {
                "zh": "秘鲁", "en": "Peru", "ja": "ペルー", "ko": "페루",
                "pt": "Peru", "es": "Perú", "id": "Peru",
                "ar": "بيرو", "fa": "پرو", "ru": "Перу", "bg": "Перу", "tr": "Peru",
            },
            "cities": ["Lima"],
        },
        {
            "key": "cl",
            "country": "CL",
            "labels": {
                "zh": "智利", "en": "Chile", "ja": "チリ", "ko": "칠레",
                "pt": "Chile", "es": "Chile", "id": "Chili",
                "ar": "تشيلي", "fa": "شیلی", "ru": "Чили", "bg": "Чили", "tr": "Şili",
            },
            "cities": ["Santiago"],
        },
        {
            "key": "ec",
            "country": "EC",
            "labels": {
                "zh": "厄瓜多尔", "en": "Ecuador", "ja": "エクアドル", "ko": "에콰도르",
                "pt": "Equador", "es": "Ecuador", "id": "Ekuador",
                "ar": "الإكوادور", "fa": "اکوادور", "ru": "Эквадор", "bg": "Еквадор", "tr": "Ekvador",
            },
            "cities": ["Quito"],
        },
        {
            "key": "ve",
            "country": "VE",
            "labels": {
                "zh": "委内瑞拉", "en": "Venezuela", "ja": "ベネズエラ", "ko": "베네수엘라",
                "pt": "Venezuela", "es": "Venezuela", "id": "Venezuela",
                "ar": "فنزويلا", "fa": "ونزوئلا", "ru": "Венесуэла", "bg": "Венесуела", "tr": "Venezuela",
            },
            "cities": ["Caracas"],
        },
        {
            "key": "uy",
            "country": "UY",
            "labels": {
                "zh": "乌拉圭", "en": "Uruguay", "ja": "ウルグアイ", "ko": "우루과이",
                "pt": "Uruguai", "es": "Uruguay", "id": "Uruguay",
                "ar": "الأوروغواي", "fa": "اروگوئه", "ru": "Уругвай", "bg": "Уругвай", "tr": "Uruguay",
            },
            "cities": ["Montevideo"],
        },
        {
            "key": "py",
            "country": "PY",
            "labels": {
                "zh": "巴拉圭", "en": "Paraguay", "ja": "パラグアイ", "ko": "파라과이",
                "pt": "Paraguai", "es": "Paraguay", "id": "Paraguay",
                "ar": "باراغواي", "fa": "پاراگوئه", "ru": "Парагвай", "bg": "Парагвай", "tr": "Paraguay",
            },
            "cities": ["Asunción"],
        },
        {
            "key": "bo",
            "country": "BO",
            "labels": {
                "zh": "玻利维亚", "en": "Bolivia", "ja": "ボリビア", "ko": "볼리비아",
                "pt": "Bolívia", "es": "Bolivia", "id": "Bolivia",
                "ar": "بوليفيا", "fa": "بولیوی", "ru": "Боливия", "bg": "Боливия", "tr": "Bolivya",
            },
            "cities": ["La Paz"],
        },
    ],
    "id": [
        {
            "key": "id",
            "country": "ID",
            "labels": {
                "zh": "印度尼西亚", "en": "Indonesia", "ja": "インドネシア", "ko": "인도네시아",
                "pt": "Indonésia", "es": "Indonesia", "id": "Indonesia",
                "ar": "إندونيسيا", "fa": "اندونزی", "ru": "Индонезия", "bg": "Индонезия", "tr": "Endonezya",
            },
            "cities": [
                "Jakarta", "Surabaya", "Bandung", "Medan", "Makassar", "Yogyakarta", "Semarang", "Bali",
                "Palembang", "Malang", "Balikpapan", "Pontianak", "Manado", "Padang", "Pekanbaru",
                "Bandar Lampung", "Denpasar", "Bogor", "Depok", "Tangerang", "Bekasi", "Solo",
                "Cirebon", "Jambi", "Ambon", "Kupang", "Mataram", "Banjarmasin",
            ],
        },
    ],
    "ar": [
        {
            "key": "sa",
            "country": "SA",
            "labels": {
                "zh": "沙特阿拉伯", "en": "Saudi Arabia", "ja": "サウジアラビア", "ko": "사우디아라비아",
                "pt": "Arábia Saudita", "es": "Arabia Saudí", "id": "Arab Saudi",
                "ar": "السعودية", "fa": "عربستان سعودی", "ru": "Саудовская Аравия", "bg": "Саудитска Арабия", "tr": "Suudi Arabistan",
            },
            "cities": ["الرياض", "جدة"],
        },
        {
            "key": "ae",
            "country": "AE",
            "labels": {
                "zh": "阿联酋", "en": "United Arab Emirates", "ja": "アラブ首長国連邦", "ko": "아랍에미리트",
                "pt": "Emirados Árabes Unidos", "es": "Emiratos Árabes Unidos", "id": "Uni Emirat Arab",
                "ar": "الإمارات", "fa": "امارات متحده عربی", "ru": "ОАЭ", "bg": "ОАЕ", "tr": "Birleşik Arap Emirlikleri",
            },
            "cities": ["دبي", "أبوظبي"],
        },
        {
            "key": "eg",
            "country": "EG",
            "labels": {
                "zh": "埃及", "en": "Egypt", "ja": "エジプト", "ko": "이집트",
                "pt": "Egito", "es": "Egipto", "id": "Mesir",
                "ar": "مصر", "fa": "مصر", "ru": "Египет", "bg": "Египет", "tr": "Mısır",
            },
            "cities": ["القاهرة"],
        },
        {
            "key": "ma",
            "country": "MA",
            "labels": {
                "zh": "摩洛哥", "en": "Morocco", "ja": "モロッコ", "ko": "모로코",
                "pt": "Marrocos", "es": "Marruecos", "id": "Maroko",
                "ar": "المغرب", "fa": "مراکش", "ru": "Марокко", "bg": "Мароко", "tr": "Fas",
            },
            "cities": ["الدار البيضاء"],
        },
        {
            "key": "jo",
            "country": "JO",
            "labels": {
                "zh": "约旦", "en": "Jordan", "ja": "ヨルダン", "ko": "요르단",
                "pt": "Jordânia", "es": "Jordania", "id": "Yordania",
                "ar": "الأردن", "fa": "اردن", "ru": "Иордания", "bg": "Йордания", "tr": "Ürdün",
            },
            "cities": ["عمّان"],
        },
        {
            "key": "qa",
            "country": "QA",
            "labels": {
                "zh": "卡塔尔", "en": "Qatar", "ja": "カタール", "ko": "카타르",
                "pt": "Catar", "es": "Catar", "id": "Qatar",
                "ar": "قطر", "fa": "قطر", "ru": "Катар", "bg": "Катар", "tr": "Katar",
            },
            "cities": ["الدوحة"],
        },
    ],
    "fa": [
        {
            "key": "ir",
            "country": "IR",
            "labels": {
                "zh": "伊朗", "en": "Iran", "ja": "イラン", "ko": "이란",
                "pt": "Irã", "es": "Irán", "id": "Iran",
                "ar": "إيران", "fa": "ایران", "ru": "Иран", "bg": "Иран", "tr": "İran",
            },
            "cities": ["تهران", "اصفهان", "مشهد", "شیراز", "تبریز", "کرج", "اهواز", "قم"],
        },
    ],
    "ru": [
        {
            "key": "ru",
            "country": "RU",
            "labels": {
                "zh": "俄罗斯", "en": "Russia", "ja": "ロシア", "ko": "러시아",
                "pt": "Rússia", "es": "Rusia", "id": "Rusia",
                "ar": "روسيا", "fa": "روسیه", "ru": "Россия", "bg": "Русия", "tr": "Rusya",
            },
            "cities": [
                "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург",
                "Казань", "Нижний Новгород", "Краснодар", "Сочи",
            ],
        },
    ],
    "bg": [
        {
            "key": "bg",
            "country": "BG",
            "labels": {
                "zh": "保加利亚", "en": "Bulgaria", "ja": "ブルガリア", "ko": "불가리아",
                "pt": "Bulgária", "es": "Bulgaria", "id": "Bulgaria",
                "ar": "بلغاريا", "fa": "بلغارستان", "ru": "Болгария", "bg": "България", "tr": "Bulgaristan",
            },
            "cities": [
                "София", "Пловдив", "Варна", "Бургас",
                "Русе", "Стара Загора", "Плевен", "Благоевград",
            ],
        },
    ],
    "tr": [
        {
            "key": "tr",
            "country": "TR",
            "labels": {
                "zh": "土耳其", "en": "Turkey", "ja": "トルコ", "ko": "튀르키예",
                "pt": "Turquia", "es": "Turquía", "id": "Türkiye",
                "ar": "تركيا", "fa": "ترکیه", "ru": "Турция", "bg": "Турция", "tr": "Türkiye",
            },
            "cities": [
                "İstanbul", "Ankara", "İzmir", "Bursa",
                "Antalya", "Adana", "Gaziantep", "Eskişehir",
            ],
        },
    ],
}


def _norm_lang(lang: str | None) -> str:
    return (lang or "zh").split("-")[0].lower()


def list_regions(lang: str = "zh", ui_lang: str | None = None) -> List[Dict[str, Any]]:
    """返回某语言文化圈下的国家/地区列表（含城市）。"""
    lk = _norm_lang(lang)
    display = _norm_lang(ui_lang or lang)
    out = []
    for r in _REGIONS.get(lk, _REGIONS["en"]):
        labels = r.get("labels") or {}
        out.append({
            "key": r["key"],
            "country": r["country"],
            "label": labels.get(display) or labels.get("en") or r["key"],
            "labels": dict(labels),
            "cities": list(r.get("cities") or []),
        })
    return out


def flatten_cities(lang: str = "zh") -> List[str]:
    """扁平城市列表（兼容旧 CITIES_DB / get_cities）。"""
    seen = set()
    out: List[str] = []
    for r in list_regions(lang):
        for c in r["cities"]:
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


# ASCII / 无重音 / 别名 → 目录内规范名
_CITY_ALIASES: Dict[str, str] = {
    "mexico city": "México City",
    "ciudad de mexico": "México City",
    "ciudad de méxico": "México City",
    "sao paulo": "São Paulo",
    "são paulo": "São Paulo",
    "hong kong": "香港",
    "hk": "香港",
    "taipei": "台北",
    "beijing": "北京",
    "shanghai": "上海",
    "guangzhou": "广州",
    "shenzhen": "深圳",
    "tokyo": "東京",
    "osaka": "大阪",
    "seoul": "서울",
    "busan": "부산",
    "new york": "New York",
    "los angeles": "Los Angeles",
    "san francisco": "San Francisco",
    "london": "London",
    "paris": "Paris",
    "jakarta": "Jakarta",
    "bandung": "Bandung",
    "surabaya": "Surabaya",
}


def normalize_city_query(city: str) -> str:
    if not city:
        return ""
    s = " ".join(str(city).strip().split())
    return _CITY_ALIASES.get(s.lower()) or s


def find_region_for_city(city: str, lang: str | None = None) -> Optional[Dict[str, Any]]:
    """根据城市反查所属 region；优先精确匹配，避免短名子串误判。"""
    if not city:
        return None
    city_s = normalize_city_query(city)
    lower = city_s.lower()

    def _pack(lk: str, r: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "lang": lk,
            "key": r["key"],
            "country": r["country"],
            "label": r["label"],
            "cities": r["cities"],
        }

    def _scan(langs: list[str], exact_only: bool) -> Optional[Dict[str, Any]]:
        for lk in langs:
            for r in list_regions(lk):
                for c in r["cities"]:
                    cl = c.lower()
                    if city_s == c or lower == cl:
                        return _pack(lk, r)
                    if exact_only:
                        continue
                    # 弱匹配：仅前缀且查询≥6，禁止 Angeles⊂Los Angeles 类误绑
                    if len(lower) >= 6 and (cl.startswith(lower) or lower.startswith(cl)):
                        return _pack(lk, r)
        return None

    langs = [_norm_lang(lang)] if lang else list(_REGIONS.keys())
    hit = _scan(langs, exact_only=True)
    if hit:
        return hit
    if lang:
        hit = _scan(list(_REGIONS.keys()), exact_only=True)
        if hit:
            return hit
    hit = _scan(langs if lang else list(_REGIONS.keys()), exact_only=False)
    return hit


def get_cities_for_region(lang: str, region_key: str | None = None, country: str | None = None) -> List[str]:
    """按 region key 或 country 码过滤城市；皆空则返回该语言全量。"""
    regions = list_regions(lang)
    if region_key:
        for r in regions:
            if r["key"] == region_key:
                return list(r["cities"])
        return []
    if country:
        cc = country.strip().upper()
        out: List[str] = []
        for r in regions:
            if r["country"].upper() == cc:
                out.extend(r["cities"])
        return out
    return flatten_cities(lang)


def cities_db_from_regions() -> Dict[str, List[str]]:
    return {lang: flatten_cities(lang) for lang in _REGIONS}
