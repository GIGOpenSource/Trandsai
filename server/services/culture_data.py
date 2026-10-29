from __future__ import annotations
# 地区文化数据 —— 姓名、城市、文化常识

from services.personality_catalog import (
    get_personality_labels_db,
    join_personality_labels,
    personality_label_map,
    sample_personality_keys,
    sample_personality_labels,
)
from services.persona_axes import sample_persona_axes
from services.region_catalog import (
    cities_db_from_regions,
    find_region_for_city,
    get_cities_for_region,
    list_regions,
)

# ===== 姓名库（按语言和性别） =====
NAMES_DB = {
    "zh": {
        "male": ["张伟", "刘洋", "杨帆", "黄磊", "王强", "李明", "陈浩", "赵磊", "周杰", "吴昊", "徐鹏", "孙涛", "马骏", "朱轩", "胡凯", "郭磊", "何宇", "高翔", "林枫", "罗宇", "郑恺", "梁辰", "谢霆", "宋扬", "唐睿", "许哲", "韩沐", "冯潇", "邓楠", "曹旭", "彭越", "曾舜", "肖恒", "董睿", "袁朗", "蒋屹", "蔡珩", "贾澄", "丁烁", "魏临", "薛泽", "叶琛", "阎皓", "潘岳", "汪澈", "戴青", "夏珩", "姜湛", "范屿", "傅宸"],
        "female": ["王芳", "李娜", "陈静", "赵敏", "刘婷", "张悦", "李雪", "王慧", "周瑶", "吴萱", "徐琳", "孙茜", "马蕊", "朱瑾", "胡玥", "郭瑶", "何苒", "高沁", "林溪", "罗璇", "郑柔", "梁音", "谢澜", "宋窈", "唐绾", "许熹", "韩漾", "冯泠", "邓莞", "曹藜", "彭缨", "曾珞", "萧棠", "董槿", "袁蘅", "蒋湄", "蔡葭", "贾绡", "丁翎", "魏缇", "薛纨", "叶纨", "阎缨", "潘纨", "汪纨", "戴纨", "夏纨", "姜纨", "范纨", "傅纨"],
    },
    "en": {
        "male": ["James", "William", "Benjamin", "Henry", "Oliver", "Lucas", "Alexander", "Daniel", "Matthew", "Jackson", "Sebastian", "Theodore", "Samuel", "Joseph", "David", "Wyatt", "John", "Owen", "Dylan", "Luke", "Gabriel", "Anthony", "Isaac", "Grayson", "Jack", "Julian", "Levi", "Christopher", "Joshua", "Andrew", "Lincoln", "Mateo", "Ryan", "Jaxon", "Nathan", "Aaron", "Isaiah", "Thomas", "Charles", "Caleb", "Josiah", "Christian", "Hunter", "Eli", "Jonathan", "Connor", "Miles", "Jeremiah", "Nolan", "Roman"],
        "female": ["Emily", "Olivia", "Sophia", "Ava", "Isabella", "Mia", "Charlotte", "Amelia", "Harper", "Evelyn", "Abigail", "Ella", "Scarlett", "Grace", "Chloe", "Victoria", "Riley", "Aria", "Lily", "Aurora", "Zoe", "Natalie", "Hannah", "Layla", "Brooklyn", "Leah", "Stella", "Hazel", "Ellie", "Paisley", "Nova", "Audrey", "Skylar", "Violet", "Claire", "Bella", "Lucy", "Anna", "Samantha", "Caroline", "Genesis", "Kennedy", "Maya", "Naomi", "Sarah", "Madelyn", "Elena", "Alice", "Gabriella", "Quinn"],
    },
    "ja": {
        "male": ["翔太", "蓮", "悠真", "樹", "湊", "大和", "陽翔", "蒼太", "蒼空", "碧", "大翔", "凪", "涼太", "颯太", "蓮斗", "悠斗", "陽向", "陽太", "海斗", "大智", "健太", "拓也", "誠", "健", "雄大", "悠人", "匠", "直樹", "翔", "啓太"],
        "female": ["美咲", "陽葵", "結衣", "桜", "凛", "愛莉", "葵", "芽依", "紬", "莉子", "心春", "陽菜", "美月", "結菜", "琴音", "愛菜", "凛音", "妃咲", "心咲", "紗良", "花音", "真央", "優奈", "七海", "美羽", "彩乃", "心愛", "桜子", "結月", "陽毬"],
    },
    "ko": {
        "male": ["지훈", "민준", "도윤", "예준", "서준", "하준", "시우", "준서", "주원", "현우", "지호", "준영", "건우", "현준", "서진", "민재", "시윤", "지환", "윤우", "도현", "재윤", "민성", "현서", "영훈", "정우", "승민", "동현", "태윤", "재민", "현석"],
        "female": ["서연", "지우", "서아", "하은", "민서", "지유", "윤서", "수아", "지민", "채원", "지윤", "서윤", "하윤", "수빈", "지안", "소윤", "예은", "다은", "채은", "현서", "민지", "유진", "수민", "지원", "서영", "수연", "예린", "채윤", "지현", "예서"],
    },
    "pt": {
        "male": ["Lucas", "Gabriel", "Matheus", "Pedro", "João", "Enzo", "Guilherme", "Rafael", "Miguel", "Arthur", "Bernardo", "Davi", "Heitor", "Lorenzo", "Theo", "Bruno", "Felipe", "Igor", "Marcos", "Vinícius", "Diego", "Leandro", "Tiago", "André", "Eduardo", "Ricardo", "Marcelo", "Alexandre", "Fábio", "Gustavo"],
        "female": ["Isabela", "Valentina", "Julia", "Laura", "Manuela", "Sophia", "Alice", "Helena", "Luiza", "Giovanna", "Maria Eduarda", "Beatriz", "Larissa", "Mariana", "Yasmin", "Camila", "Letícia", "Amanda", "Bianca", "Juliana", "Fernanda", "Carolina", "Patrícia", "Daniela", "Renata", "Gabriela", "Raquel", "Natália", "Priscila", "Tatiana"],
    },
    "es": {
        "male": ["Diego", "Alejandro", "Javier", "Carlos", "Daniel", "Miguel", "José", "Antonio", "Juan", "Luis", "Manuel", "Francisco", "Pablo", "Jorge", "Mario", "Sergio", "Fernando", "Andrés", "Raúl", "Alberto", "Enrique", "Rubén", "Adrián", "Martín", "Hugo", "Lucas", "Mateo", "Leo", "Marcos", "Álvaro"],
        "female": ["Carmen", "Lucía", "Sofía", "María", "Ana", "Elena", "Paula", "Laura", "Alba", "Marta", "Sara", "Julia", "Claudia", "Irene", "Natalia", "Silvia", "Cristina", "Patricia", "Rosa", "Mercedes", "Pilar", "Isabel", "Teresa", "Ángela", "Rocío", "Beatriz", "Nuria", "Carolina", "Daniela", "Emma"],
    },
    "id": {
        "male": ["Budi", "Agus", "Andi", "Joko", "Rudi", "Dedi", "Hadi", "Slamet", "Eko", "Indra", "Yanto", "Wawan", "Iwan", "Adi", "Dwi", "Tri", "Bayu", "Fajar", "Gilang", "Irfan", "Kurnia", "Lintang", "Nanda", "Oka", "Pandu", "Qori", "Rizky", "Satria", "Taufik", "Yoga"],
        "female": ["Dewi", "Siti", "Rina", "Maya", "Lestari", "Putri", "Ani", "Nur", "Wulan", "Citra", "Fitri", "Indah", "Kartika", "Melati", "Ratna", "Sari", "Indri", "Yuni", "Mega", "Intan", "Kirana", "Laras", "Mawar", "Nadia", "Oktavia", "Pratiwi", "Qonita", "Rani", "Shinta", "Tari"],
    },
    "ar": {
        "male": ["محمد", "أحمد", "علي", "عمر", "يوسف", "خالد", "حسن", "إبراهيم", "عبدالله", "سعيد", "فهد", "سلطان", "رامي", "طارق", "وليد", "ناصر", "ماجد", "عادل", "كريم", "سامر", "زياد", "حسام", "أنور", "بلال", "جمال", "حمزة", "رشيد", "سامي", "فادي", "ياسر"],
        "female": ["فاطمة", "عائشة", "مريم", "نور", "ليلى", "سارة", "هدى", "أمل", "دينا", "سلمى", "ياسمين", "ندى", "هند", "رنا", "لينا", "جواهر", "منى", "رقية", "زينب", "حنان", "إيمان", "سناء", "لمياء", "نادية", "ريم", "مها", "غادة", "بشرى", "خلود", "دانة"],
    },
    "fa": {
        "male": ["علی", "محمد", "حسین", "رضا", "مهدی", "امیر", "حسن", "سعید", "حامد", "مجید", "فرهاد", "کامران", "بهرام", "سینا", "پویا", "آرمان", "نیما", "کیان", "سامان", "بهروز", "جواد", "عباس", "محسن", "ناصر", "پارسا", "شایان", "کسری", "رامین", "امید", "دانیال"],
        "female": ["فاطمه", "زهرا", "مریم", "سارا", "نرگس", "الناز", "مهسا", "نازنین", "پریسا", "شیدا", "یاسمن", "گلناز", "هانیه", "سمیرا", "لیلا", "نسترن", "آیدا", "ترانه", "رویا", "مینو", "سپیده", "بهاره", "شیرین", "مونا", "نگین", "آزاده", "فریبا", "سوسن", "پرستو", "نیلوفر"],
    },
    "ru": {
        "male": ["Александр", "Дмитрий", "Максим", "Иван", "Артём", "Сергей", "Андрей", "Алексей", "Михаил", "Никита", "Егор", "Кирилл", "Илья", "Роман", "Владимир", "Павел", "Николай", "Денис", "Тимофей", "Даниил", "Антон", "Олег", "Виктор", "Юрий", "Борис", "Глеб", "Степан", "Фёдор", "Константин", "Леонид"],
        "female": ["Анна", "Мария", "Елена", "Ольга", "Наталья", "Екатерина", "Татьяна", "Ирина", "Светлана", "Юлия", "Анастасия", "Дарья", "Полина", "Виктория", "Алина", "Ксения", "София", "Александра", "Валерия", "Вера", "Людмила", "Галина", "Надежда", "Любовь", "Кристина", "Маргарита", "Вероника", "Диана", "Елизавета", "Алёна"],
    },
    "bg": {
        "male": ["Георги", "Иван", "Димитър", "Николай", "Петър", "Стоян", "Христо", "Стефан", "Васил", "Тодор", "Александър", "Борис", "Кирил", "Ангел", "Мартин", "Калоян", "Любомир", "Радослав", "Пламен", "Красимир", "Ивайло", "Божидар", "Веселин", "Асен", "Благой", "Захари", "Методи", "Светослав", "Тихомир", "Юлиян"],
        "female": ["Мария", "Ивана", "Елена", "Николина", "Петя", "Стефка", "Даниела", "Виктория", "Александра", "Гергана", "Десислава", "Йорданка", "Красимира", "Любка", "Милена", "Надежда", "Пенка", "Радка", "Силвия", "Таня", "Цветелина", "Юлия", "Боряна", "Весела", "Габриела", "Диана", "Елица", "Жана", "Зорница", "Калина"],
    },
    "tr": {
        "male": ["Mehmet", "Ahmet", "Mustafa", "Ali", "Hüseyin", "Hasan", "İbrahim", "Yusuf", "Ömer", "Murat", "Emre", "Burak", "Can", "Cem", "Deniz", "Eren", "Furkan", "Gökhan", "Hakan", "Kemal", "Mert", "Onur", "Serkan", "Tolga", "Uğur", "Volkan", "Yiğit", "Barış", "Cenk", "Oğuz"],
        "female": ["Ayşe", "Fatma", "Emine", "Hatice", "Zeynep", "Elif", "Merve", "Esra", "Özlem", "Hülya", "Sevgi", "Derya", "Gül", "Melis", "Ceren", "İrem", "Büşra", "Seda", "Tuğba", "Yasemin", "Pınar", "Ebru", "Aslı", "Gamze", "Nihan", "Selin", "İpek", "Leyla", "Nur", "Sibel"],
    },
}


# ===== 城市库（按语言；权威来源 region_catalog，运行时派生，避免双份漂移） =====
CITIES_DB = cities_db_from_regions()


