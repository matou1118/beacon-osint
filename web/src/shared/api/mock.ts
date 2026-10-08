import type {
  Announcement,
  AnnouncementList,
  Categories,
  GraphNodeDetail,
  GraphSummary,
  HeatTopicsData,
  LoginResult,
  Regions,
  Subscription,
} from '@/shared/types'

// ── 分类 ──────────────────────────────────────────────────

export const mockCategories: Categories = {
  l1: [
    '招商',
    '行业政策',
    '行业补贴',
    '公招信息',
    '招标信息',
    '商业机会',
    '招聘信息',
    '活动通知',
    '人才政策',
    '养老与社区',
    '招生信息',
    '其他',
  ],
  elderly_sub: [
    '高龄津贴',
    '失能补贴',
    '老年助餐',
    '探访关爱',
    '适老化改造',
    '社区活动',
    '养老机构',
    '长期护理保险',
  ],
  admission_sub: [
    '高职单招',
    '军队院校招生',
    '公安司法院校招生',
    '公费定向培养',
    '行业院校招生',
    '职业院校招生',
    '事业单位定向培养',
    '招生政策解读',
  ],
}

// ── 地域 ──────────────────────────────────────────────────

export const mockRegions: Regions = {
  provinces: ['北京', '上海', '广东', '浙江', '江苏', '四川', '山东', '湖北', '湖南', '河南'],
  cities: {
    北京: ['东城区', '西城区', '海淀区', '朝阳区', '丰台区'],
    上海: ['黄浦区', '徐汇区', '长宁区', '静安区', '浦东新区'],
    广东: ['广州市', '深圳市', '珠海市', '佛山市', '东莞市'],
    浙江: ['杭州市', '宁波市', '温州市', '绍兴市', '嘉兴市'],
    江苏: ['南京市', '苏州市', '无锡市', '常州市', '徐州市'],
    四川: ['成都市', '绵阳市', '德阳市', '南充市', '宜宾市'],
    山东: ['济南市', '青岛市', '烟台市', '潍坊市', '临沂市'],
    湖北: ['武汉市', '黄石市', '十堰市', '宜昌市', '襄阳市'],
    湖南: ['长沙市', '株洲市', '湘潭市', '衡阳市', '岳阳市'],
    河南: ['郑州市', '洛阳市', '开封市', '新乡市', '南阳市'],
  },
  counties: {},
}

// ── 公告 ──────────────────────────────────────────────────

const now = new Date()
function daysAgo(n: number): string {
  const d = new Date(now)
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}
function daysAhead(n: number): string {
  const d = new Date(now)
  d.setDate(d.getDate() + n)
  return d.toISOString().slice(0, 10)
}

