# 地区文化数据 —— 姓名、城市、文化常识

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
}


# ===== 城市库（按语言） =====
CITIES_DB = {
    "zh": ["北京", "上海", "成都", "广州", "深圳", "杭州", "武汉", "西安", "南京", "重庆"],
    "en": ["New York", "Los Angeles", "London", "San Francisco", "Seattle", "Chicago", "Boston", "Austin", "Melbourne", "Toronto"],
    "ja": ["東京", "大阪", "京都", "札幌", "福岡", "名古屋", "横浜", "神戸", "仙台", "広島"],
    "ko": ["서울", "부산", "인천", "대구", "광주", "대전", "울산", "제주", "수원", "창원"],
    "pt": ["São Paulo", "Rio de Janeiro", "Salvador", "Brasília", "Belo Horizonte", "Fortaleza", "Curitiba", "Porto Alegre", "Recife", "Manaus"],
    "es": ["Madrid", "Barcelona", "México City", "Buenos Aires", "Lima", "Bogotá", "Santiago", "Valencia", "Sevilla", "Guadalajara"],
    "id": ["Jakarta", "Surabaya", "Bandung", "Medan", "Makassar", "Yogyakarta", "Semarang", "Bali", "Palembang", "Malang"],
}


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
        "content": "한국의 데이트는 보통 남성이主導하지만, 최근에는 여성도 적극적으로约하는 경우가 늘고 있다. 첫 데이트는 보통 카페나 식사로 시작한다. '넷플릭스 앤 칠' 같은 홈 데이트도 인기가 많다. 소개팅과 미팅은 매우 흔한 연애 시작 방식이다. 부모님께 인사드리는 것은 매우 진지한 단계로 여겨진다. 명절(추석, 설날)에는 선물 세트를 주고받는 문화가 있다.",
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
}


def get_cultural_context(lang: str) -> str:
    return _CULTURAL_CONTEXTS.get(lang, _CULTURAL_CONTEXTS["zh"])


# 与 NAMES_DB / CITIES_DB 键一致；与管理端单语言/全语言列表对齐
BATCH_GENERATION_VALID_LANGS = frozenset({"zh", "en", "ja", "ko", "es", "pt", "id"})

# 批量「全部语言」时的处理顺序
BATCH_GENERATION_ALL_LANGS_ORDER: tuple[str, ...] = ("zh", "en", "ja", "ko", "es", "pt", "id")


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
    }
    return instructions.get(lang, instructions["en"])


def get_random_names(lang: str, gender: str, count: int = 5) -> list:
    """返回符合当地文化的随机姓名列表"""
    import random
    db = NAMES_DB.get(lang, NAMES_DB["zh"])
    key = "male" if gender in ("男", "male") else "female"
    pool = db.get(key, [])
    if len(pool) <= count:
        return pool
    return random.sample(pool, count)


def get_cities(lang: str) -> list:
    """返回对应语言的典型城市列表"""
    return CITIES_DB.get(lang, CITIES_DB["zh"])