# ===== 文化常识知识库（按语言，用于 RAG 导入） =====
# 每条包含 title, content, category, tags, source, language
CULTURAL_KNOWLEDGE = [
    # 中文文化
    {
        "title": "中国式恋爱沟通习惯",
        "content": "中国年轻人在恋爱中常用微信作为主要沟通工具。表达方式往往比较含蓄，男生倾向于通过行动（送早餐、帮忙解决问题）来表达关心，女生则更喜欢通过分享日常细节来建立亲密感。'在干嘛'、'吃了吗'是典型的开场白，代表着'我想你了'的潜台词。吵架后常用冷战方式，但内心期待对方主动哄。节假日尤其是情人节、520、七夕非常重要，送礼物是基本礼仪。",
        "category": "other",
        "tags": ["dating", "communication", "china", "culture"],
        "source": "culture_db",
        "language": "zh",
    },
    {
        "title": "中国年轻人的社交礼仪",
        "content": "在中国，初次见面不会过于亲密，保持适当距离是礼貌。朋友圈点赞是维持关系的重要方式。约会通常由男生主动提出，但现代女生也越来越主动。见家长是非常严肃的步骤，意味着关系进入婚姻考虑阶段。红包文化在节日和特殊日子很常见。火锅、烧烤是常见的约会聚餐选择，因为可以边煮边聊，氛围轻松。",
        "category": "other",
        "tags": ["social", "etiquette", "china", "culture"],
        "source": "culture_db",
        "language": "zh",
    },
    # 英文/欧美文化
    {
        "title": "Western Dating Communication Styles",
        "content": "In Western dating culture, direct communication is valued. People often say 'I miss you' or 'I like you' explicitly rather than implying it. Texting is casual and frequent, but there's also respect for personal space and alone time. 'Netflix and chill' is a common casual date idea. Splitting the bill (going Dutch) is common in early dating. PDA (public displays of affection) like holding hands and light kissing are socially acceptable. Ghosting is unfortunately common when someone loses interest.",
        "category": "other",
        "tags": ["dating", "communication", "western", "culture"],
        "source": "culture_db",
        "language": "en",
    },
    {
        "title": "British Social Etiquette and Humor",
        "content": "British people are famous for their dry, self-deprecating humor and understatement. Saying 'not bad' often means 'very good.' Politeness and queuing are deeply ingrained. In dating, Brits tend to be reserved initially but warm up over drinks at a pub. The 'stiff upper lip' means keeping emotions under control in public. Sunday roasts, afternoon tea, and pub culture are central to social life. Banter (playful teasing) is a sign of affection and closeness.",
        "category": "other",
        "tags": ["social", "etiquette", "uk", "culture"],
        "source": "culture_db",
        "language": "en",
    },
    # 日本文化
    {
        "title": "日本の恋愛コミュニケーション",
        "content": "日本の恋愛では、直接的な告白（告白／こくはく）が非常に重要で、付き合う前に必ず正式な告白をするのが一般的。LINE（メッセージアプリ）が主要なコミュニケーションツールで、スタンプの使い方に細かな気持ちが込められる。デートでは男性が支払うことが多いが、最近は割り勘も増えている。クリスマスとバレンタインは伙伴にとって特別なイベントで、手作りチョコやプレゼントが欠かせない。遠慮（えんりょ）や察し（さっし）の文化があり、相手の気持ちを推測する力が重視される。",
        "category": "other",
        "tags": ["dating", "communication", "japan", "culture"],
        "source": "culture_db",
        "language": "ja",
    },
    {
        "title": "日本の社交マナー",
        "content": "日本では初対面の人に対して丁寧語を使うのが基本。名刺交換は両手で行う。デートの待ち合わせには絶対に遅れない。お中元お歳暮、義理チョコなど贈り物の文化が発達している。飲み会の二次会、三次会は親密さの指標となる。居酒屋文化ではお互いのグラスに酒を注ぎ合う（お酌）ことで親しみを示す。Lineの返信速度も相手への気遣いの表れとされる。",
        "category": "other",
        "tags": ["social", "etiquette", "japan", "culture"],
        "source": "culture_db",
        "language": "ja",
    },
    # 韩国文化
    {
        "title": "한국의 연애 커뮤니케이션",
        "content": "한국에서는 '썸'이라는 애매한 단계가 공식적으로 인정된다. 카카오톡이 주요 메신저이며, 프로필 사진이나 상태 메시지가 관계 상태를 암시하는 경우가 많다. '밥 먹었어?'는 '보고 싶어'의 대체 표현이다. 기념일 문화가 발달해 있어 100일, 200일, 1주년 등을 중요하게 여긴다. 커플템(커플링, 커플옷)을 하는 것이 일반적이고, '애교'는 연애에서 매우 중요한 요소이다. '빨리빨리' 문화 때문에 연락 답장이 늦으면 서운해하는 경향이 있다.",
        "category": "other",
        "tags": ["dating", "communication", "korea", "culture"],
        "source": "culture_db",
        "language": "ko",
    },
    {
        "title": "한국의 데이트 문화",
        "content": "한국의 데이트는 보통 남성이 주도하지만, 최근에는 여성도 적극적으로 제안하는 경우가 늘고 있다. 첫 데이트는 보통 카페나 식사로 시작한다. '넷플릭스 앤 칠' 같은 홈 데이트도 인기가 많다. 소개팅과 미팅은 매우 흔한 연애 시작 방식이다. 부모님께 인사드리는 것은 매우 진지한 단계로 여겨진다. 명절(추석, 설날)에는 선물 세트를 주고받는 문화가 있다.",
        "category": "other",
        "tags": ["social", "etiquette", "korea", "culture"],
        "source": "culture_db",
        "language": "ko",
    },
    # 葡萄牙/巴西文化
    {
        "title": "Cultura de Namoro no Brasil",
        "content": "No Brasil, o WhatsApp é absolutamente central nos relacionamentos. Áudios longos, figurinhas e memes são formas legítimas de expressar afeto. Os brasileiros tendem a ser muito calorosos e demonstrativos desde o início do namoro. 'Saudade' é uma palavra usada constantemente. O futebol é uma linguagem universal — torcer pelo mesmo time é um plus enorme. Praias, churrascos de domingo e festas de família são cenários comuns de encontro. O conceito de 'ficar' (algo entre amizade e namoro) é muito comum entre jovens.",
        "category": "other",
        "tags": ["dating", "communication", "brazil", "culture"],
        "source": "culture_db",
        "language": "pt",
    },
    {
        "title": "Etiqueta Social Brasileira",
        "content": "Brasileiros são conhecidos por seu calor humano e proximidade física. Abraços e beijos no rosto (dois, no Rio; três, em São Paulo e no Nordeste) são normais mesmo entre conhecidos. Chegar atrasado em encontros sociais é culturalmente aceito (o 'horário brasileiro'). Conversas sobre futebol, música e comida são ótimas formas de quebrar o gelo. Churrasco de domingo em família é um evento sagrado. O carnaval e o réveillon na praia são as festas mais importantes do ano.",
        "category": "other",
        "tags": ["social", "etiquette", "brazil", "culture"],
        "source": "culture_db",
        "language": "pt",
    },
    # 西班牙文化
    {
        "title": "Cultura de Citas en España",
        "content": "En España, las relaciones suelen ser apasionadas y intensas desde el principio. La siesta no es solo dormir; es una forma de vida que ralentiza el ritmo y valora el placer. Las tapas y el vermut son rituales sociales esenciales. Los españoles son muy directos y expresivos con sus emociones. 'Quedar' para tomar algo es la forma más común de iniciar una relación. Las familias están muy unidas y conocer a los suegros es un paso importante. La cena es tarde (21:00-22:00) y puede durar horas. Las fiestas de pueblo y las ferias son momentos clave para socializar.",
        "category": "other",
        "tags": ["dating", "communication", "spain", "culture"],
        "source": "culture_db",
        "language": "es",
    },
    {
        "title": "Etiqueta Social Española",
        "content": "En España, la gente es abierta y conversadora. Dos besos (empezando por la derecha) son la norma de saludo. Llegar con media hora de retraso a una cena social es normal. La comida y el vino son formas de mostrar cariño. Hablar en voz alta no significa enfado; es solo entusiasmo. Las sobremesas (conversaciones después de comer) pueden durar horas. El flamenco, la paella y las fiestas locales reflejan la pasión por la vida. Los domingos en familia alrededor de una paella o un cocido son tradición.",
        "category": "other",
        "tags": ["social", "etiquette", "spain", "culture"],
        "source": "culture_db",
        "language": "es",
    },
    # 印尼文化
    {
        "title": "Budaya Pacaran di Indonesia",
        "content": "Di Indonesia, WhatsApp dan Instagram adalah alat komunikasi utama dalam hubungan. Orang Indonesia cenderung sopan dan tidak terlalu langsung dalam mengungkapkan perasaan di awal hubungan. 'Kamu sudah makan?' adalah cara umum menunjukkan perhatian. Kencan pertama biasanya di kafe atau mall. Budaya 'halal dating' (pacaran tanpa kontak fisik berlebihan) masih umum di kalangan yang religius. Angkringan, warteg, dan kaki lima adalah pilihan makan yang populer dan merakyat. Lebaran dan Idul Fitri adalah momen penting untuk berkunjung ke keluarga.",
        "category": "other",
        "tags": ["dating", "communication", "indonesia", "culture"],
        "source": "culture_db",
        "language": "id",
    },
    {
        "title": "Etika Sosial Indonesia",
        "content": "Masyarakat Indonesia terkenal dengan keramahan dan kehangatannya. Salam dengan berjabat tangan ringan atau anggukan sopan. Basa-basi adalah bagian penting dari percakapan sebelum masuk ke topik utama. Makan bersama (makan bareng) adalah cara utama membangun keakraban. Nasi adalah makanan pokok yang hampir selalu ada di setiap waktu makan. Hormat kepada yang lebih tua sangat diutamakan. Gotong royong (kerja sama) adalah nilai dasar masyarakat. Ngobrol di angkringan sambil minum kopi tubruk adalah gaya hidup yang sangat Indonesia.",
        "category": "other",
        "tags": ["social", "etiquette", "indonesia", "culture"],
        "source": "culture_db",
        "language": "id",
    },
]


# ===== 人设生成 Prompt 文化上下文 =====
_CULTURAL_CONTEXTS = {
    "zh": """文化背景指令：
- 这是一个生活在中国大都市的年轻人，日常沟通主要使用微信
- 社交习惯：含蓄内敛，注重细节关心，节日仪式感强
- 恋爱观：重视陪伴和实际行动，期待细水长流的感情
- 常用表达："在干嘛"="我想你"，分享日常=建立亲密感
- 生活元素：奶茶、火锅、共享单车、地铁通勤、外卖、短视频""",
    "en": """Cultural context:
- This is a young person living in a Western city, communicating primarily through texting and social media
- Social habits: Values direct communication but also personal space, enjoys casual dates like coffee or drinks
- Love language: Words of affirmation and quality time are common; 'I miss you' and 'I like you' are said directly
- Lifestyle elements: Brunch, craft beer, gym routines, Netflix, road trips, podcasts, farmers markets""",
    "ja": """文化背景指示：
- 日本の都市に住む若者で、主にLINEでコミュニケーションを取る
- 社交習慣：遠慮と察しが大事、直接的な感情表現は控えめだが、スタンプや細かい気遣いで伝える
- 恋愛観：告白文化が重要、記念日を大切にし、手作りプレゼントに心を込める
- 生活要素：コンビニ、電車通勤、カフェ、駅前商業施設、アニメ・漫画、祭り""",
    "ko": """문화 배경 지시：
- 한국의 대도시에 사는 젊은이로, 주로 카카오톡으로 소통한다
- 사교 습관：'썸' 문화가 발달해 있으며, 애교와 스킨십이 중요하다. 빠른 답장이 상대방에 대한 관심을 나타낸다
- 연애관：기념일을 매우 중시하고, 커플템을 즐긴다. 직접적인 표현보다는行動으로 보여주는 것을 선호하기도 한다
- 생활 요소：카페, 편의점, 지하철, 배달 음식, K-뷰티, 노래방, 치맥""",
    "pt": """Instrução de contexto cultural:
- Jovem que vive em uma cidade brasileira, usa WhatsApp como principal forma de comunicação
- Hábitos sociais: Caloroso, demonstrativo, manda áudios longos, adora figurinhas e memes. 'Saudade' é palavra do dia a dia
- Visão de amor: Apaixonado, gosta de demonstrar carinho publicamente, valoriza encontros em praias e churrascos de família
- Elementos de vida: Açaí, pão de queijo, futebol, praia, churrasco, samba, ônibus lotado, cerveja gelada""",
    "es": """Instrucción de contexto cultural:
- Joven que vive en una ciudad española o latina, comunicación intensa y apasionada
- Hábitos sociales: Directo, expresivo, le encanta la sobremesa, las tapas y el vermut. La familia es muy importante
- Visión del amor: Apasionado, intenso, valora los detalles románticos y las conversaciones profundas hasta la madrugada
- Elementos de vida: Tapas, paella, flamenco, siesta, fiestas de pueblo, vino, café, paseos por la plaza""",
    "id": """Instruksi konteks budaya:
- Pemuda yang tinggal di kota besar Indonesia, berkomunikasi terutama melalui WhatsApp dan Instagram
- Kebiasaan sosial: Ramah, sopan, tidak terlalu langsung dalam ungkap perasaan. 'Sudah makan?' adalah cara menunjukkan perhatian
- Pandangan cinta: Menghargai kebersamaan dan kehangatan keluarga, kencan santai di kafe atau mall
- Elemen kehidupan: Nasi, kopi tubruk, angkringan, kaki lima, ojek online, mall, Lebaran, gotong royong""",
    "ar": """سياق ثقافي:
- شاب/شابة يعيش في مدينة عربية كبرى، التواصل اليومي غالباً عبر واتساب وتيليغرام والرسائل الصوتية
- العادات الاجتماعية: احترام الأسرة والخصوصية في العلاقات؛ المزاح والكرم والضيافة أهم من التصريحات العلنية
- نظرة الحب: التعارف يبدأ بهدوء (قهوة، مول، سيران)، والجدية تظهر عند إشراك الأهل؛ الغيرة والحياء جزء من اللغة العاطفية
- عناصر الحياة: قهوة عربية، تمر، جمعات الجمعة، رمضان والعيد، أوبر/كريم، مولات، شيشة مع الأصدقاء""",
    "fa": """بافت فرهنگی:
- جوانی که در شهر بزرگ ایران زندگی می‌کند؛ تلگرام و اینستاگرام محور پیام‌رسانی روزانه‌اند (واتساپ هم هست اما تلگرام عادت غالب است)
- عادات اجتماعی: تعارف، رودربایستی با خانواده، شوخی خصوصی در گروه دوستان؛ احساسات در جمع رسمی کمتر رک است
- نگاه به عشق: قرارها اغلب محتاط و دور از چشم فامیل؛ قهوه و قدم‌زدن و گپ طولانی؛ جدی شدن یعنی معرفی به خانواده
- عناصر زندگی: چای، ترافیک، اسنپ، دورهمی خانگی، شعر و موسیقی، چهارشنبه‌سوری و عید نوروز، بازار و پاساژ""",
    "ru": """Культурный контекст:
- Молодой человек в крупном российском городе; повседневная связь — Telegram, иногда VK и WhatsApp, голосовые и мемы
- Социальные привычки: ирония и самоирония, тёплый круг «своих», личные темы не выносят сразу на публику
- Взгляд на любовь: сначала переписка и прогулка, цветы и знаки внимания ценятся; знакомство с родителями — серьёзный шаг
- Элементы жизни: метро, шаурма/кофе с собой, дача по выходным, Новый год важнее 14 февраля, сериалы, дворы и ЖК""",
    "bg": """Културен контекст:
- Млад човек в български град; WhatsApp, Viber и Facebook Messenger са ежедневието, плюс гласови и стикери
- Социални навици: топлина в тесен кръг, семейството тежи, кафето е ритуал, не бърза среща
- Поглед към любовта: запознанства през приятели или приложения; сериозността се вижда, когато представиш човека на родителите
- Елементи от живота: баница, кафе, море през лятото, планина през уикенда, имен ден, ракия на маса, градски транспорт""",
    "tr": """Kültürel bağlam:
- Büyük Türk şehrinde yaşayan genç; günlük iletişim WhatsApp ve Instagram, ses kayıtları ve sticker sıradan
- Sosyal alışkanlıklar: aileye saygı, misafirlik, espri ve samimiyet; duygular bazen dolaylı, yakınlara karşı sıcaktır
- Aşk bakışı: kahve/yürüyüşle başlayan tanışma, aileye anlatmak ciddiyet işaretidir; kıskançlık ve ilgi mesajları dilin parçası
- Yaşam öğeleri: çay, simit, dolmuş/metro, sahil veya sahur/iftar ritmi, ev yemeği, maç sohbeti, AVM ve sahil yürüyüşü""",
}


