"""Price request scope, distinct from A8 calculation eligibility.
NHIS non-A8 29-country reference includes Taiwan/Singapore (not OECD members).
Source fields are starting points, not evidence of any product's price.
"""
A8 = [
    ("JP", "일본", "JPY"), ("IT", "이탈리아", "EUR"), ("FR", "프랑스", "EUR"),
    ("CH", "스위스", "CHF"), ("UK", "영국", "GBP"), ("DE", "독일", "EUR"),
    ("CA", "캐나다", "CAD"), ("US", "미국", "USD"),
]
EXTRA = [
    ("AU", "호주", "AUD", "https://www.pbs.gov.au/"),
    ("NZ", "뉴질랜드", "NZD", "https://schedule.pharmac.govt.nz/"),
    ("TW", "대만", "TWD", "https://info.nhi.gov.tw/INAE3000/INAE3000S02"),
    ("NO", "노르웨이", "NOK", "https://www.dmp.no/"),
    ("DK", "덴마크", "DKK", "https://www.medicinpriser.dk/"),
    ("SE", "스웨덴", "SEK", "https://www.tlv.se/"),
    ("FI", "핀란드", "EUR", "https://www.kela.fi/medicines"),
    ("IS", "아이슬란드", "ISK", "https://www.lyfjastofnun.is/"),
    ("CZ", "체코", "CZK", "https://prehledy.sukl.cz/prehled_leciv.html"),
    ("SK", "슬로바키아", "EUR", "https://www.health.gov.sk/"),
    ("HU", "헝가리", "HUF", "https://www.neak.gov.hu/"),
    ("PL", "폴란드", "PLN", "https://www.gov.pl/web/zdrowie"),
    ("GR", "그리스", "EUR", "https://www.moh.gov.gr/"),
    ("NL", "네덜란드", "EUR", "https://www.medicijnkosten.nl/"),
    ("BE", "벨기에", "EUR", "https://www.riziv.fgov.be/"),
    ("LU", "룩셈부르크", "EUR", "https://cns.public.lu/"),
    ("LV", "라트비아", "EUR", "https://dati.zva.gov.lv/zalu-registrs/"),
    ("LT", "리투아니아", "EUR", "https://kainynas.vlk.lt/webapp/index.html"),
    ("IL", "이스라엘", "ILS", "https://www.gov.il/"),
    ("IE", "아일랜드", "EUR", "https://www.ncpe.ie/"),
    ("CL", "칠레", "CLP", "https://www.cenabast.cl/"),
    ("MX", "멕시코", "MXN", "https://www.gob.mx/se"),
    ("ES", "스페인", "EUR", "https://www.sanidad.gob.es/"),
    ("SI", "슬로베니아", "EUR", "https://www.jazmp.si/"),
    ("SG", "싱가포르", "SGD", "https://www.mims.com/singapore"),
    ("EE", "에스토니아", "EUR", "https://www.ravimiregister.ee/"),
    ("AT", "오스트리아", "EUR", "https://www.sozialversicherung.at/"),
    ("TR", "튀르키예", "TRY", "https://www.titck.gov.tr/"),
    ("PT", "포르투갈", "EUR", "https://www.infarmed.pt/"),
]
CATALOG = {
    "a8": [dict(code=c, label=l, currency=u) for c, l, u in A8],
    "oecd": [dict(code=c, label=l, currency=u, source_url=s) for c, l, u, s in EXTRA],
    "note": "OECD·공단 추가국가: A8 제외 공단 가이드 29개국(대만·싱가포르 포함). OECD 회원국 전체 목록과는 다릅니다. 국가별 원가격 단계와 VAT를 구분하며 A8 평균에 합산하지 않습니다.",
}
CURRENCIES = {r[0]: r[2] for r in A8 + EXTRA}