export const mockAnnouncements: Announcement[] = [
  {
    id: 'a001',
    title: '关于组织开展2026年度高新技术企业认定工作的通知',
    issuing_body: '四川省科技厅',
    gov_level: 'provincial',
    region: '四川省',
    region_province: '四川',
    region_city: null,
    region_county: null,
    category_l1: '行业补贴',
    category_l2: '研发补贴',
    target_group: '企业',
    target_industry: '科技',
    amount: '最高300万元',
    deadline: daysAhead(45),
    key_points: [
      '申报条件：企业需拥有核心自主知识产权',
      '补贴标准：首次认定奖励30万元，复审奖励20万元',
      '申报材料：高新技术企业认定申请书、知识产权证明、研发费用专项审计报告',
    ],
    source_url: 'https://kjt.sc.gov.cn/xxgk/zcwj/',
    publish_date: daysAgo(2),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: null,
    corroboration_status: 'corroborated',
    corroboration_count: 2,
    corroboration_reason: '四川省科技厅官网与国家科技部官网均发布',
    corroborating_sources: [
      { source_id: 's001', domain: 'kjt.sc.gov.cn', url: 'https://kjt.sc.gov.cn/xxgk/zcwj/', source_type: 'government' },
      { source_id: 's002', domain: 'most.gov.cn', url: 'https://www.most.gov.cn/', source_type: 'government' },
    ],
    match_reasons: ['层级匹配：省级', '地域匹配：四川省', '分类匹配：行业补贴', '关键词匹配：高新技术企业、补贴'],
    match_score: 85,
  },
  {
    id: 'a002',
    title: '教育部关于做好2026年普通高等学校招生工作的通知',
    issuing_body: '教育部',
    gov_level: 'national',
    region: '全国',
    region_province: null,
    region_city: null,
    region_county: null,
    category_l1: '招生信息',
    category_l2: '招生政策解读',
    target_group: '学生',
    target_industry: null,
    amount: null,
    deadline: daysAhead(90),
    key_points: [
      '招生计划：全国普通本专科招生计划安排约1000万人',
      '政策要点：继续实施国家专项计划、地方专项计划和高校专项计划',
      '时间安排：6月7日-8日全国统一高考',
    ],
    source_url: 'http://www.moe.gov.cn/srcsite/A15/moe_776/s3258/202601/t20260121_1427110.html',
    publish_date: daysAgo(1),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 78,
    corroboration_status: 'corroborated',
    corroboration_count: 3,
    corroboration_reason: '教育部官网、人民网、新华网均报道',
    corroborating_sources: [
      { source_id: 's003', domain: 'moe.gov.cn', url: 'http://www.moe.gov.cn/srcsite/A15/moe_776/s3258/202601/t20260121_1427110.html', source_type: 'government' },
      { source_id: 's004', domain: 'people.com.cn', url: 'https://www.people.com.cn/', source_type: 'mainstream_media' },
    ],
    match_reasons: ['层级匹配：国家级', '分类匹配：招生信息', '关键词匹配：高考、招生'],
    match_score: 92,
  },
  {
    id: 'a003',
    title: '成都市2026年首批城市更新机会清单发布',
    issuing_body: '成都市住房和城乡建设局',
    gov_level: 'municipal',
    region: '四川省成都市',
    region_province: '四川',
    region_city: '成都',
    region_county: null,
    category_l1: '行业政策',
    category_l2: null,
    target_group: '居民',
    target_industry: '住建',
    amount: '总投资约1740亿元',
    deadline: daysAhead(60),
    key_points: [
      '清单项目：共推出140个城市更新项目',
      '更新类型：危旧房改造、老旧小区改造、城中村改造、老旧街区厂区更新、城市生态修复等八大任务',
      '专项子清单：银行融资需求26个项目186.4亿元、招商运营需求25个项目137.5亿元、低效闲置资产盘活12个项目',
    ],
    source_url: 'http://sc.people.com.cn/n2/2026/0729/c379471-41652791.html',
    publish_date: daysAgo(5),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 45,
    corroboration_status: 'corroborated',
    corroboration_count: 2,
    corroboration_reason: '人民网四川频道与中新网四川均报道',
    corroborating_sources: [
      { source_id: 's005', domain: 'people.com.cn', url: 'http://sc.people.com.cn/n2/2026/0729/c379471-41652791.html', source_type: 'mainstream_media' },
      { source_id: 's005b', domain: 'chinanews.com.cn', url: 'https://www.sc.chinanews.com.cn/cdxw/2026-07-28/247202.html', source_type: 'mainstream_media' },
    ],
    match_reasons: ['层级匹配：市级', '地域匹配：四川省成都市', '分类匹配：行业政策', '关键词匹配：老旧小区、改造'],
    match_score: 78,
  },
  {
    id: 'a004',
    title: '关于开展2026年事业单位公开招聘的公告',
    issuing_body: '湖北省人力资源和社会保障厅',
    gov_level: 'provincial',
    region: '湖北省',
    region_province: '湖北',
    region_city: null,
    region_county: null,
    category_l1: '招聘信息',
    category_l2: '事业单位招聘',
    target_group: '求职者',
    target_industry: null,
    amount: null,
    deadline: daysAhead(20),
    key_points: [
      '招聘人数：省直事业单位共招聘386人',
      '报名方式：网上报名，每人限报一个岗位',
      '笔试时间：2026年11月15日',
      '笔试科目：《职业能力倾向测验》和《综合应用能力》',
    ],
    source_url: 'https://rst.hubei.gov.cn/zfxxgk/fdzdgknr/qtzdgk/',
    publish_date: daysAgo(3),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 62,
    corroboration_status: 'corroborated',
    corroboration_count: 2,
    corroboration_reason: '湖北省人社厅官网与荆楚网均发布',
    corroborating_sources: [
      { source_id: 's006', domain: 'rst.hubei.gov.cn', url: 'https://rst.hubei.gov.cn/zfxxgk/fdzdgknr/qtzdgk/', source_type: 'government' },
    ],
    match_reasons: ['层级匹配：省级', '地域匹配：湖北省', '分类匹配：招聘信息', '关键词匹配：事业单位、招聘'],
    match_score: 88,
  },
  {
    id: 'a005',
    title: '关于下达2026年省级中小企业发展专项资金的通知',
    issuing_body: '浙江省经济和信息化厅',
    gov_level: 'provincial',
    region: '浙江省',
    region_province: '浙江',
    region_city: null,
    region_county: null,
    category_l1: '行业补贴',
    category_l2: '研发补贴',
    target_group: '企业',
    target_industry: '制造业',
    amount: '专项资金总额2.8亿元',
    deadline: daysAhead(30),
    key_points: [
      '支持方向：专精特新中小企业培育、小微企业园建设、中小企业数字化改造',
      '申报条件：在浙江省内注册登记、具有独立法人资格的中小企业',
      '补贴标准：单个项目最高补助不超过200万元',
    ],
    source_url: 'https://jxt.zj.gov.cn/col/col1229457/index.html',
    publish_date: daysAgo(6),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 38,
    corroboration_status: 'single_source',
    corroboration_count: 1,
    corroboration_reason: null,
    corroborating_sources: [
      { source_id: 's007', domain: 'jxt.zj.gov.cn', url: 'https://jxt.zj.gov.cn/col/col1229457/index.html', source_type: 'government' },
    ],
    match_reasons: ['层级匹配：省级', '地域匹配：浙江省', '分类匹配：行业补贴', '关键词匹配：中小企业、专项资金'],
    match_score: 75,
  },
  {
    id: 'a006',
    title: '关于2026年高龄津贴发放标准调整的通知',
    issuing_body: '青岛市卫生健康委员会',
    gov_level: 'municipal',
    region: '山东省青岛市',
    region_province: '山东',
    region_city: '青岛',
    region_county: null,
    category_l1: '养老与社区',
    category_l2: '高龄津贴',
    target_group: '老年人',
    target_industry: null,
    amount: '每人每月200-600元',
    deadline: null,
    key_points: [
      '发放对象：具有青岛市户籍、年满80周岁及以上的老年人',
      '发放标准：80-89周岁每人每月200元，90-99周岁每人每月400元，100周岁及以上每人每月600元',
      '申请方式：向户籍所在地社区（村）提出申请',
    ],
    source_url: 'http://wsjkw.qingdao.gov.cn/',
    publish_date: daysAgo(4),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 52,
    corroboration_status: 'corroborated',
    corroboration_count: 2,
    corroboration_reason: '青岛市卫健委官网与青岛日报均报道',
    corroborating_sources: [
      { source_id: 's008', domain: 'wsjkw.qingdao.gov.cn', url: 'http://wsjkw.qingdao.gov.cn/', source_type: 'government' },
    ],
    match_reasons: ['层级匹配：市级', '地域匹配：山东省青岛市', '分类匹配：养老与社区', '关键词匹配：高龄津贴、老年人'],
    match_score: 82,
  },
  {
    id: 'a007',
    title: '国家发展改革委 国家能源局关于印发《抽水蓄能电站开发建设管理暂行办法》的通知',
    issuing_body: '国家发展和改革委员会',
    gov_level: 'national',
    region: '全国',
    region_province: null,
    region_city: null,
    region_county: null,
    category_l1: '行业政策',
    category_l2: null,
    target_group: null,
    target_industry: '能源',
    amount: null,
    deadline: null,
    key_points: [
      '文号：发改能源规〔2025〕93号',
      '核心内容：规范抽水蓄能电站开发建设管理，确保工程质量和安全',
      '核准要求：先评估后核准，委托工程咨询单位独立开展咨询评估',
      '有效期：自公布之日起施行，有效期5年',
    ],
    source_url: 'https://zfxxgk.ndrc.gov.cn/web/iteminfo.jsp?id=20481',
    publish_date: daysAgo(7),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 71,
    corroboration_status: 'corroborated',
    corroboration_count: 3,
    corroboration_reason: '发改委官网、中国政府网、北极星电力网均报道',
    corroborating_sources: [
      { source_id: 's009', domain: 'ndrc.gov.cn', url: 'https://zfxxgk.ndrc.gov.cn/web/iteminfo.jsp?id=20481', source_type: 'government' },
      { source_id: 's010', domain: 'gov.cn', url: 'https://www.gov.cn/gongbao/2025/issue_11946/202503/content_7015856.html', source_type: 'government' },
    ],
    match_reasons: ['层级匹配：国家级', '分类匹配：行业政策', '关键词匹配：抽水蓄能、能源项目'],
    match_score: 90,
  },
  {
    id: 'a008',
    title: '关于2026年公安司法院校招生面试体检体能测试的公告',
    issuing_body: '四川省教育考试院',
    gov_level: 'provincial',
    region: '四川省',
    region_province: '四川',
    region_city: null,
    region_county: null,
    category_l1: '招生信息',
    category_l2: '公安司法院校招生',
    target_group: '学生',
    target_industry: null,
    amount: null,
    deadline: daysAhead(15),
    key_points: [
      '参加对象：报考公安司法院校且文化成绩达到面试分数线的考生',
      '时间地点：2026年10月20日-22日，四川警察学院',
      '测试项目：面试、体检、体能测试（男子1000米跑、女子800米跑、50米跑、立定跳远）',
    ],
    source_url: 'https://www.sceea.cn/Html/202610/',
    publish_date: daysAgo(3),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 41,
    corroboration_status: 'single_source',
    corroboration_count: 1,
    corroboration_reason: null,
    corroborating_sources: [
      { source_id: 's011', domain: 'sceea.cn', url: 'https://www.sceea.cn/Html/202610/', source_type: 'education' },
    ],
    match_reasons: ['层级匹配：省级', '地域匹配：四川省', '分类匹配：招生信息', '关键词匹配：公安司法院校、招生'],
    match_score: 80,
  },
  {
    id: 'a009',
    title: '关于公开征求《XX市养老服务条例（草案）》意见的公告',
    issuing_body: '上海市民政局',
    gov_level: 'municipal',
    region: '上海市',
    region_province: '上海',
    region_city: null,
    region_county: null,
    category_l1: '养老与社区',
    category_l2: '养老机构',
    target_group: '老年人',
    target_industry: null,
    amount: null,
    deadline: daysAhead(25),
    key_points: [
      '征求意见时间：2026年10月10日至11月10日',
      '意见反馈方式：信函、传真或电子邮件',
      '主要内容：养老服务设施规划、居家社区养老服务、机构养老服务、医养结合等',
    ],
    source_url: 'https://mzj.sh.gov.cn/shmzj2024/index.html',
    publish_date: daysAgo(4),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 35,
    corroboration_status: 'single_source',
    corroboration_count: 1,
    corroboration_reason: null,
    corroborating_sources: [
      { source_id: 's012', domain: 'mzj.sh.gov.cn', url: 'https://mzj.sh.gov.cn/shmzj2024/index.html', source_type: 'government' },
    ],
    match_reasons: ['层级匹配：市级', '地域匹配：上海市', '分类匹配：养老与社区', '关键词匹配：养老服务、条例'],
    match_score: 73,
  },
  {
    id: 'a010',
    title: '关于2026年市级重大项目前期费用补助的公示',
    issuing_body: '深圳市发展和改革委员会',
    gov_level: 'municipal',
    region: '广东省深圳市',
    region_province: '广东',
    region_city: '深圳',
    region_county: null,
    category_l1: '行业补贴',
    category_l2: null,
    target_group: '企业',
    target_industry: '综合',
    amount: '补助资金合计1.2亿元',
    deadline: daysAhead(10),
    key_points: [
      '补助对象：2026年市级重大项目前期工作承担单位',
      '补助标准：按项目前期工作实际费用的50%给予补助，最高不超过500万元',
      '公示时间：2026年10月6日至10月12日',
    ],
    source_url: 'http://fgw.sz.gov.cn/zwgk/',
    publish_date: daysAgo(2),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 28,
    corroboration_status: 'single_source',
    corroboration_count: 1,
    corroboration_reason: null,
    corroborating_sources: [
      { source_id: 's013', domain: 'fgw.sz.gov.cn', url: 'http://fgw.sz.gov.cn/zwgk/', source_type: 'government' },
    ],
    match_reasons: ['层级匹配：市级', '地域匹配：广东省深圳市', '分类匹配：行业补贴', '关键词匹配：重大项目、补助'],
    match_score: 76,
  },
  {
    id: 'a011',
    title: '关于做好2026年农村订单定向本科医学生免费培养项目招生工作的通知',
    issuing_body: '湖南省卫生健康委员会',
    gov_level: 'provincial',
    region: '湖南省',
    region_province: '湖南',
    region_city: null,
    region_county: null,
    category_l1: '招生信息',
    category_l2: '公费定向培养',
    target_group: '学生',
    target_industry: '医疗',
    amount: '免学费、免住宿费，补助生活费',
    deadline: daysAhead(40),
    key_points: [
      '文号：湘卫函〔2026〕38号',
      '招生计划：全省定向医学生436人（临床医学277人、中医学和中西医临床医学159人）',
      '培养院校：湖南师范大学、南华大学、吉首大学、湘南学院、邵阳学院、湖南医药学院、长沙医学院、湖南中医药大学',
      '就业保障：毕业后定向到基层医疗卫生机构服务6年',
    ],
    source_url: 'https://gaokao.chsi.com.cn/gkxx/zc/ss/202606/20260601/2293519385.html',
    publish_date: daysAgo(8),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 33,
    corroboration_status: 'corroborated',
    corroboration_count: 2,
    corroboration_reason: '湖南省卫健委官网与湖南省教育厅官网均发布',
    corroborating_sources: [
      { source_id: 's014', domain: 'chsi.com.cn', url: 'https://gaokao.chsi.com.cn/gkxx/zc/ss/202606/20260601/2293519385.html', source_type: 'education' },
    ],
    match_reasons: ['层级匹配：省级', '地域匹配：湖南省', '分类匹配：招生信息', '关键词匹配：定向医学生、免费培养'],
    match_score: 84,
  },
  {
    id: 'a012',
    title: '关于江苏省第六批专精特新"小巨人"企业和第三批专精特新"小巨人"复核通过企业名单的公示',
    issuing_body: '江苏省工业和信息化厅',
    gov_level: 'provincial',
    region: '江苏省',
    region_province: '江苏',
    region_city: null,
    region_county: null,
    category_l1: '商业机会',
    category_l2: null,
    target_group: '企业',
    target_industry: '制造业',
    amount: null,
    deadline: daysAhead(35),
    key_points: [
      '公示内容：江苏省第六批专精特新"小巨人"企业名单及第三批复核通过企业名单',
      '背景：工信部开展第六批专精特新"小巨人"企业培育和第三批复核工作',
      '意义：专精特新"小巨人"企业可享受各级财政扶持、融资支持等政策',
    ],
    source_url: 'http://gxt.jiangsu.gov.cn/art/2024/9/2/art_6281_11338926.html',
    publish_date: daysAgo(5),
    media_type: null,
    media_tags: [],
    media_cluster_id: null,
    media_heat: 30,
    corroboration_status: 'single_source',
    corroboration_count: 1,
    corroboration_reason: null,
    corroborating_sources: [
      { source_id: 's015', domain: 'gxt.jiangsu.gov.cn', url: 'http://gxt.jiangsu.gov.cn/art/2024/9/2/art_6281_11338926.html', source_type: 'government' },
    ],
    match_reasons: ['层级匹配：省级', '地域匹配：江苏省', '分类匹配：商业机会', '关键词匹配：技术创新、项目征集'],
    match_score: 72,
  },
]