def get_cultural_context(lang: str) -> str:
    lk = (lang or "en").split("-")[0].lower()
    return _CULTURAL_CONTEXTS.get(lk, _CULTURAL_CONTEXTS["en"])


# ——— 城市 → 主流文化/语言/意识形态锚点（生成与运行时共用）———
# 覆盖 CITIES_DB 主城市；未知城市回退到语言级主流文化
_CITY_LOCALE: dict[str, dict[str, str]] = {
    # zh
    "北京": {"lang": "zh", "region": "中国华北/首都圈", "language": "普通话（北京口语可轻可重）", "ideology": "重视秩序与面子、家庭期待与个人奋斗并存；社交偏含蓄，关系推进看长期承诺与实际行动。", "everyday": "地铁通勤、体制/互联网混搭节奏、胡同与写字楼、节假日仪式感强。"},
    "上海": {"lang": "zh", "region": "中国长三角", "language": "普通话为主，偶有沪语氛围", "ideology": "务实精明、边界感与效率并重；看重自我提升与生活品质，关系里讲究体面与对等付出。", "everyday": "写字楼加班、咖啡厅约会、外卖与地铁、精致日常。"},
    "成都": {"lang": "zh", "region": "中国西南", "language": "普通话带川渝语感", "ideology": "松弛感与人情味；乐天务实，重视朋友圈与烟火气，不爱硬卷口号。", "everyday": "火锅、茶馆、夜市、慢节奏社交。"},
    "广州": {"lang": "zh", "region": "中国珠三角", "language": "普通话/粤语双语氛围", "ideology": "务实经商、家庭与口腹之欲并重；感情表达偏行动派，少空谈。", "everyday": "早茶、城际通勤、市井与现代并存。"},
    "深圳": {"lang": "zh", "region": "中国珠三角移民城市", "language": "普通话为主的移民普通话", "ideology": "奋斗与流动感强；更开放多元，看重能力与结果，关系里也带效率与坦诚。", "everyday": "科技园、加班、城中村到高楼的对照。"},
    "杭州": {"lang": "zh", "region": "中国长三角", "language": "普通话", "ideology": "互联网与传统江南气质交织；重视审美、体面与温和进取。", "everyday": "互联网公司、西湖周边、咖啡与外卖。"},
    "武汉": {"lang": "zh", "region": "中国华中", "language": "普通话带武汉语感", "ideology": "直爽热络、抗压强；看重义气与实在，不喜欢虚伪客套。", "everyday": "过江通勤、热干面、夜生活与市井。"},
    "西安": {"lang": "zh", "region": "中国西北", "language": "普通话带陕西语感", "ideology": "厚重与务实并存；家庭 ethnos 感强，重情义，对传统与现代都能容纳。", "everyday": "古城与新区、面食、夜市。"},
    "南京": {"lang": "zh", "region": "中国长三角", "language": "普通话", "ideology": "文气与稳重；重历史感与体面，关系推进偏细水长流。", "everyday": "高校氛围、城际通勤、街头美食。"},
    "重庆": {"lang": "zh", "region": "中国西南", "language": "普通话带重庆语感", "ideology": "火辣直接、重江湖义气；情感表达更外放，讨厌拧巴。", "everyday": "山城通勤、火锅、夜景与加班。"},
    # en
    "New York": {"lang": "en", "region": "USA Northeast", "language": "American English (NYC pace)", "ideology": "Individual ambition, direct talk, diversity as default; relationships prize honesty and personal space.", "everyday": "subway, walk-ups, late nights, coffee-to-go."},
    "Los Angeles": {"lang": "en", "region": "USA West Coast", "language": "American English (SoCal casual)", "ideology": "Optimistic self-branding, wellness and hustle mix; dating can be chill yet image-aware.", "everyday": "cars, brunch, gym, industry freelancers."},
    "London": {"lang": "en", "region": "UK", "language": "British English", "ideology": "Understatement, dry humor, politeness with reserve; affection via banter more than grand declarations.", "everyday": "Tube, pubs, rainy walks, Sunday roast vibe."},
    "San Francisco": {"lang": "en", "region": "USA Bay Area", "language": "American English (tech-casual)", "ideology": "Progressive, product/idea oriented, values authenticity and boundaries; pragmatism around work-life.", "everyday": "tech offices, transit/Uber, coffee, fog."},
    "Seattle": {"lang": "en", "region": "USA Pacific Northwest", "language": "American English", "ideology": "Polite reserve, outdoorsy independence, low-key sincerity over flash.", "everyday": "rain, coffee, hiking weekends, tech/campus life."},
    "Chicago": {"lang": "en", "region": "USA Midwest", "language": "American English", "ideology": "Straightforward, loyal, community-minded; less performative than coastal scenes.", "everyday": "neighborhoods, winters, sports talk, diners."},
    "Boston": {"lang": "en", "region": "USA Northeast", "language": "American English", "ideology": "Education-proud, witty, a bit competitive; values competence and loyalty.", "everyday": "universities, walkable streets, seasons."},
    "Austin": {"lang": "en", "region": "USA Texas", "language": "American English (Texas-casual)", "ideology": "Laid-back creativity + entrepreneurial streak; friendly directness.", "everyday": "live music, tacos, heat, startups."},
    "Melbourne": {"lang": "en", "region": "Australia", "language": "Australian English", "ideology": "Egalitarian mateship, dry humor, work-life balance; dating often casual-first.", "everyday": "cafés, tram, footy, laneways."},
    "Toronto": {"lang": "en", "region": "Canada", "language": "Canadian English", "ideology": "Polite multiculturalism, fairness, conflict-averse warmth; values inclusion and stability.", "everyday": "TTC, condos, seasons, diverse food."},
    # ja
    "東京": {"lang": "ja", "region": "日本首都圏", "language": "日本語（標準〜軽い東京弁）", "ideology": "遠慮・察し・空気読み；公私の距離を大切にし、告白・段階を重んじる。", "everyday": "電車通勤、コンビニ、カフェ、繁華街。"},
    "大阪": {"lang": "ja", "region": "関西", "language": "日本語（関西弁気質）", "ideology": "明るく商売っ気と人情；冗談と本音が近いが、礼儀は欠かさない。", "everyday": "食い倒れ、電車、商店街。"},
    "京都": {"lang": "ja", "region": "関西", "language": "日本語（丁寧寄り）", "ideology": "伝統と婉曲表現；表面の丁寧さの奥に本音。性急な踏み込みを嫌う。", "everyday": "寺社、観光と生活の同居、季節行事。"},
    "札幌": {"lang": "ja", "region": "北海道", "language": "日本語", "ideology": "実直・距離感ある優しさ；自然と季節に根ざした価値観。", "everyday": "雪、ビール、広い街並み。"},
    "福岡": {"lang": "ja", "region": "九州", "language": "日本語（九州気質）", "ideology": "親しみやすく開放的；食と地元愛が強い。", "everyday": "屋台、地下鉄、海近い日常。"},
    "名古屋": {"lang": "ja", "region": "中部", "language": "日本語", "ideology": "堅実・実利；派手さより積み上げ。", "everyday": "ものづくり、鉄道、地元メシ。"},
    "横浜": {"lang": "ja", "region": "首都圏", "language": "日本語", "ideology": "都会的で国際色；丁寧だが東京より少しゆったり。", "everyday": "港、みなとみらい、通勤。"},
    "神戸": {"lang": "ja", "region": "関西", "language": "日本語", "ideology": "洒落っ気と上品さ；異国情緒と礼儀。", "everyday": "港町、スイーツ、坂道。"},
    "仙台": {"lang": "ja", "region": "東北", "language": "日本語", "ideology": "控えめ誠実；派手な自己主張より継続の信頼。", "everyday": "学都、杜の都、季節の明確さ。"},
    "広島": {"lang": "ja", "region": "中国地方", "language": "日本語", "ideology": "実直・地元愛；平和と生活実感を重んじる。", "everyday": "路面電車、お好み焼き、瀬戸内。"},
    # ko
    "서울": {"lang": "ko", "region": "한국 수도권", "language": "한국어（서울 표준）", "ideology": "성취·속도·체면과 애정표현이 공존；기념일·응답속도·관계 단계에 민감.", "everyday": "지하철, 카페, 배달, 야근."},
    "부산": {"lang": "ko", "region": "영남", "language": "한국어（부산 기질）", "ideology": "직설·정 많음；허세보다 실속과 의리.", "everyday": "바다, 시장, 사투리 톤."},
    "인천": {"lang": "ko", "region": "수도권", "language": "한국어", "ideology": "실용·다양성；공항·무역 감각의 개방성.", "everyday": "교통 허브, 항구 도시 리듬."},
    "대구": {"lang": "ko", "region": "영남", "language": "한국어", "ideology": "보수와 열정이 섞인 직정；관계에서 분명한 호불호.", "everyday": "내륙 도시, 먹거리, 더위."},
    "광주": {"lang": "ko", "region": "호남", "language": "한국어", "ideology": "정의감·공동체 의식；감정 표현이 진한 편.", "everyday": "예술·민주화 기억과 일상 공존."},
    "대전": {"lang": "ko", "region": "충청", "language": "한국어", "ideology": "차분·실무형；과학도시 분위기의 이성적 태도.", "everyday": "연구단지, 교통 요지."},
    "울산": {"lang": "ko", "region": "영남", "language": "한국어", "ideology": "산업도시 실무주의；성실과 안정 중시.", "everyday": "공장·항만 리듬."},
    "제주": {"lang": "ko", "region": "제주", "language": "한국어", "ideology": "여유·자연친화；육지 속도에 거리를 둠.", "everyday": "관광과 로컬의 이중 리듬."},
    "수원": {"lang": "ko", "region": "수도권", "language": "한국어", "ideology": "서울 인접 실속형；가족·직장 균형.", "everyday": "출퇴근, 신도시 생활."},
    "창원": {"lang": "ko", "region": "영남", "language": "한국어", "ideology": "계획도시 실용주의；안정적 관계 선호.", "everyday": "공업·주거 혼합."},
    # pt (Brazil)
    "São Paulo": {"lang": "pt", "region": "Brasil Sudeste", "language": "português brasileiro", "ideology": "Ambicioso, rápido, diversificado; valoriza conquista e calor humano no privado.", "everyday": "metrô, trânsito, coworking, boteco."},
    "Rio de Janeiro": {"lang": "pt", "region": "Brasil Sudeste", "language": "português brasileiro (carioca)", "ideology": "Caloroso, corporal, presente; celebra alegria e vínculos sociais públicos.", "everyday": "praia, samba, morro/asfalto, WhatsApp áudios."},
    "Salvador": {"lang": "pt", "region": "Brasil Nordeste", "language": "português brasileiro", "ideology": "Afeto, fé e festa; comunidade e ancestralidade importam.", "everyday": "axé, praia, família ampliada."},
    "Brasília": {"lang": "pt", "region": "Brasil Centro-Oeste", "language": "português brasileiro", "ideology": "Mais formal/planejado; mistura serviço público e vida de cidade nova.", "everyday": "eixos, carros, fins de semana."},
    "Belo Horizonte": {"lang": "pt", "region": "Brasil Sudeste", "language": "português brasileiro", "ideology": "Mineiro reservado no começo, leal depois; comida e conversa longas.", "everyday": "boteco, pão de queijo, serra."},
    "Fortaleza": {"lang": "pt", "region": "Brasil Nordeste", "language": "português brasileiro", "ideology": "Acolhedor, solar, direto no afeto; valoriza presença.", "everyday": "praia, calor, família."},
    "Curitiba": {"lang": "pt", "region": "Brasil Sul", "language": "português brasileiro", "ideology": "Mais contido, ordem e planejamento; humor seco.", "everyday": "frio relativo, parques, rotina."},
    "Porto Alegre": {"lang": "pt", "region": "Brasil Sul", "language": "português brasileiro", "ideology": "Opinião forte, chimarrão e debate; lealdade de grupo.", "everyday": "churrasco, frio, política cotidiana."},
    "Recife": {"lang": "pt", "region": "Brasil Nordeste", "language": "português brasileiro", "ideology": "Criativo, afetuoso, resistência e festa juntas.", "everyday": "maracatu, praia, tecnologia local."},
    "Manaus": {"lang": "pt", "region": "Brasil Norte", "language": "português brasileiro", "ideology": "Orgulho amazônico, adaptação e calor humano; ritmo próprio.", "everyday": "rio, calor, comércio zonal."},
    # es
    "Madrid": {"lang": "es", "region": "España", "language": "español (castellano)", "ideology": "Directo, sociable, valora sobremesa y familia; pasión sin perder humor.", "everyday": "tapas, metro, noches largas."},
    "Barcelona": {"lang": "es", "region": "España Cataluña", "language": "español / catalán ambiente", "ideology": "Abierta, creativa, independencia cultural; mix cosmopolita.", "everyday": "playa-ciudad, terrazas, diseño."},
    "México City": {"lang": "es", "region": "México", "language": "español mexicano", "ideology": "Cálido, ingenioso, familia amplia; resiliencia y humor ante el caos urbano.", "everyday": "metro, antojitos, tráfico, fiestas."},
    "Buenos Aires": {"lang": "es", "region": "Argentina", "language": "español rioplatense", "ideology": "Intelectual, apasionado, opinado; amistad intensa y ironía.", "everyday": "café, tango vibe, protesta y charla."},
    "Lima": {"lang": "es", "region": "Perú", "language": "español peruano", "ideology": "Cortés, comida como afecto, mezcla tradición y modernidad.", "everyday": "ceiche, niebla, familia."},
    "Bogotá": {"lang": "es", "region": "Colombia", "language": "español colombiano", "ideology": "Educado, trabajador, afectuoso con cercanos; paciencia urbana.", "everyday": "TransMilenio, frío de altura, cafés."},
    "Santiago": {"lang": "es", "region": "Chile", "language": "español chileno", "ideology": "Reservado al inicio, leal luego; humor propio y pragmatismo.", "everyday": "metro, cerros, once."},
    "Valencia": {"lang": "es", "region": "España", "language": "español", "ideology": "Mediterráneo, fiesta y comida; equilibrio vida-trabajo.", "everyday": "paella, playa, fallas vibe."},
    "Sevilla": {"lang": "es", "region": "España Andalucía", "language": "español andaluz", "ideology": "Cálido, ceremonial, orgullo local; emotividad abierta.", "everyday": "tapas, calor, feria."},
    "Guadalajara": {"lang": "es", "region": "México", "language": "español mexicano", "ideology": "Tradición y calidez; familia y música como ejes.", "everyday": "mariachi, mercados, barrios."},
    # id
    "Jakarta": {"lang": "id", "region": "Indonesia", "language": "Bahasa Indonesia", "ideology": "Sopan, hierarki usia, gotong royong; ambisi kota besar + hormat keluarga.", "everyday": "macet, ojol, mall, WhatsApp."},
    "Surabaya": {"lang": "id", "region": "Jawa Timur", "language": "Bahasa Indonesia", "ideology": "Tegas, praktis, setia; kurang basa-basi kosong.", "everyday": "dagangan, pantai dekat, kerja keras."},
    "Bandung": {"lang": "id", "region": "Jawa Barat", "language": "Bahasa Indonesia", "ideology": "Kreatif, santai-hangat; komunitas dan kopi.", "everyday": "kuliner, sejuk, kampus."},
    "Medan": {"lang": "id", "region": "Sumatra", "language": "Bahasa Indonesia", "ideology": "Blak-blakan, kuat keluarga etnis-mix; loyalitas tinggi.", "everyday": "makanan kaya rasa, kota sibuk."},
    "Makassar": {"lang": "id", "region": "Sulawesi", "language": "Bahasa Indonesia", "ideology": "Bangga lokal, tegas tapi hangat; laut sebagai identitas.", "everyday": "pantai, kuliner laut."},
    "Yogyakarta": {"lang": "id", "region": "Jawa", "language": "Bahasa Indonesia", "ideology": "Halus, budaya-adiluhung, hormat; pacaran sering lebih sopan.", "everyday": "kampus, malioboro, malam angkringan."},
    "Semarang": {"lang": "id", "region": "Jawa Tengah", "language": "Bahasa Indonesia", "ideology": "Tenang, ramah, nilai kekeluargaan.", "everyday": "kota pesisir, kuliner."},
    "Bali": {"lang": "id", "region": "Bali", "language": "Bahasa Indonesia", "ideology": "Spiritual-harmoni, terbuka pada wisatawan tapi jaga adat; hidup seimbang.", "everyday": "pura, pantai, hospitality."},
    "Palembang": {"lang": "id", "region": "Sumatra", "language": "Bahasa Indonesia", "ideology": "Hangat, bangga kuliner/sungai; kekeluargaan.", "everyday": "pempek, sungai Musi."},
    "Malang": {"lang": "id", "region": "Jawa Timur", "language": "Bahasa Indonesia", "ideology": "Santai-edukatif, komunitas muda; ramah tanpa lebay.", "everyday": "kampus, sejuk, wisata dekat."},
    # auto-expanded city stubs
    "天津": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "天津的通勤与市井节奏、外卖与社交平台日常。",},
    "苏州": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "苏州的通勤与市井节奏、外卖与社交平台日常。",},
    "长沙": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "长沙的通勤与市井节奏、外卖与社交平台日常。",},
    "郑州": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "郑州的通勤与市井节奏、外卖与社交平台日常。",},
    "青岛": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "青岛的通勤与市井节奏、外卖与社交平台日常。",},
    "大连": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "大连的通勤与市井节奏、外卖与社交平台日常。",},
    "厦门": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "厦门的通勤与市井节奏、外卖与社交平台日常。",},
    "福州": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "福州的通勤与市井节奏、外卖与社交平台日常。",},
    "合肥": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "合肥的通勤与市井节奏、外卖与社交平台日常。",},
    "昆明": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "昆明的通勤与市井节奏、外卖与社交平台日常。",},
    "贵阳": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "贵阳的通勤与市井节奏、外卖与社交平台日常。",},
    "南宁": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "南宁的通勤与市井节奏、外卖与社交平台日常。",},
    "南昌": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "南昌的通勤与市井节奏、外卖与社交平台日常。",},
    "哈尔滨": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "哈尔滨的通勤与市井节奏、外卖与社交平台日常。",},
    "长春": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "长春的通勤与市井节奏、外卖与社交平台日常。",},
    "沈阳": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "沈阳的通勤与市井节奏、外卖与社交平台日常。",},
    "石家庄": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "石家庄的通勤与市井节奏、外卖与社交平台日常。",},
    "太原": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "太原的通勤与市井节奏、外卖与社交平台日常。",},
    "兰州": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "兰州的通勤与市井节奏、外卖与社交平台日常。",},
    "银川": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "银川的通勤与市井节奏、外卖与社交平台日常。",},
    "乌鲁木齐": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "乌鲁木齐的通勤与市井节奏、外卖与社交平台日常。",},
    "海口": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "海口的通勤与市井节奏、外卖与社交平台日常。",},
    "三亚": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "三亚的通勤与市井节奏、外卖与社交平台日常。",},
    "香港": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "香港的通勤与市井节奏、外卖与社交平台日常。",},
    "台北": {"lang": "zh", "region": "中国都市", "language": "普通话", "ideology": "务实含蓄、家庭与个人奋斗并存；关系推进看行动与长期承诺。", "everyday": "台北的通勤与市井节奏、外卖与社交平台日常。",},
    "Vancouver": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Vancouver; coffee, weekends, work hustle.",},
    "Sydney": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Sydney; coffee, weekends, work hustle.",},
    "Manchester": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Manchester; coffee, weekends, work hustle.",},
    "Dublin": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Dublin; coffee, weekends, work hustle.",},
    "Edinburgh": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Edinburgh; coffee, weekends, work hustle.",},
    "Portland": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Portland; coffee, weekends, work hustle.",},
    "Denver": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Denver; coffee, weekends, work hustle.",},
    "Miami": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Miami; coffee, weekends, work hustle.",},
    "Atlanta": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Atlanta; coffee, weekends, work hustle.",},
    "Philadelphia": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Philadelphia; coffee, weekends, work hustle.",},
    "Minneapolis": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Minneapolis; coffee, weekends, work hustle.",},
    "San Diego": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in San Diego; coffee, weekends, work hustle.",},
    "Houston": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Houston; coffee, weekends, work hustle.",},
    "Dallas": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Dallas; coffee, weekends, work hustle.",},
    "Singapore": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Singapore; coffee, weekends, work hustle.",},
    "Auckland": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Auckland; coffee, weekends, work hustle.",},
    "Cape Town": {"lang": "en", "region": "English-speaking urban", "language": "English", "ideology": "Directness, personal space, negotiated intimacy; honesty over performance.", "everyday": "Local transit and neighborhood life in Cape Town; coffee, weekends, work hustle.",},
    "千葉": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "千葉の電車・コンビニ・地元の食と季節感。",},
    "埼玉": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "埼玉の電車・コンビニ・地元の食と季節感。",},
    "静岡": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "静岡の電車・コンビニ・地元の食と季節感。",},
    "金沢": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "金沢の電車・コンビニ・地元の食と季節感。",},
    "新潟": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "新潟の電車・コンビニ・地元の食と季節感。",},
    "岡山": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "岡山の電車・コンビニ・地元の食と季節感。",},
    "熊本": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "熊本の電車・コンビニ・地元の食と季節感。",},
    "鹿児島": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "鹿児島の電車・コンビニ・地元の食と季節感。",},
    "那覇": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "那覇の電車・コンビニ・地元の食と季節感。",},
    "松山": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "松山の電車・コンビニ・地元の食と季節感。",},
    "高松": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "高松の電車・コンビニ・地元の食と季節感。",},
    "富山": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "富山の電車・コンビニ・地元の食と季節感。",},
    "長野": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "長野の電車・コンビニ・地元の食と季節感。",},
    "宇都宮": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "宇都宮の電車・コンビニ・地元の食と季節感。",},
    "岐阜": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "岐阜の電車・コンビニ・地元の食と季節感。",},
    "奈良": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "奈良の電車・コンビニ・地元の食と季節感。",},
    "和歌山": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "和歌山の電車・コンビニ・地元の食と季節感。",},
    "青森": {"lang": "ja", "region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的な関係；礼儀と本音の距離感。", "everyday": "青森の電車・コンビニ・地元の食と季節感。",},
    "성남": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "성남의 카페·배달·출퇴근 리듬.",},
    "고양": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "고양의 카페·배달·출퇴근 리듬.",},
    "용인": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "용인의 카페·배달·출퇴근 리듬.",},
    "청주": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "청주의 카페·배달·출퇴근 리듬.",},
    "전주": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "전주의 카페·배달·출퇴근 리듬.",},
    "천안": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "천안의 카페·배달·출퇴근 리듬.",},
    "포항": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "포항의 카페·배달·출퇴근 리듬.",},
    "안산": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "안산의 카페·배달·출퇴근 리듬.",},
    "부천": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "부천의 카페·배달·출퇴근 리듬.",},
    "남양주": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "남양주의 카페·배달·출퇴근 리듬.",},
    "화성": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "화성의 카페·배달·출퇴근 리듬.",},
    "평택": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "평택의 카페·배달·출퇴근 리듬.",},
    "김해": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "김해의 카페·배달·출퇴근 리듬.",},
    "진주": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "진주의 카페·배달·출퇴근 리듬.",},
    "원주": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "원주의 카페·배달·출퇴근 리듬.",},
    "춘천": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "춘천의 카페·배달·출퇴근 리듬.",},
    "강릉": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "강릉의 카페·배달·출퇴근 리듬.",},
    "여수": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "여수의 카페·배달·출퇴근 리듬.",},
    "순천": {"lang": "ko", "region": "한국 도시", "language": "한국어", "ideology": "속도·체면과 애정표현 공존；관계 단계와 응답에 민감.", "everyday": "순천의 카페·배달·출퇴근 리듬.",},
    "Belém": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Belém: comida, encontros, WhatsApp.",},
    "Goiânia": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Goiânia: comida, encontros, WhatsApp.",},
    "Campinas": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Campinas: comida, encontros, WhatsApp.",},
    "São Luís": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de São Luís: comida, encontros, WhatsApp.",},
    "Maceió": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Maceió: comida, encontros, WhatsApp.",},
    "Natal": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Natal: comida, encontros, WhatsApp.",},
    "Teresina": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Teresina: comida, encontros, WhatsApp.",},
    "João Pessoa": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de João Pessoa: comida, encontros, WhatsApp.",},
    "Florianópolis": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Florianópolis: comida, encontros, WhatsApp.",},
    "Vitória": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Vitória: comida, encontros, WhatsApp.",},
    "Santos": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Santos: comida, encontros, WhatsApp.",},
    "Ribeirão Preto": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Ribeirão Preto: comida, encontros, WhatsApp.",},
    "Uberlândia": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Uberlândia: comida, encontros, WhatsApp.",},
    "Lisboa": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Lisboa: comida, encontros, WhatsApp.",},
    "Porto": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Porto: comida, encontros, WhatsApp.",},
    "Coimbra": {"lang": "pt", "region": "mundo lusófono urbano", "language": "português", "ideology": "Calor afetivo, presença e família; celebração e lealdade.", "everyday": "Ritmo urbano de Coimbra: comida, encontros, WhatsApp.",},
    "Monterrey": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Monterrey: café, comida, calles y familia.",},
    "Medellín": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Medellín: café, comida, calles y familia.",},
    "Quito": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Quito: café, comida, calles y familia.",},
    "Caracas": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Caracas: café, comida, calles y familia.",},
    "Montevideo": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Montevideo: café, comida, calles y familia.",},
    "Asunción": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Asunción: café, comida, calles y familia.",},
    "La Paz": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en La Paz: café, comida, calles y familia.",},
    "Córdoba": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Córdoba: café, comida, calles y familia.",},
    "Rosario": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Rosario: café, comida, calles y familia.",},
    "Málaga": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Málaga: café, comida, calles y familia.",},
    "Bilbao": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Bilbao: café, comida, calles y familia.",},
    "Zaragoza": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Zaragoza: café, comida, calles y familia.",},
    "Murcia": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Murcia: café, comida, calles y familia.",},
    "Palma": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Palma: café, comida, calles y familia.",},
    "Granada": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Granada: café, comida, calles y familia.",},
    "Puebla": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Puebla: café, comida, calles y familia.",},
    "Tijuana": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Tijuana: café, comida, calles y familia.",},
    "Cancún": {"lang": "es", "region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia y sobremesa; pasión con humor local.", "everyday": "Vida cotidiana en Cancún: café, comida, calles y familia.",},
    "Balikpapan": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Balikpapan: ojol, kuliner, WhatsApp, keluarga.",},
    "Pontianak": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Pontianak: ojol, kuliner, WhatsApp, keluarga.",},
    "Manado": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Manado: ojol, kuliner, WhatsApp, keluarga.",},
    "Padang": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Padang: ojol, kuliner, WhatsApp, keluarga.",},
    "Pekanbaru": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Pekanbaru: ojol, kuliner, WhatsApp, keluarga.",},
    "Bandar Lampung": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Bandar Lampung: ojol, kuliner, WhatsApp, keluarga.",},
    "Denpasar": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Denpasar: ojol, kuliner, WhatsApp, keluarga.",},
    "Bogor": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Bogor: ojol, kuliner, WhatsApp, keluarga.",},
    "Depok": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Depok: ojol, kuliner, WhatsApp, keluarga.",},
    "Tangerang": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Tangerang: ojol, kuliner, WhatsApp, keluarga.",},
    "Bekasi": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Bekasi: ojol, kuliner, WhatsApp, keluarga.",},
    "Solo": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Solo: ojol, kuliner, WhatsApp, keluarga.",},
    "Cirebon": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Cirebon: ojol, kuliner, WhatsApp, keluarga.",},
    "Jambi": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Jambi: ojol, kuliner, WhatsApp, keluarga.",},
    "Ambon": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Ambon: ojol, kuliner, WhatsApp, keluarga.",},
    "Kupang": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Kupang: ojol, kuliner, WhatsApp, keluarga.",},
    "Mataram": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Mataram: ojol, kuliner, WhatsApp, keluarga.",},
    "Banjarmasin": {"lang": "id", "region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan, hormat, gotong royong; ungkapan sering tidak langsung.", "everyday": "Ritme Banjarmasin: ojol, kuliner, WhatsApp, keluarga.",},
    # ar
    "الرياض": {"lang": "ar", "region": "السعودية / نجد", "language": "العربية (لهجة نجدية خفيفة ممكنة)", "ideology": "محافظة عملية، كرامة الأسرة والخصوصية أهم من العرض العلني؛ العلاقات الجدية تمر عبر احترام الأهل والحدود.", "everyday": "حرّ، مولات مكيفة، قهوة عربية، واتساب صوتي، كريم في الزحمة."},
    "جدة": {"lang": "ar", "region": "السعودية / الحجاز", "language": "العربية (نبرة حجازية ممكنة)", "ideology": "أكثر انفتاحاً بحرياً وتجارياً؛ كرم وضيافة، ومزج تقاليد الحجاز مع إيقاع المدينة.", "everyday": "كورنيش البحر الأحمر، كافيهات، زحمة، جمعات رمضان والعيد."},
    "دبي": {"lang": "ar", "region": "الإمارات", "language": "العربية مع إنجليزية يومية في الشارع", "ideology": "طموح عالمي وحدود مهنية واضحة؛ العلاقات سريعة الإيقاع لكن السمعة والعائلة تبقى حاضرة.", "everyday": "مترو وتاكسي، ناطحات، برانش وبحر، واتساب على مدار الساعة."},
    "أبوظبي": {"lang": "ar", "region": "الإمارات", "language": "العربية", "ideology": "أهدأ وأكثر رسمية من دبي؛ احترام المؤسسات والعائلة، والطموح بلا استعراض زائد.", "everyday": "كورنيش، دوائر حكومية، قهوة، عطلة نهاية أسبوع عائلية."},
    "القاهرة": {"lang": "ar", "region": "مصر", "language": "العربية (مصري دارج في الحوار اليومي)", "ideology": "خفة دم وصبر على الزحمة؛ الأسرة كبيرة والصداقة حميمة؛ الجدية العاطفية تُقاس بالحضور لا بالشعارات.", "everyday": "مترو وميكروباص، شاي بالنعناع، كشري، نيل، سهر واتساب."},
    "الدار البيضاء": {"lang": "ar", "region": "المغرب", "language": "العربية (دارجة مغربية في اليومي)", "ideology": "مزيج متوسطي وأطلسي؛ عملية، أنيقة في المدينة، والعائلة حاضرة حتى مع إيقاع العمل.", "everyday": "ترامواي، مقاهي، كاسكروط، كورنيش عين الذئاب، زحمة الدار البيضاء."},
    "عمّان": {"lang": "ar", "region": "الأردن", "language": "العربية (لهجة شامية)", "ideology": "ضيافة شامية وواقع حضري؛ علاقات هادئة، احترام الأهل، ومزاح دافئ في الدائرة القريبة.", "everyday": "صعود الجبال، مقاهي جبل عمّان/الويبدة، شاي مر، أوبر، جمعات العيلة."},
    "الدوحة": {"lang": "ar", "region": "قطر", "language": "العربية", "ideology": "مجتمع صغير نسبياً وسمعة مهمة؛ هدوء خليجي، كرم، وحدود واضحة بين العام والخاص.", "everyday": "كورنيش، مولات، قهوة، حرّ، تجمعات عائلية في العيد."},
    # fa
    "تهران": {"lang": "fa", "region": "ایران / پایتخت", "language": "فارسی تهرانی", "ideology": "جاه‌طلبی شهری با تعارف و رودربایستی؛ حریم خصوصی مهم است، صمیمیت در حلقه خودی‌ها باز می‌شود.", "everyday": "ترافیک، مترو و اسنپ، کافه، دورهمی خانه، تلگرام و اینستاگرام."},
    "اصفهان": {"lang": "fa", "region": "ایران مرکزی", "language": "فارسی", "ideology": "افتخار به تاریخ و ظرافت؛ مهمان‌نوازی و خانواده، کمتر شتابِ پایتخت.", "everyday": "زاینده‌رود و میدان نقش جهان، چای، بازار، جمعه‌های خانوادگی."},
    "مشهد": {"lang": "fa", "region": "ایران شرقی", "language": "فارسی", "ideology": "ایمان و خانواده پررنگ‌تر؛ احترام به سنت در کنار زندگی شهری.", "everyday": "حرم و زائر، چای، ترافیک زیارتی، دورهمی فامیل."},
    "شیراز": {"lang": "fa", "region": "ایران فارس", "language": "فارسی", "ideology": "شعر، باغ و خونگرمی؛ احساسات لطیف‌تر بیان می‌شود، تعارف زنده است.", "everyday": "حافظیه و باغ ارم، فالوده، گپ شبانه، بهار و عید."},
    "تبریز": {"lang": "fa", "region": "آذربایجان شرقی", "language": "فارسی با فضای ترکی آذری", "ideology": "رک‌گویی نسبی، غیرت و کار؛ وفاداری به جمع خودی.", "everyday": "بازار تبریز، کوفته و چای، زمستان سرد، خانواده گسترده."},
    "کرج": {"lang": "fa", "region": "البرز / حومه تهران", "language": "فارسی", "ideology": "ریتم خوابگاهی-کاریِ پایتخت؛ عملی، صرفه‌جو، روابط در رفت‌وآمد شکل می‌گیرد.", "everyday": "رفت‌وآمد تهران-کرج، آپارتمان، اسنپ، کافه محلی."},
    "اهواز": {"lang": "fa", "region": "خوزستان", "language": "فارسی", "ideology": "گرما و صراحت جنوب؛ تعارف با شوخی بلند، خانواده نزدیک.", "everyday": "گرمای شدید، رود کارون، دورهمی عصرگاهی وقتی هوا نرم می‌شود."},
    "قم": {"lang": "fa", "region": "ایران مرکزی", "language": "فارسی", "ideology": "محافظه‌کارتر و خانواده‌محور؛ حریم و آبرو وزن دارند.", "everyday": "حرم و حوزه در پس‌زمینه شهر، چای، دیدار فامیل، زندگی آرام‌تر از تهران."},
    # ru
    "Москва": {"lang": "ru", "region": "Россия / столица", "language": "русский (столичный темп)", "ideology": "Амбиции, скорость, дистанция в метро и тепло в своём кругу; отношения ценят честность и время.", "everyday": "метро, кофе с собой, работа/учёба, Telegram, дворы и ЖК."},
    "Санкт-Петербург": {"lang": "ru", "region": "Россия / Северо-Запад", "language": "русский", "ideology": "Ирония, культура, чуть больше меланхолии; ценят разговор и эстетику, не любит показуху.", "everyday": "дожди, парадные, кофейни, белые ночи, прогулки вдоль Невы."},
    "Новосибирск": {"lang": "ru", "region": "Россия / Сибирь", "language": "русский", "ideology": "Прямодушие, учёба и наука рядом с бытом; меньше столичного лоска, больше опоры на своих.", "everyday": "мороз, метро, общежития/кампус, шаурма, дача летом."},
    "Екатеринбург": {"lang": "ru", "region": "Россия / Урал", "language": "русский", "ideology": "Уральская прямота и рабочая гордость; дружба проверяется делом.", "everyday": "промзоны и бары, холод, Telegram-чаты, выходные за городом."},
    "Казань": {"lang": "ru", "region": "Россия / Татарстан", "language": "русский (татарский колорит вокруг)", "ideology": "Смешение культур, гостеприимство, уважение к семье и праздникам.", "everyday": "кремль и набережная, чак-чак, универсиада-ритм, семейные застолья."},
    "Нижний Новгород": {"lang": "ru", "region": "Россия / Волга", "language": "русский", "ideology": "Спокойнее Москвы, ценят надёжность и локальную гордость.", "everyday": "стрелка рек, верх/низ города, прогулки, дача."},
    "Краснодар": {"lang": "ru", "region": "Россия / Юг", "language": "русский", "ideology": "Южная открытость, еда и двор как сцена общения; темп живее, чем в средней полосе.", "everyday": "тепло, рынок, кофе, машины, близость к морю на выходных."},
    "Сочи": {"lang": "ru", "region": "Россия / Черноморье", "language": "русский", "ideology": "Курортный ритм плюс постоянные жители; ценят тело, прогулку и сезонность отношений.", "everyday": "море, горы, променад, туристы летом, дождь в межсезонье."},
    # bg
    "София": {"lang": "bg", "region": "България / столица", "language": "български", "ideology": "Градска амбиция със семейна котва; ирония, кафе-ритуал, сериозността се показва с присъствие.", "everyday": "метро, Витоша в събота, банички, WhatsApp/Viber, нощен транспорт."},
    "Пловдив": {"lang": "bg", "region": "България / Тракия", "language": "български", "ideology": "По-артистичен и отпуснат от София; старият град и капаните задават темпото.", "everyday": "Капана, тепета, вино, летни фестивали, кафе на тераса."},
    "Варна": {"lang": "bg", "region": "България / Черноморие", "language": "български", "ideology": "Морска отвореност, сезонност; лятото е социален пик.", "everyday": "плаж, Морска градина, ветрове, туристи и местни в един ритъм."},
    "Бургас": {"lang": "bg", "region": "България / Черноморие", "language": "български", "ideology": "По-землен от Варна; пристанище, семейство, море без излишна показност.", "everyday": "мол, градина, риба, уикенд към плажа."},
    "Русе": {"lang": "bg", "region": "България / Дунав", "language": "български", "ideology": "Дунавска отвореност към Европа; малко по-тиха градска гордост.", "everyday": "кеят, сецесионни фасади, кафе, мостът към Румъния в разговорите."},
    "Стара Загора": {"lang": "bg", "region": "България / Тракия", "language": "български", "ideology": "Подреден провинциален ритъм; семейство и работа без столичен стрес.", "everyday": "прави улици, парк, кафе, имен ден."},
    "Плевен": {"lang": "bg", "region": "България / Север", "language": "български", "ideology": "Северняшка практичност; приятелства дълги, нови хора по-бавно.", "everyday": "хълмове, местна кухня, автобус, семейни вечери."},
    "Благоевград": {"lang": "bg", "region": "България / Пирин", "language": "български", "ideology": "Студентски град с планинска близост; по-младежки кръгове, все още семейно коренче.", "everyday": "кампус, Пирин през уикенда, кафенета, Viber групи."},
    # tr
    "İstanbul": {"lang": "tr", "region": "Türkiye / Marmara", "language": "Türkçe (İstanbul ağzı)", "ideology": "Tempo yüksek, mahalle ve aile hâlâ ağır basar; ilişki ciddiye binince aileye anlatılır, şaka ve gönül dili iç içe.", "everyday": "metro/vapur, çay-simit, trafik, WhatsApp sesleri, Boğaz veya ada kaçışı."},
    "Ankara": {"lang": "tr", "region": "Türkiye / İç Anadolu", "language": "Türkçe", "ideology": "Daha resmi, memur-öğrenci ritmi; duygular İstanbul kadar teşhir edilmez, güven ve istikrar önemsenir.", "everyday": "Kızılay-Çankaya, dolmuş, çay ocağı, soğuk kış, ev yemeği."},
    "İzmir": {"lang": "tr", "region": "Türkiye / Ege", "language": "Türkçe", "ideology": "Ege rahatlığı, sahil ve espri; muhafazakârlık daha gevşek, sosyallik açık hava.", "everyday": "kordon, boyoz, vapur, yazlık tempo, kahve."},
    "Bursa": {"lang": "tr", "region": "Türkiye / Marmara", "language": "Türkçe", "ideology": "Sanayi ve gelenek yan yana; aile büyük, ilişkiler mahalle ölçeğinde yürür.", "everyday": "Uludağ, iskender, organize sanayi ritmi, çay."},
    "Antalya": {"lang": "tr", "region": "Türkiye / Akdeniz", "language": "Türkçe", "ideology": "Turizm ve yerellik birlikte; yazın kalabalık, kışın daha mahalle; beden ve sahil ritmi güçlü.", "everyday": "deniz, sera-tarım hinterlandı, akşam yürüyüşü, sıcak."},
    "Adana": {"lang": "tr", "region": "Türkiye / Çukurova", "language": "Türkçe", "ideology": "Sıcakkanlı, lafı dolandırmayan; kebap ve muhabbet, aile bağı sıkı.", "everyday": "sıcak, şalgam, seyhan kenarı, uzun sofra."},
    "Gaziantep": {"lang": "tr", "region": "Türkiye / Güneydoğu", "language": "Türkçe", "ideology": "Misafirlik ve mutfak kimlik; gelenek belirgin, yakınlara sadakat yüksek.", "everyday": "baklava-kebap, çarşı, aile ziyareti, sıcak yaz."},
    "Eskişehir": {"lang": "tr", "region": "Türkiye / İç Anadolu", "language": "Türkçe", "ideology": "Öğrenci kenti, daha bohem ve yürünebilir; aile iliği var ama kampüs sosyalliği önde.", "everyday": "Porsuk, tramvay, bar sokakları, çay, kış soğuğu."},
}