# ===== 性格标签库（按语言） =====
PERSONALITIES_DB = {
    "zh": ["温柔", "可爱", "活泼", "阳光", "成熟", "腹黑", "冷静", "酷", "文艺", "元气", "傲娇", "天然呆", "毒舌", "御姐", "治愈", "神秘", "热情", "直率", "内敛", "细腻", "理性", "感性", "幽默", "慵懒", "独立", "黏人", "乖巧", "叛逆", "优雅", "随性"],
    "en": ["warm", "cute", "energetic", "sunny", "mature", "mysterious", "calm", "cool", "artsy", "lively", "tsundere", "airheaded", "sarcastic", "queenly", "healing", "passionate", "straightforward", "reserved", "delicate", "rational", "emotional", "humorous", "lazy", "independent", "clingy", "obedient", "rebellious", "elegant", "casual", "bold"],
    "ja": ["温柔", "可愛い", "活発", "元気", "成熟", "腹黒", "冷静", "クール", "文芸的", "元気いっぱい", "ツンデレ", "天然", "毒舌", "お姉さん系", "癒し系", "情熱的", "素直", "内向的", "繊細", "理性的", "感性的", "ユーモラス", "のんびり", "独立", "甘えん坊", "おとなしい", "反抗的", "優雅", "気まま", "大胆"],
    "ko": ["다정", "귀여움", "활발", "밝음", "성숙", "츤데레", "침착", "쿨", "예술적", "생기넘침", "츤데레", "천연", "독설", "누나형", "힐링", "열정적", "솔직", "내향적", "섬세", "이성적", "감성적", "유머러스", "느긋", "독립적", "애교만점", "얌전", "반항적", "우아", "자유로움", "대담"],
    "pt": ["carinhoso", "fofo", "energético", "alegre", "maduro", "misterioso", "calmo", "descolado", "artístico", "vibrante", "tsundere", "distraído", "sarcástico", "dominante", "acolhedor", "apaixonado", "direto", "reservado", "delicado", "racional", "emotivo", "humorístico", "preguiçoso", "independente", "apegado", "obediente", "rebelde", "elegante", "casual", "ousado"],
    "es": ["cariñoso", "adorable", "enérgico", "soleado", "maduro", "misterioso", "calmado", "genial", "artístico", "animado", "tsundere", "distraído", "sarcástico", "dominante", "sanador", "apasionado", "directo", "reservado", "delicado", "racional", "emotivo", "humorístico", "perezoso", "independiente", "apegado", "obediente", "rebelde", "elegante", "casual", "atrevido"],
    "id": ["lembut", "imut", "ceria", "cerah", "dewasa", "misterius", "tenang", "keren", "artistik", "enerjik", "tsundere", "polos", "sinis", "kakak-perempuan", "penyembuh", "penuh gairah", "blak-blakan", "tertutup", "halus", "rasional", "emosional", "lucu", "santai", "mandiri", "manja", "penurut", "nakal", "anggun", "santai", "berani"],
}


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


# ===== 批量生成辅助函数 =====
def get_random_personalities(lang: str, count: int = 3) -> list:
    """返回随机性格标签列表"""
    import random
    pool = PERSONALITIES_DB.get(lang, PERSONALITIES_DB["zh"])
    count = max(2, min(count, len(pool)))
    return random.sample(pool, count)


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


def build_batch_profiles(lang: str, count: int, gender: str = None, sexual_orientation: str = None) -> list:
    """批量生成基础属性列表，返回字典列表（含 name, gender, age, city, personality, mbti, sexual_orientation）

    Args:
        lang: 语言/文化背景
        count: 生成数量
        gender: 指定性别（"男" 或 "女"），None 则随机
        sexual_orientation: 指定性取向，None 则随机
    """
    import random
    names_db = NAMES_DB.get(lang, NAMES_DB["zh"])
    cities_db = CITIES_DB.get(lang, CITIES_DB["zh"])
    personalities_db = PERSONALITIES_DB.get(lang, PERSONALITIES_DB["zh"])

    used_names = set()
    profiles = []

    for _ in range(count):
        g = gender if gender in ("男", "女") else random.choice(["男", "女"])
        name_pool = names_db.get("male" if g == "男" else "female", [])
        # 避免重名
        available = [n for n in name_pool if n not in used_names]
        if not available:
            available = name_pool
        name = random.choice(available)
        used_names.add(name)

        city = random.choice(cities_db)
        age = random.randint(18, 35)
        personality_tags = random.sample(personalities_db, k=min(3, len(personalities_db)))
        personality = "、".join(personality_tags) if lang == "zh" else ", ".join(personality_tags)
        mbti = random.choice(MBTI_LIST)
        so = sexual_orientation if sexual_orientation else get_random_sexual_orientation()

        profiles.append({
            "name": name,
            "gender": g,
            "age": age,
            "city": city,
            "personality": personality,
            "mbti": mbti,
            "sexual_orientation": so,
        })

    return profiles


def get_cultural_knowledge_entries() -> list:
    """返回所有文化常识条目，用于导入知识库"""
    return CULTURAL_KNOWLEDGE


def infer_language_from_city(city: str) -> str:
    """根据城市名称推断对应语言，与前端 companionLang.ts 和 CITIES_DB 保持一致。
    用于确保机器人资料的 language 与地区信息一致。
    """
    if not city or not isinstance(city, str):
        return "zh"
    city_lower = city.strip().lower()
    for lang, cities in CITIES_DB.items():
        for c in cities:
            c_lower = c.lower()
            if city_lower in c_lower or c_lower in city_lower or any(word in city_lower for word in [c_lower.split()[0] if ' ' in c else c_lower]):
                return lang
    # 默认中文地区
    if any(ch in city_lower for ch in ["北京", "上海", "成都", "广州", "深圳", "杭州", "武汉", "西安", "南京", "重庆"]):
        return "zh"
    return "zh"