// ── 公告列表 ──────────────────────────────────────────────

export function mockAnnouncementList(params: Record<string, unknown>): AnnouncementList {
  let items = [...mockAnnouncements]

  const category = params.category as string | undefined
  if (category && category !== '全部') {
    items = items.filter((a) => a.category_l1 === category)
  }

  const keyword = params.keyword as string | undefined
  if (keyword) {
    const kw = keyword.toLowerCase()
    items = items.filter(
      (a) =>
        a.title.toLowerCase().includes(kw) ||
        a.issuing_body.toLowerCase().includes(kw) ||
        a.key_points.some((p) => p.toLowerCase().includes(kw)),
    )
  }

  const region = params.region as string | undefined
  if (region) {
    items = items.filter(
      (a) =>
        a.region_province === region ||
        a.region_city === region ||
        a.region?.includes(region),
    )
  }

  const govLevel = params.gov_level as string | undefined
  if (govLevel) {
    items = items.filter((a) => a.gov_level === govLevel)
  }

  const page = Number(params.page ?? 1)
  const pageSize = Number(params.page_size ?? 20)

  return {
    total: items.length,
    page,
    page_size: pageSize,
    items: items.slice((page - 1) * pageSize, page * pageSize),
  }
}

// ── 热频话题 ──────────────────────────────────────────────