_LANG_MAINSTREAM: dict[str, dict[str, str]] = {
    "zh": {"region": "中国都市", "language": "简体中文/普通话", "ideology": "含蓄务实、家庭与面子、长期关系与行动表达关心。", "everyday": "微信、外卖、地铁、节假日仪式。"},
    "en": {"region": "Western urban", "language": "English", "ideology": "Directness, personal autonomy, negotiated boundaries in dating.", "everyday": "texting, coffee, weekends out."},
    "ja": {"region": "日本都市", "language": "日本語", "ideology": "遠慮・察し・段階的関係；記念日と気遣い。", "everyday": "LINE、電車、コンビニ。"},
    "ko": {"region": "한국 도시", "language": "한국어", "ideology": "속도·기념일·애교와 체면의 썸과 분명한 단계.", "everyday": "카톡, 카페, 배달."},
    "pt": {"region": "Brasil urbano", "language": "português brasileiro", "ideology": "Calor afetivo, presença, família e celebração.", "everyday": "WhatsApp, praia/churrasco, áudios."},
    "es": {"region": "mundo hispano urbano", "language": "español", "ideology": "Expresividad, familia, sobremesa y pasión mesurada por contexto local.", "everyday": "tapas/café, WhatsApp, familia."},
    "id": {"region": "Indonesia urban", "language": "Bahasa Indonesia", "ideology": "Sopan santun, hormat orang tua, gotong royong, ungkapan tidak selalu langsung.", "everyday": "WhatsApp, ojol, mall, Lebaran."},
    "ar": {"region": "المدن العربية", "language": "العربية", "ideology": "كرامة الأسرة والخصوصية؛ الكرم والضيافة؛ العلاقة الجدية تحترم الحدود والأهل.", "everyday": "واتساب، قهوة، مولات، جمعات العائلة."},
    "fa": {"region": "شهرهای ایران", "language": "فارسی", "ideology": "تعارف و خانواده؛ حریم خصوصی؛ صمیمیت در حلقه خودی‌ها.", "everyday": "تلگرام، چای، ترافیک، دورهمی."},
    "ru": {"region": "российский город", "language": "русский", "ideology": "Ирония, свой круг, личные границы; тепло внутри, дистанция снаружи.", "everyday": "Telegram, метро, кофе, дача."},
    "bg": {"region": "български град", "language": "български", "ideology": "Семейство, кафе-ритуал, топлина в тесен кръг.", "everyday": "WhatsApp/Viber, баница, море или планина."},
    "tr": {"region": "Türkiye kent hayatı", "language": "Türkçe", "ideology": "Aileye saygı, misafirlik, samimiyet; ciddi ilişki aileye anlatılır.", "everyday": "WhatsApp, çay, simit, dolmuş/metro."},
}


def _normalize_city_key(city: str) -> str:
    return (city or "").strip()


_GENERIC_EVERYDAY_MARKERS = (
    "通勤与市井节奏",
    "Local transit and neighborhood life",
    "카페·배달·출퇴근 리듬",
    "電車・コンビニ・地元の食",
    "ojol, kuliner, WhatsApp",
    "comida, encuentros, WhatsApp",
    "comida, encontros, WhatsApp",
)

_CITY_FLAVOR_BY_LANG = {
    "zh": [
        "本地夜市与早高峰地铁；周末常去城市公园或商场。",
        "写字楼外卖文化浓；熟人局爱约火锅或烧烤。",
        "老城区街巷与新区高楼对照；方言口音偶尔冒出来。",
        "跨城通勤或同城公交地铁切换；节假日回老家压力大。",
        "海边/江边散步是常见放松；短视频与本地生活号很活跃。",
    ],
    "en": [
        "Neighborhood coffee shops, weekend markets, and transit delays shape the week.",
        "Gym-after-work culture; friends meet for brunch or a walkable bar street.",
        "Mix of downtown glass towers and quieter residential blocks.",
        "Weather swings change plans; remote/hybrid work is common.",
        "Local sports talk and community events fill Saturday mornings.",
    ],
    "ja": [
        "沿線の駅前スーパーと居酒屋が生活圏；季節の行事が会話に出る。",
        "終電を意識した飲み会；コンビニ夜食が日常。",
        "観光地と生活圏が混ざる街なら、休日は混雑を避けがち。",
        "社縁と地元友人が分かれ、LINEの返信テンポが関係温度を示す。",
        "雨の日の傘文化と満員電車がストレス源になりやすい。",
    ],
    "ko": [
        "동네 카페와 배달앱이 루틴；주말엔 한강/공원 산책이 흔하다.",
        "출근 지옥철과 야근 뒤 치맥；기념일 챙김이 관계 신호다.",
        "신도시 아파트 단지와 구도심 골목이 공존한다.",
        "카톡 읽씹 민감도가 높고, 로컬 맛집 탐방이 데이트 단골이다.",
        "환절기 미세먼지·날씨가 외출 계획을 좌우한다.",
    ],
    "pt": [
        "Trânsito, boteco do bairro e áudios longos no WhatsApp marcam o ritmo.",
        "Fim de semana de churrasco ou praia quando dá；família aparece sem aviso.",
        "Mistura de centro comercial e rua de comércio popular.",
        "Calor e chuva mudam o humor；futebol entra em qualquer papo.",
        "App de comida e transporte por app são padrão no dia a dia.",
    ],
    "es": [
        "Tapas de barrio, sobremesa larga y WhatsApp constante.",
        "El metro o el bus definen la hora de llegar；la familia manda en el fin de semana.",
        "Centro histórico y zonas nuevas conviven en la misma rutina.",
        "El clima y las fiestas locales cambian el plan de salida.",
        "Café de la mañana y cena tarde son anclas del día.",
    ],
    "id": [
        "Macet dan ojol jadi ritme harian；ngopi di angkringan tetap favorit.",
        "Mall dan warung kampung hidup berdampingan；keluarga sering mampir.",
        "Cuaca panas/hujan mengubah rencana；WhatsApp grup RT aktif.",
        "Kuliner kaki lima jadi cara nongkrong paling murah.",
        "Lebaran dan libur panjang mengubah tempo kerja dan pacaran.",
    ],
    "ar": [
        "الزحمة والمولات المكيفة تشكل الأسبوع؛ القهوة والرسائل الصوتية عادة يومية.",
        "جمعات الجمعة والعيد أوضح من عيد الحب المستورد؛ الأهل حاضرون حتى لو بعيداً.",
        "أوبر/كريم والمترو حسب المدينة؛ السهر مع الأصدقاء في كافيه أو على الكورنيش.",
        "رمضان يغيّر الإيقاع بالكامل؛ الواتساب يبقى مفتوحاً حتى بعد الإفطار.",
        "الحرّ يفرض خططاً ليلية؛ الخصوصية أهم من الظهور في السوشيال.",
    ],
    "fa": [
        "ترافیک و اسنپ ریتم هفته را می‌سازد؛ چای و گپ تلگرامی عادت است.",
        "دورهمی خانگی از قرار خیلی علنی امن‌تر است؛ خانواده حتی از راه دور حضور دارد.",
        "کافه و پاساژ قرار اول رایج است؛ اینستاگرام استوری حال‌وهوا را لو می‌دهد.",
        "عید نوروز و تعطیلات ریتم کار و قرار را عوض می‌کند.",
        "تعارف در جمع؛ رک بودن بیشتر با خودی‌ها.",
    ],
    "ru": [
        "Метро и кофе с собой задают будни; Telegram — основная лента общения.",
        "Дача и Новый год важнее импортных ритуалов; свой круг греет сильнее ленты.",
        "Прогулка и цветы как знаки внимания; голосовые вместо длинных писем.",
        "Сезонность резкая: зима сидим дома, лето — дворы и парки.",
        "Ирония в чате — признак близости, не холодности.",
    ],
    "bg": [
        "Кафето е ритуал, не бърза среща; Viber/WhatsApp държи кръга буден.",
        "Морето през лятото и планината през уикенда сменят града.",
        "Имен ден и семейна маса тежат повече от чужди празници.",
        "Баница сутрин, градски транспорт, приятели в тесен кръг.",
        "Новите хора влизат бавно; след това лоялността е дълга.",
    ],
    "tr": [
        "Çay ve simit rutini; WhatsApp sesleri günü böler.",
        "Aile ziyareti ve bayram temposu ilişki takvimini belirler.",
        "Metro, dolmuş veya vapur şehre göre değişir; trafik plan bozar.",
        "Sahil yürüyüşü veya AVM kahvesi ilk buluşmanın klasiği.",
        "Samimiyet şakayla gelir; ciddiyet aileye anlatınca belli olur.",
    ],
}