export const mockHeatTopics: HeatTopicsData = {
  date: daysAgo(0),
  topics: [
    {
      cluster_id: 'h001',
      heat_score: 95,
      terms: ['高考', '招生计划', '教育部'],
      title: '教育部关于做好2026年普通高等学校招生工作的通知',
      source_count: 12,
    },
    {
      cluster_id: 'h002',
      heat_score: 82,
      terms: ['抽水蓄能', '能源项目', '发改委'],
      title: '国家发展改革委关于核准XX抽水蓄能电站项目的批复',
      source_count: 8,
    },
    {
      cluster_id: 'h003',
      heat_score: 71,
      terms: ['事业单位', '招聘', '湖北'],
      title: '关于开展2026年事业单位公开招聘的公告',
      source_count: 6,
    },
    {
      cluster_id: 'h004',
      heat_score: 58,
      terms: ['高龄津贴', '养老', '青岛'],
      title: '关于2026年高龄津贴发放标准调整的通知',
      source_count: 5,
    },
    {
      cluster_id: 'h005',
      heat_score: 45,
      terms: ['老旧小区', '改造', '成都'],
      title: '关于2026年城镇老旧小区改造工作的通知',
      source_count: 4,
    },
    {
      cluster_id: 'h006',
      heat_score: 38,
      terms: ['中小企业', '专项资金', '浙江'],
      title: '关于下达2026年省级中小企业发展专项资金的通知',
      source_count: 3,
    },
  ],
}