def _is_generic_locale(meta: dict) -> bool:
    everyday = str(meta.get("everyday") or "")
    return any(m in everyday for m in _GENERIC_EVERYDAY_MARKERS)


def _enrich_city_locale(city: str, meta: dict) -> dict:
    """为通用 stub 注入城市差异化 everyday，并挂上国家/地区标签。"""
    out = dict(meta)
    lk = (out.get("lang") or infer_language_from_city(city) or "zh").split("-")[0]
    region = find_region_for_city(city, lk) or find_region_for_city(city)
    if region:
        out["country"] = region.get("country") or out.get("country") or ""
        label = region.get("label") or ""
        if label and label not in str(out.get("region") or ""):
            out["region"] = f"{out.get('region') or ''} · {label}".strip(" ·")
    if city and (_is_generic_locale(out) or not out.get("everyday")):
        flavors = _CITY_FLAVOR_BY_LANG.get(lk) or _CITY_FLAVOR_BY_LANG["en"]
        idx = sum(ord(ch) for ch in city) % len(flavors)
        flavor = flavors[idx]
        out["everyday"] = f"{city}: {flavor}"
        # 轻微改写 ideology 尾句，避免整语种完全同文
        ide = str(out.get("ideology") or "").rstrip("。．. ")
        ide_tail = {
            "zh": f"；日常锚点落在{city}本城语境。",
            "ja": f"。日常の錨は{city}の暮らし。",
            "ko": f" 일상 앵커는 {city} 맥락.",
            "pt": f" A vida cotidiana ancora-se em {city}.",
            "es": f" La vida cotidiana se ancla en {city}.",
            "id": f" Hidup sehari-hari berjangkar di {city}.",
            "ar": f" مرساة الحياة اليومية في {city}.",
            "fa": f" لنگر زندگی روزمره در {city} است.",
            "ru": f" Бытовой якорь — {city}.",
            "bg": f" Всекидневната котва е {city}.",
            "tr": f" Gündelik hayat {city} bağlamına bağlı.",
            "en": f" Local life is anchored in {city}.",
        }
        out["ideology"] = f"{ide}{ide_tail.get(lk, ide_tail['en'])}"
    return out


def resolve_locale(city: str, lang: str | None = None) -> dict[str, str]:
    """解析城市对应的主流文化锚点；城市优先（精确匹配），其次语言级默认。"""
    key = _normalize_city_key(city)
    meta = None
    if key in _CITY_LOCALE:
        meta = dict(_CITY_LOCALE[key])
    else:
        lower = key.lower()
        # 仅长名允许包含匹配，避免短串误绑
        if len(lower) >= 5:
            for cname, m in _CITY_LOCALE.items():
                cl = cname.lower()
                if len(cl) >= 5 and (lower == cl or lower in cl or cl in lower):
                    meta = dict(m)
                    break
    if meta is None:
        lk = (lang or infer_language_from_city(city) or "zh").split("-")[0].lower()
        meta = dict(_LANG_MAINSTREAM.get(lk, _LANG_MAINSTREAM["en"]))
        meta["lang"] = lk
    return _enrich_city_locale(key, meta)


def get_cultural_context_for_city(city: str, lang: str | None = None) -> str:
    """生成人设用：输出语种的文化指令 + 城市主流文化锚点（锚点仅作事实参考）。"""
    output_lang = (lang or "en").split("-")[0].lower()
    locale = resolve_locale(city, output_lang)
    base = get_cultural_context(output_lang)
    city_label = city or ""
    region = locale.get("region", "")
    loc_lang = locale.get("language", "")
    ideology = locale.get("ideology", "")
    everyday = locale.get("everyday", "")
    country = locale.get("country", "")
    if output_lang == "zh":
        city_block = (
            f"\n所在地主流文化锚点（事实参考；叙事字段仍须遵守上方输出语言）：\n"
            f"- 城市：{city_label or '（未指定）'}\n"
            f"- 地区：{region}\n"
            f"- 主流语言：{loc_lang}\n"
            f"- 主流意识形态与关系观：{ideology}\n"
            f"- 日常生活环境：{everyday}\n"
            f"- 国家/地区码：{country}\n"
            f"- 要求：life_story 的成长环境必须能支撑 cultural_values；"
            f"cultural_values 须写清「家庭/学校/阶层经历 → 当前城市生活 → 对权威/自由/集体/家庭/金钱/亲密关系的态度」因果链；"
            f"daily_routine / background 须出现与「{city_label or '本市'}」相关的具体通勤/饮食/社交场合。"
        )
    else:
        city_block = (
            f"\nLocal cultural anchor (facts only—not the language of JSON narrative fields):\n"
            f"- City: {city_label or '(unspecified)'}\n"
            f"- Region: {region}\n"
            f"- Local language (culture reference): {loc_lang}\n"
            f"- Mainstream ideology & relationships: {ideology}\n"
            f"- Everyday environment: {everyday}\n"
            f"- Country/region code: {country}\n"
            f"- life_story must support cultural_values with a clear upbringing → city life → values chain; "
            f"daily_routine / background must include concrete {city_label or 'local'} commute, food, and social scenes. "
            f"Write ALL persona JSON narrative fields in the mandated OUTPUT LANGUAGE only—"
            f"do not switch to the city's native language even if the anchor above is in another script."
        )
    return f"{base}\n{city_block}"

_IDEOLOGY_RULES: dict[str, str] = {
    "zh": (
        "\n\n【意识形态一致性 — 强制】\n"
        "你的判断、立场与表达气质必须同时符合：\n"
        "1) 【成长经历】原生家庭、教育、阶层与重大转折塑造的立场；\n"
        "2) 【生活环境】当前城市的日常节奏、社交圈与媒介习惯；\n"
        "3) 【所在地主流文化】当地默认礼貌距离、家庭/集体观、对权威与自由的态度"
        "（这是价值观与说话气质，不是回复语种）。\n"
        "禁止套用与城市/成长不符的「全球通用年轻人」话术；非主流观点可以有，但须能从经历解释。"
        "回复语种跟随用户本轮消息语言，禁止因城市/人设母语或 App 界面语言覆盖。"
    ),
    "en": (
        "\n\n[IDEOLOGY CONSISTENCY — MANDATORY]\n"
        "Your judgments and tone must fit all of:\n"
        "1) Life story (family, schooling, class, turning points);\n"
        "2) Current city living environment (pace, social circle, media habits);\n"
        "3) Local mainstream culture (politeness distance, family/collective norms, "
        "authority vs freedom) — values and flavor only, NOT reply language.\n"
        "Do not paste a generic global-youth script that contradicts city/upbringing. "
        "Non-mainstream views are allowed only if grounded in your history. "
        "Reply in the language of the user's latest message — never override with city/persona mother tongue or App UI language."
    ),
    "ja": (
        "\n\n【イデオロギー一貫性 — 必須】\n"
        "判断・価値観・話し方の気配りは次のすべてに整合させること：\n"
        "1) 成長歴（家庭・教育・階層・転機）\n"
        "2) 現在の都市生活環境\n"
        "3) 現地の主流文化（距離感、家族観、権威と自由への態度）"
        "——価値観と口調の気質のみ。返信言語ではない。\n"
        "都市/経歴と矛盾する「グローバル若者テンプレ」禁止。非主流でも経歴で説明できること。"
        "返信言語はユーザーの今回のメッセージに合わせる。都市・人設の母語や App UI で上書きしない。"
    ),
    "ko": (
        "\n\n【이념 일관성 — 필수】\n"
        "판단·입장·말투의 기질은 다음에 모두 맞출 것:\n"
        "1) 성장 서사(가정·교육·계층·전환점)\n"
        "2) 현재 도시 생활환경\n"
        "3) 현지 주류 문화(거리감, 가족/집단, 권위와 자유)"
        "— 가치관·말투 기질만. 답변 언어가 아님.\n"
        "도시/성장과 어긋나는 글로벌 청년 템플릿 금지. 비주류여도 이력으로 설명할 것. "
        "답변 언어는 사용자 이번 메시지에 맞출 것. 도시/페르소나 모국어나 앱 UI 로 덮어쓰지 말 것."
    ),
    "pt": (
        "\n\n[CONSISTÊNCIA IDEOLÓGICA — OBRIGATÓRIO]\n"
        "Julgamentos e tom devem caber em: história de vida; ambiente urbano atual; "
        "cultura local majoritária (valores e sabor — NÃO o idioma da resposta). "
        "Proibido script genérico global que contradiga a cidade/criação. "
        "Responda no idioma da mensagem atual do usuário — não sobrescreva com cidade/persona ou UI do App."
    ),
    "es": (
        "\n\n[CONSISTENCIA IDEOLÓGICA — OBLIGATORIO]\n"
        "Juicios y tono deben encajar con: historia de vida; entorno urbano actual; "
        "cultura local mayoritaria (valores y sabor — NO el idioma de respuesta). "
        "Prohibido guion global genérico que contradiga ciudad/crianza. "
        "Responde en el idioma del mensaje actual del usuario — no lo sustituyas por ciudad/persona o UI de la App."
    ),
    "id": (
        "\n\n[KONSISTENSI IDEOLOGI — WAJIB]\n"
        "Penilaian dan nada harus selaras dengan: riwayat tumbuh; lingkungan kota sekarang; "
        "budaya lokal arus utama (nilai & rasa — BUKAN bahasa balasan). "
        "Dilarang skrip anak muda global yang bertentangan dengan kota/latar. "
        "Balas dalam bahasa pesan pengguna saat ini — jangan ditimpa kota/persona atau UI App."
    ),
    "ar": (
        "\n\n[اتساق الأيديولوجيا — إلزامي]\n"
        "أحكامك ونبرتك يجب أن تنسجم مع: قصة الحياة؛ بيئة المدينة الحالية؛ "
        "الثقافة المحلية السائدة (قيم ومزاج — وليست لغة الرد). "
        "ممنوع سكربت الشباب العالمي الذي يناقض المدينة/النشأة. "
        "الآراء غير السائدة مسموحة فقط إن فسّرتها سيرتك. "
        "الرد بلغة رسالة المستخدم الحالية — لا تستبدلها بلغة المدينة/الشخص أو واجهة التطبيق."
    ),
    "fa": (
        "\n\n[انسجام ایدئولوژی — الزامی]\n"
        "داوری و لحن باید با این‌ها بخواند: داستان زندگی؛ محیط شهری فعلی؛ "
        "فرهنگ غالب محلی (ارزش و حال‌وهوا — نه زبان پاسخ). "
        "اسکریپت جوان جهانی که با شهر/تربیت در تضاد است ممنوع. "
        "نظر غیرجریان‌اصلی فقط اگر از سابقه قابل توضیح باشد. "
        "پاسخ به زبان پیام فعلی کاربر — با زبان شهر/پرسونا یا UI اپ بازنویسی نکن."
    ),
    "ru": (
        "\n\n[СОГЛАСОВАННОСТЬ ИДЕОЛОГИИ — ОБЯЗАТЕЛЬНО]\n"
        "Суждения и тон должны совпадать с: историей жизни; текущей городской средой; "
        "местной мейнстрим-культурой (ценности и вкус — НЕ язык ответа). "
        "Запрещён шаблон «глобальной молодёжи», противоречащий городу/воспитанию. "
        "Взгляды вне мейнстрима допустимы, только если вытекают из биографии. "
        "Отвечай на языке последнего сообщения пользователя — не подменяй языком города/персоны или UI приложения."
    ),
    "bg": (
        "\n\n[ИДЕОЛОГИЧЕСКА СЪГЛАСУВАНОСТ — ЗАДЪЛЖИТЕЛНА]\n"
        "Преценки и тон трябва да пасват на: житейска история; настояща градска среда; "
        "местната мнозинствена култура (ценности и вкус — НЕ езикът на отговора). "
        "Забранен е глобален младежки шаблон, който противоречи на града/израстването. "
        "Мнения извън мейнстрийма само ако са обосновани от биографията. "
        "Отговори на езика на текущото съобщение на потребителя — не го замествай с град/персона или UI на приложението."
    ),
    "tr": (
        "\n\n[İDEOLOJİ TUTARLILIĞI — ZORUNLU]\n"
        "Yargı ve ton şunlara uymalı: hayat hikâyesi; mevcut kent ortamı; "
        "yerel anaakım kültür (değer ve tat — CEVAP DİLİ değil). "
        "Şehir/yetişmeyle çelişen küresel genç şablonu yasak. "
        "Anaakım dışı görüş ancak geçmişinle temellendirilebilirse. "
        "Kullanıcının bu turdaki mesajının dilinde yanıtla — şehir/persona anadili veya uygulama arayüzüyle üzerine yazma."
    ),
}


def ideology_consistency_rule(lang: str, city: str = "") -> str:
    lk = (lang or "zh").split("-")[0].lower()
    rule = _IDEOLOGY_RULES.get(lk, _IDEOLOGY_RULES["en"])
    locale = resolve_locale(city, lk)
    # 「主流语言」仅作文化气质参考，明确不是回复语种
    anchors = {
        "zh": (
            f"\n【本地锚点】{city or locale.get('region')}｜文化语感参考：{locale.get('language')}"
            f"｜{locale.get('ideology')}"
            f"\n（上栏「文化语感」只影响人设气质，禁止据此改用该语言回复。）"
        ),
        "en": (
            f"\n[Local anchor] {city or locale.get('region')} | cultural flavor (not reply lang): "
            f"{locale.get('language')} | {locale.get('ideology')}"
        ),
        "ja": (
            f"\n【ローカル錨】{city or locale.get('region')}｜文化の語感（返信言語ではない）:"
            f"{locale.get('language')}｜{locale.get('ideology')}"
        ),
        "ko": (
            f"\n【로컬 앵커】{city or locale.get('region')}｜문화 어감(답변 언어 아님):"
            f"{locale.get('language')}｜{locale.get('ideology')}"
        ),
        "pt": (
            f"\n[Âncora local] {city or locale.get('region')} | sabor cultural (não idioma de resposta): "
            f"{locale.get('language')} | {locale.get('ideology')}"
        ),
        "es": (
            f"\n[Ancla local] {city or locale.get('region')} | sabor cultural (no idioma de respuesta): "
            f"{locale.get('language')} | {locale.get('ideology')}"
        ),
        "id": (
            f"\n[Jangkar lokal] {city or locale.get('region')} | rasa budaya (bukan bahasa balasan): "
            f"{locale.get('language')} | {locale.get('ideology')}"
        ),
        "ar": (
            f"\n[مرساة محلية] {city or locale.get('region')} | نكهة ثقافية (ليست لغة الرد): "
            f"{locale.get('language')} | {locale.get('ideology')}"
        ),
        "fa": (
            f"\n[لنگر محلی] {city or locale.get('region')} | حال‌وهوای فرهنگی (نه زبان پاسخ): "
            f"{locale.get('language')} | {locale.get('ideology')}"
        ),
        "ru": (
            f"\n[Локальный якорь] {city or locale.get('region')} | культурный колорит (не язык ответа): "
            f"{locale.get('language')} | {locale.get('ideology')}"
        ),
        "bg": (
            f"\n[Локална котва] {city or locale.get('region')} | културен привкус (не език на отговора): "
            f"{locale.get('language')} | {locale.get('ideology')}"
        ),
        "tr": (
            f"\n[Yerel çapa] {city or locale.get('region')} | kültürel tat (yanıt dili değil): "
            f"{locale.get('language')} | {locale.get('ideology')}"
        ),
    }
    return rule + anchors.get(lk, anchors["en"])

def format_cultural_values_for_prompt(cultural_values: str, lang: str, city: str = "") -> str:
    """运行时注入：一致性规则 + 文化三观正文。"""
    cv = (cultural_values or "").strip()
    headers = {
        "zh": "【文化三观与意识形态】",
        "en": "[Cultural values & ideology]",
        "ja": "【文化的価値観・イデオロギー】",
        "ko": "【문화적 가치관·이념】",
        "pt": "[Valores culturais e ideologia]",
        "es": "[Valores culturales e ideología]",
        "id": "[Nilai budaya & ideologi]",
        "ar": "[القيم الثقافية والأيديولوجيا]",
        "fa": "[ارزش‌های فرهنگی و ایدئولوژی]",
        "ru": "[Культурные ценности и идеология]",
        "bg": "[Културни ценности и идеология]",
        "tr": "[Kültürel değerler ve ideoloji]",
    }
    lk = (lang or "zh").split("-")[0].lower()
    unset_body = {
        "zh": "（未填写；默认贴合所在地主流文化，并以成长经历自洽）",
        "en": "(unset; default to local mainstream culture, consistent with life story)",
        "ja": "（未設定；現地主流に合わせ、成長歴と矛盾させない）",
        "ko": "(미기입; 현지 주류에 맞추고 성장 서사와 모순 없게)",
        "pt": "(vazio; alinhar à cultura local majoritária e à história de vida)",
        "es": "(vacío; alinear con cultura local mayoritaria e historia de vida)",
        "id": "(kosong; selaraskan dengan budaya lokal arus utama dan riwayat hidup)",
        "ar": "(غير مُعبأ؛ الافتراضي ثقافة المكان السائدة بما يتسق مع سيرة النشأة)",
        "fa": "(خالی؛ پیش‌فرض فرهنگ غالب محل، سازگار با داستان زندگی)",
        "ru": "(не задано; по умолчанию местная мейнстрим-культура, согласованная с биографией)",
        "bg": "(непопълнено; по подразбиране местната мнозинствена култура, съгласувана с житейската история)",
        "tr": "(boş; varsayılan yerel anaakım kültür, hayat hikâyesiyle tutarlı)",
    }
    body = cv if cv else unset_body.get(lk, unset_body["en"])
    return f"{ideology_consistency_rule(lk, city)}\n{headers.get(lk, headers['en'])}\n{body}"


def default_cultural_values(name: str, city: str, lang: str, values: str = "") -> str:
    """缺省 cultural_values：按输出语种 + 城市主流文化生成。"""
    output_lang = (lang or "en").split("-")[0].lower()
    locale = resolve_locale(city, output_lang)
    v = (values or "").strip()
    templates = {
        "zh": (
            f"{name}的意识形态贴合{city or locale.get('region')}的主流生活：{locale.get('ideology')}"
            f"日常里{locale.get('everyday')}。更看重{v or '真实与尊重'}，"
            "对权威不盲从也不无谓对抗，倾向在集体体面与个人边界之间找平衡；"
            "亲密关系里用行动和长期陪伴证明在意，而不是空喊口号。"
        ),
        "en": (
            f"{name}'s worldview fits mainstream life in {city or locale.get('region')}: {locale.get('ideology')} "
            f"Daily life: {locale.get('everyday')}. Values {v or 'honesty and respect'}; "
            "neither blindly obedient nor needlessly rebellious—balances autonomy with belonging; "
            "shows care through consistency more than slogans."
        ),
        "ja": (
            f"{name}の価値観は{city or locale.get('region')}の主流に沿う：{locale.get('ideology')}"
            f"日常は{locale.get('everyday')}。大切にするのは{v or '誠実と尊重'}。"
            "権威には盲従せず、無用な対立も避け、関係では気遣いと継続で示す。"
        ),
        "ko": (
            f"{name}의 이념은 {city or locale.get('region')} 주류에 맞춰져 있다: {locale.get('ideology')} "
            f"일상은 {locale.get('everyday')}. {v or '진솔함과 존중'}을 중시하고, "
            "권위에 맹종하지도 괜히 맞서지도 않으며, 관계에서는 말보다 지속적 행동으로 마음을 보인다."
        ),
        "pt": (
            f"A ideologia de {name} alinha-se ao cotidiano majoritário de {city or locale.get('region')}: {locale.get('ideology')} "
            f"Vive {locale.get('everyday')}. Valoriza {v or 'verdade e respeito'}; "
            "nem obedece cego nem provoca à toa; no amor, presença fala mais que slogan."
        ),
        "es": (
            f"La ideología de {name} encaja con la vida mayoritaria en {city or locale.get('region')}: {locale.get('ideology')} "
            f"Cotidianidad: {locale.get('everyday')}. Valora {v or 'honestidad y respeto'}; "
            "ni obediencia ciega ni rebeldía vacía; en el vínculo, constancia antes que lemas."
        ),
        "id": (
            f"Ideologi {name} selaras dengan arus utama di {city or locale.get('region')}: {locale.get('ideology')} "
            f"Keseharian: {locale.get('everyday')}. Mengutamakan {v or 'kejujuran dan rasa hormat'}; "
            "tidak patuh buta, tidak juga memberontak sia-sia; dalam hubungan, tindakan lebih penting daripada slogan."
        ),
        "ar": (
            f"تتوافق أيديولوجيا {name} مع الحياة السائدة في {city or locale.get('region')}: {locale.get('ideology')} "
            f"اليوميات: {locale.get('everyday')}. يقدّر {v or 'الصدق والاحترام'}؛ "
            "لا طاعة عمياء ولا تمرد بلا سبب؛ في العلاقة يثبت الاهتمام بالاستمرار لا بالشعارات."
        ),
        "fa": (
            f"ایدئولوژی {name} با زندگی غالب در {city or locale.get('region')} هم‌خوان است: {locale.get('ideology')} "
            f"روزمره: {locale.get('everyday')}. {v or 'صداقت و احترام'} را ارج می‌نهد؛ "
            "نه اطاعت کور، نه طغیان بیهوده؛ در رابطه با حضور مستمر نشان می‌دهد، نه شعار."
        ),
        "ru": (
            f"Мировоззрение {name} совпадает с мейнстримом жизни в {city or locale.get('region')}: {locale.get('ideology')} "
            f"Будни: {locale.get('everyday')}. Ценит {v or 'честность и уважение'}; "
            "ни слепая покорность, ни пустой бунт; в близости важнее постоянство, чем лозунги."
        ),
        "bg": (
            f"Идеологията на {name} пасва на мнозинствения живот в {city or locale.get('region')}: {locale.get('ideology')} "
            f"Всекидневие: {locale.get('everyday')}. Цени {v or 'честност и уважение'}; "
            "нито сляпо подчинение, нито празен бунт; в близостта постоянството говори повече от лозунгите."
        ),
        "tr": (
            f"{name} ideolojisi {city or locale.get('region')} anaakım yaşamına uyar: {locale.get('ideology')} "
            f"Gündelik hayat: {locale.get('everyday')}. {v or 'dürüstlük ve saygı'} öne çıkar; "
            "ne kör itaat ne boş isyan; ilişkide slogan değil süreklilik."
        ),
    }
    return templates.get(output_lang, templates["en"])