// ── 图谱 ──────────────────────────────────────────────────

export const mockGraphSummary: GraphSummary = {
  nodes: 1248,
  edges: 3567,
  evidence: 8912,
  needs_review: 214,
  node_types: { announcement: 9467, source: 34, org: 89, region: 120, category: 12, industry: 45 },
  edge_types: { issued_by: 9467, located_in: 9467, belongs_to: 9467, targets: 3200 },
}

export function mockGraphNode(nodeId: string): GraphNodeDetail {
  return {
    node: { id: nodeId, type: 'announcement', name: '示例公告' },
    neighbors: [
      { edge_type: 'ISSUED_BY', direction: 'outgoing', node: { id: 'organization:1', type: 'organization', name: '四川省科技厅' } },
      { edge_type: 'LOCATED_IN', direction: 'outgoing', node: { id: 'region:1', type: 'region', name: '四川省' } },
      { edge_type: 'BELONGS_TO', direction: 'outgoing', node: { id: 'category:1', type: 'category', name: '行业补贴' } },
    ],
  }
}

// ── 订阅 ──────────────────────────────────────────────────

export const mockSubscriptions: Subscription[] = [
  {
    id: 1,
    gov_levels: ['national', 'provincial'],
    region: '四川',
    categories: ['行业补贴', '行业政策', '招聘信息'],
    target_group: '企业',
    target_industry: '科技',
    keywords: ['高新技术企业', '研发补贴'],
    push_frequency: 'daily',
    enabled: true,
    created_at: daysAgo(30),
  },
  {
    id: 2,
    gov_levels: ['provincial', 'municipal'],
    region: '四川',
    categories: ['招生信息'],
    target_group: '学生',
    target_industry: null,
    keywords: ['高考', '招生'],
    push_frequency: 'weekly',
    enabled: true,
    created_at: daysAgo(15),
  },
]

// ── 登录 ──────────────────────────────────────────────────

export const mockLoginResult: LoginResult = {
  token: 'mock-jwt-token-for-preview',
  user_id: 'mock-user-001',
  expires_in: 86400,
}