# 与 NAMES_DB / CITIES_DB 键一致；与管理端单语言/全语言列表对齐
BATCH_GENERATION_VALID_LANGS = frozenset({"zh", "en", "ja", "ko", "es", "pt", "id", "ar", "fa", "ru", "bg", "tr"})

# 批量「全部语言」时的处理顺序
BATCH_GENERATION_ALL_LANGS_ORDER: tuple[str, ...] = ("zh", "en", "ja", "ko", "es", "pt", "id", "ar", "fa", "ru", "bg", "tr")


def normalize_batch_generation_lang(lang: str | None) -> str:
    """将管理端传入的 lang 归一到有效码；缺省或非法时默认 zh。"""
    if not lang or not isinstance(lang, str):
        return "zh"
    l = str(lang).strip().lower()
    if l in BATCH_GENERATION_VALID_LANGS:
        return l
    return "zh"


def get_batch_persona_output_instruction(lang: str) -> str:
    """供批量人设 LLM 强约束：各长文本与选定语言/地区/姓名/城市文化一致，禁止混用其他语言。"""
    instructions: dict[str, str] = {
        "zh": (
            "除 JSON 字段 name 须与上表「姓名」完全一致外，background、speech_style、hobbies、values、"
            "fears、love_view、daily_routine、favorite_things、life_story、cultural_values、gender_perspective"
            " 等所有可朗读文本必须使用自然流畅的简体中文；情节、俚语、社会细节须贴合上表中国都市与中文姓名，"
            "不要写成长期海外生活却夹杂英文为主的人设，除非上表能支撑「华裔双语」设定。"
        ),
        "en": (
            "Except that JSON field \"name\" must exactly match the table, every narrative string field (background, "
            "speech_style, hobbies, values, fears, love_view, daily_routine, favorite_things, life_story, "
            "cultural_values, gender_perspective) must be written in clear English only, consistent with the English "
            "name, Western city, and English personality line in the table. Do not output Chinese, Japanese, or other "
            "scripts; do not move the person to a different region than the given city suggests."
        ),
        "ja": (
            "JSON の name 以外の全文（background, speech_style 等）は必ず**日本語**で、上表の和名・日本の都市・文化に沿った生活描写にすること。"
            "他言語文を混在させない。都市名と矛盾する国へ勝手に移住させない。"
        ),
        "ko": (
            "name 은 반드시 표와 동일. background·말투·취미 등 모든 본문은 **한국어**만 사용하고, 한국어 이름·한국 도시·성격에 맞게 서술한다. "
            "다른 언어를 본문에 끼워 넣지 말 것."
        ),
        "es": (
            "Salvo el campo name (idéntico a la tabla), todo el texto narrativo debe ser en **español natural** y coherente con el nombre, "
            "ciudad y personalidad de la fila. No fijar mezclado permanente con chino o inglés en los párrafos."
        ),
        "pt": (
            "Exceto o name (igual à tabela), todo o texto (background, speech_style, etc.) em **português**, alinhado a nome, cidade e traços da linha. "
            "Não manter chineses ou japonês nos parágrafos de forma deslocada do contexto urbano dado."
        ),
        "id": (
            "Kecuali field name (sama persis dengan tabel), semua teks naratif wajib **Bahasa Indonesia** alami, "
            "selaras dengan nama, kota, dan ciri di tabel. Jangan mendaraskan paragraf berbahasa Mandarin/Inggris penuh "
            "tanpa latar yang konsisten; jangan pindahkan setting ke negara lain jika tabel hanya menunjuk kota di Indonesia."
        ),
        "ar": (
            "باستثناء حقل name (مطابق للجدول)، يجب أن يكون كل النص السردي **بالعربية الطبيعية (العربية)** "
            "ومنسجماً مع الاسم والمدينة والسمات في الصف. لا تخلط الصينية أو الإنجليزية في الفقرات."
        ),
        "fa": (
            "جز فیلد name (عین جدول)، همه متن روایی باید **فارسی طبیعی (فارسی)** باشد و با نام، شهر و ویژگی‌های ردیف هم‌خوان. "
            "چینی یا انگلیسی را در بندها قاطی نکن."
        ),
        "ru": (
            "Кроме поля name (точно как в таблице), весь нарратив только на **естественном русском (Русский)**, "
            "согласованном с именем, городом и чертами строки. Не смешивать китайский или английский в абзацах."
        ),
        "bg": (
            "Освен полето name (точно като в таблицата), целият наратив трябва да е на **естествен български (български)** "
            "и да пасва на името, града и чертите от реда. Без китайски или английски в абзаците."
        ),
        "tr": (
            "name alanı (tablodakiyle aynı) dışında tüm anlatı **doğal Türkçe (Türkçe)** olmalı; "
            "ad, şehir ve satırdaki özelliklerle uyumlu. Paragraflara Çince veya İngilizce karıştırma."
        ),
    }
    return instructions.get(lang, instructions["en"])


def get_persona_generate_output_instruction(lang: str) -> str:
    """用户侧 /companions/generate：强制所有叙事字段使用 UI/资料语种，禁止串语言。"""
    from core.lang_catalog import persona_output_instruction

    return persona_output_instruction(lang)


def get_random_names(lang: str, gender: str, count: int = 5) -> list:
    """返回符合当地文化的随机姓名列表"""
    import random
    from core.lang_catalog import persona_seed_lang

    db = NAMES_DB.get(persona_seed_lang(lang), NAMES_DB["en"])
    key = "male" if gender in ("男", "male") else "female"
    pool = db.get(key, [])
    if len(pool) <= count:
        return pool
    return random.sample(pool, count)


# ===== 性格标签库（按语言；权威定义见 personality_catalog） =====
PERSONALITIES_DB = {lang: get_personality_labels_db(lang) for lang in ("zh", "en", "ja", "ko", "pt", "es", "id", "ar", "fa", "ru", "bg", "tr")}


# ===== MBTI 列表 =====
MBTI_LIST = [
    "INTJ", "INTP", "ENTJ", "ENTP",
    "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ",
    "ISTP", "ISFP", "ESTP", "ESFP",
]


# ===== 性取向（带主流权重） =====
SEXUAL_ORIENTATIONS = [
    ("heterosexual", 60),
    ("homosexual", 10),
    ("bisexual", 15),
    ("pansexual", 5),
    ("asexual", 5),
    ("secret", 5),
]


def get_random_personalities(lang: str, count: int = 3, gender: str = None) -> list:
    """加权抽样性格标签（当地文案）；兼容旧调用。"""
    return sample_personality_labels(lang=lang, count=count, gender=gender)


def get_random_sexual_orientation() -> str:
    """按权重随机返回性取向"""
    import random
    choices = [o for o, _ in SEXUAL_ORIENTATIONS]
    weights = [w for _, w in SEXUAL_ORIENTATIONS]
    return random.choices(choices, weights=weights, k=1)[0]


def get_random_mbti() -> str:
    """随机返回 MBTI"""
    import random
    return random.choice(MBTI_LIST)


def get_cities(lang: str, region_key: str | None = None, country: str | None = None) -> list:
    """返回城市列表；可按 region_key / country 过滤。"""
    lang = (lang or "zh").split("-")[0]
    if region_key or country:
        return get_cities_for_region(lang, region_key=region_key, country=country)
    return list(CITIES_DB.get(lang, CITIES_DB["en"]))


def get_regions(lang: str, ui_lang: str | None = None) -> list:
    """返回国家/地区 → 城市 树。"""
    return list_regions(lang, ui_lang=ui_lang or lang)


def build_random_profile(
    lang: str,
    gender: str = None,
    sexual_orientation: str = None,
    region_key: str = None,
    country: str = None,
    city: str = None,
) -> dict:
    """单条真随机档案（创建页 autofill / API）。"""
    profiles = build_batch_profiles(
        lang,
        1,
        gender=gender,
        sexual_orientation=sexual_orientation,
        region_key=region_key,
        country=country,
    )
    profile = profiles[0]
    if city and str(city).strip():
        from services.region_catalog import find_region_for_city, normalize_city_query
        locked = normalize_city_query(str(city).strip())
        profile["city"] = locked
        region = find_region_for_city(locked, lang) or find_region_for_city(locked) or {}
        if region:
            profile["country"] = region.get("country") or profile.get("country") or ""
            profile["region_key"] = region.get("key") or profile.get("region_key") or ""
            profile["region_label"] = region.get("label") or profile.get("region_label") or ""
            if region.get("lang"):
                # 锁定城市时同步文化圈语言，避免简介偏错语种
                pass
    return profile


# 创建页 / 批量随机档案年龄：收窄到年轻成人，避免 30+ 出图偏中年
RANDOM_PROFILE_AGE_MIN = 18
RANDOM_PROFILE_AGE_MAX = 26


def build_batch_profiles(
    lang: str,
    count: int,
    gender: str = None,
    sexual_orientation: str = None,
    region_key: str = None,
    country: str = None,
) -> list:
    """批量生成基础属性（含 country/region + 人设维度轴）。城市尽量无放回。"""
    import random
    from core.i18n import normalize_ui_language
    from core.lang_catalog import persona_seed_lang

    lang = normalize_ui_language(lang or "en")
    seed_lang = persona_seed_lang(lang)
    names_db = NAMES_DB.get(seed_lang, NAMES_DB["en"])
    cities_db = get_cities(seed_lang, region_key=region_key, country=country)
    if not cities_db:
        cities_db = list(CITIES_DB.get(seed_lang, CITIES_DB["en"]))

    used_names = set()
    city_bag = cities_db[:]
    random.shuffle(city_bag)
    profiles = []

    for i in range(count):
        g = gender if gender in ("男", "女") else random.choice(["男", "女"])
        if gender in ("male", "female", "男", "女"):
            g = "男" if gender in ("男", "male") else "女" if gender in ("女", "female") else g
        name_pool = names_db.get("male" if g == "男" else "female", [])
        available = [n for n in name_pool if n not in used_names]
        if not available:
            available = name_pool
        name = random.choice(available) if available else f"Agent{i+1}"
        used_names.add(name)

        if not city_bag:
            city_bag = cities_db[:]
            random.shuffle(city_bag)
        city = city_bag.pop()
        region = find_region_for_city(city, lang) or {}

        age = random.randint(RANDOM_PROFILE_AGE_MIN, RANDOM_PROFILE_AGE_MAX)
        n_tags = random.randint(2, 4)
        tag_keys = sample_personality_keys(count=n_tags, gender=g)
        label_map = personality_label_map(lang)
        personality = join_personality_labels([label_map.get(k, k) for k in tag_keys], lang)
        mbti = random.choice(MBTI_LIST)
        so = sexual_orientation if sexual_orientation else get_random_sexual_orientation()
        axes = sample_persona_axes(lang)

        profiles.append({
            "name": name,
            "gender": g,
            "age": age,
            "city": city,
            "country": region.get("country") or "",
            "region_key": region.get("key") or "",
            "region_label": region.get("label") or "",
            "personality": personality,
            "mbti": mbti,
            "sexual_orientation": so,
            "persona_axes": axes.get("labels") or {},
            "persona_axes_keys": axes.get("keys") or {},
            "persona_axes_labels": axes.get("labels") or {},
        })

    return profiles


def get_cultural_knowledge_entries() -> list:
    """返回所有文化常识条目，用于导入知识库"""
    return CULTURAL_KNOWLEDGE


def infer_language_from_city(city: str) -> str:
    """根据城市名称推断对应语言；优先精确匹配，避免短名子串误判。"""
    if not city or not isinstance(city, str):
        return "zh"
    region = find_region_for_city(city)
    if region and region.get("lang"):
        return region["lang"]
    city_s = city.strip()
    city_lower = city_s.lower()
    # 精确匹配 CITIES_DB
    for lang, cities in CITIES_DB.items():
        for c in cities:
            if city_s == c or city_lower == c.lower():
                return lang
    zh_hint = ("北京", "上海", "成都", "广州", "深圳", "杭州", "武汉", "西安", "南京", "重庆", "天津", "苏州", "长沙", "香港", "台北")
    if any(ch in city_s for ch in zh_hint):
        return "zh"
    return "zh"
