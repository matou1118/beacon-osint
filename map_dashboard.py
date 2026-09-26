from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
import yaml

from theme_config import THEME_LABELS, THEME_ORDER, get_theme_js_colors

ROOT = Path(__file__).resolve().parent
GEO_DIR = ROOT / "assets" / "geo"
VENDOR_DIR = ROOT / "assets" / "vendor"

PROVINCE_COORDINATES = {
    "110000": [116.4, 39.9],
    "120000": [117.2, 39.1],
    "130000": [114.5, 38.0],
    "140000": [112.5, 37.6],
    "150000": [111.7, 40.8],
    "210000": [123.4, 41.8],
    "220000": [125.3, 43.9],
    "230000": [126.6, 45.8],
    "310000": [121.5, 31.2],
    "320000": [118.8, 32.1],
    "330000": [120.2, 30.3],
    "340000": [117.3, 31.8],
    "350000": [118.3, 26.1],
    "360000": [115.9, 28.7],
    "370000": [117.0, 36.7],
    "410000": [113.6, 34.8],
    "420000": [114.3, 30.6],
    "430000": [112.9, 28.2],
    "440000": [113.3, 23.1],
    "450000": [108.3, 22.8],
    "460000": [110.0, 19.6],
    "500000": [107.9, 30.0],
    "510000": [104.1, 30.7],
    "520000": [106.7, 26.6],
    "530000": [101.7, 25.0],
    "540000": [91.1, 29.7],
    "610000": [108.9, 34.3],
    "620000": [103.8, 36.1],
    "630000": [101.8, 36.5],
    "640000": [106.3, 38.5],
    "650000": [87.6, 43.8],
    "710000": [121.0, 23.7],
    "810000": [114.2, 22.3],
    "820000": [113.5, 22.2],
    "100000_JD": [112.0, 15.0],
}

PROVINCE_SHORT_NAMES = {
    "110000": "北京", "120000": "天津", "130000": "河北", "140000": "山西",
    "150000": "内蒙古", "210000": "辽宁", "220000": "吉林", "230000": "黑龙江",
    "310000": "上海", "320000": "江苏", "330000": "浙江", "340000": "安徽",
    "350000": "福建", "360000": "江西", "370000": "山东", "410000": "河南",
    "420000": "湖北", "430000": "湖南", "440000": "广东", "450000": "广西",
    "460000": "海南", "500000": "重庆", "510000": "四川", "520000": "贵州",
    "530000": "云南", "540000": "西藏", "610000": "陕西", "620000": "甘肃",
    "630000": "青海", "640000": "宁夏", "650000": "新疆", "710000": "台湾",
    "810000": "香港", "820000": "澳门",
}


@lru_cache(maxsize=8)
def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _admin_lookups() -> dict[str, dict[str, str]]:
    provinces = _read_json(str(GEO_DIR / "administrative-levels.json"))
    cities = _read_json(str(GEO_DIR / "administrative-cities.json"))
    counties = _read_json(str(GEO_DIR / "administrative-counties.json"))
    return {
        "province": {str(item["name"]): str(item["code"]) for item in provinces},
        "city": {str(item["name"]): str(item["code"]) for item in cities},
        "county": {str(item["name"]): str(item["code"]) for item in counties},
        "province_by_code": {str(item["code"]): str(item["name"]) for item in provinces},
        "city_by_code": {str(item["code"]): str(item["name"]) for item in cities},
        "county_by_code": {str(item["code"]): str(item["name"]) for item in counties},
    }


def _round_coordinates(value: Any) -> Any:
    if isinstance(value, list):
        if value and isinstance(value[0], (int, float)):
            return [round(float(value[0]), 2), round(float(value[1]), 2)]
        return [_round_coordinates(item) for item in value]
    return value


def _normalize_geojson(level: str) -> dict[str, Any]:
    data = _read_json(str(GEO_DIR / f"china-{level}.geojson"))
    lookups = _admin_lookups()
    name_key = {"province": "province_by_code", "city": "city_by_code", "county": "county_by_code"}[level]
    code_key = "province_adcode" if level == "province" else "adcode"
    normalized = {"type": "FeatureCollection", "features": []}
    for feature in data.get("features", []):
        properties = dict(feature.get("properties") or {})
        code = str(properties.get(code_key) or properties.get("adcode") or properties.get("province_adcode") or "")
        if level == "province":
            code = code or str(properties.get("province_adcode") or "")
        if not code:
            continue
        compact_properties = {"adcode": code, "name": lookups[name_key].get(code, properties.get("name") or code), "level": level}
        if level == "city":
            compact_properties["province_adcode"] = str(properties.get("province_adcode") or "")
        if level == "county":
            compact_properties["city_adcode"] = str(properties.get("city_adcode") or "")
            compact_properties["province_adcode"] = str(properties.get("province_adcode") or "")
        geometry = dict(feature.get("geometry") or {})
        geometry["coordinates"] = _round_coordinates(geometry.get("coordinates") or [])
        normalized["features"].append({"type": "Feature", "properties": compact_properties, "geometry": geometry})
    return normalized


@lru_cache(maxsize=3)
def map_geojson(level: str) -> dict[str, Any]:
    return _normalize_geojson(level)


def _record_projection(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = ("id", "title", "source_url", "publish_date", "issuing_body", "gov_level", "region_province", "region_city", "region_county", "category_l1", "category_l2", "target_group", "target_industry", "amount", "deadline", "needs_review", "last_seen_at", "fetched_at")
    return [{key: record.get(key) for key in fields} for record in records if not record.get("needs_review")]


def _categories() -> list[str]:
    path = ROOT / "config" / "categories.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list((data.get("l1") or {}).keys())


def build_map_context(records: list[dict[str, Any]]) -> dict[str, Any]:
    projected = _record_projection(records)
    years = sorted({str(item.get("publish_date"))[:4] for item in projected if item.get("publish_date") and str(item.get("publish_date"))[:4].isdigit()}, reverse=True)
    current_year = datetime.now().year
    return {
        "records": projected,
        "geojson": {level: map_geojson(level) for level in ("province", "city", "county")},
        "lookups": _admin_lookups(),
        "categories": _categories(),
        "years": years or [str(current_year)],
        "coordinates": PROVINCE_COORDINATES,
        "province_short_names": PROVINCE_SHORT_NAMES,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _safe_script_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _map_script(theme_id: str) -> str:
    tc = get_theme_js_colors(theme_id)
    theme_options_js = json.dumps(
        {tid: {"label": THEME_LABELS[tid], "colors": get_theme_js_colors(tid)} for tid in THEME_ORDER},
        ensure_ascii=False,
    )
    return r"""
const DATA = __DATA__;
const THEME_ID = '__THEME_ID__';
const THEME_COLORS = __THEME_COLORS__;
const ALL_THEMES = __ALL_THEMES__;
const chart = echarts.init(document.getElementById('map'), null, {renderer: 'canvas'});
const state = {mode: 'globe', level: 'province', province: null, city: null, county: null, year: '5y', category: 'all', realtime: false, transitioning: false, theme: THEME_ID};
const levelNames = {province:'全国', city:'城市', county:'区县'};
const maps = {};
Object.keys(DATA.geojson).forEach(level => { maps[level] = DATA.geojson[level]; });
const coords = DATA.coordinates;
const shortNames = DATA.province_short_names;
const currentYear = Number(new Date().getFullYear());

// 主题颜色访问器 — 动态切换时只需更新此对象
let C = THEME_COLORS;

function applyTheme(themeId) {
  state.theme = themeId;
  C = ALL_THEMES[themeId].colors;
  // 更新CSS变量
  const root = document.documentElement;
  root.style.setProperty('--tc-ground', C.ground);
  root.style.setProperty('--tc-panel', C.panel);
  root.style.setProperty('--tc-line', C.line);
  root.style.setProperty('--tc-ink', C.ink);
  root.style.setProperty('--tc-muted', C.muted);
  root.style.setProperty('--tc-accent', C.accent);
  root.style.setProperty('--tc-amber', C.amber);
  root.style.setProperty('--tc-red', C.red);
  root.style.setProperty('--tc-border', C.border);
  // 更新主题选择器选中状态
  document.querySelectorAll('#theme-select option').forEach(opt => {
    opt.selected = opt.value === themeId;
  });
  // 重新渲染当前视图
  if (state.mode === 'globe') {
    showGlobe();
  } else {
    setMap();
  }
}

function esc(value) { return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function safeUrl(value) { return /^https?:\/\//i.test(String(value || '')) ? String(value) : '#'; }
function codeFor(record, level) {
  if (level === 'province') return DATA.lookups.province[record.region_province] || '';
  if (level === 'city') return DATA.lookups.city[record.region_city] || '';
  return DATA.lookups.county[record.region_county] || '';
}
function nameFor(level, code) { return (DATA.lookups[level + '_by_code'] || {})[code] || code || '未选择'; }
function shortName(code) { return shortNames[code] || nameFor('province', code); }

function yearAllowed(record) {
  if (state.year === 'all') return true;
  const year = Number(String(record.publish_date || '').slice(0,4));
  if (!year) return false;
  if (state.year === '5y') return year >= currentYear - 4 && year <= currentYear;
  if (state.year === '3y') return year >= currentYear - 2 && year <= currentYear;
  return year === Number(state.year);
}
function activeRecords() {
  return DATA.records.filter(record => yearAllowed(record) && (state.category === 'all' || record.category_l1 === state.category) && (!state.realtime || !record.last_seen_at || (Date.now() - new Date(record.last_seen_at).getTime() <= 86400000)));
}
function parentAllowed(record) {
  if (state.province && codeFor(record, 'province') !== state.province) return false;
  if (state.city && codeFor(record, 'city') !== state.city) return false;
  if (state.county && codeFor(record, 'county') !== state.county) return false;
  return true;
}
function currentLevelRecords() { return activeRecords().filter(parentAllowed); }

function aggregate(level) {
  const values = new Map();
  currentLevelRecords().forEach(record => {
    const code = codeFor(record, level);
    if (!code) return;
    const value = values.get(code) || {name: code, value: 0, latest: '', policy: 0, livelihood: 0, categories: {}, yearBreakdown: {}};
    value.value += 1;
    if (String(record.publish_date || '') > String(value.latest || '')) value.latest = record.publish_date || '';
    if (record.category_l1 === '行业政策' || record.category_l1 === '行业补贴' || record.category_l1 === '人才政策' || record.category_l1 === '养老与社区') value.policy += 1;
    if (record.category_l1 === '活动通知' || record.category_l1 === '招生信息' || record.category_l1 === '公招信息' || record.category_l1 === '公务员考试' || record.category_l1 === '军队招录') value.livelihood += 1;
    value.categories[record.category_l1 || '其他'] = (value.categories[record.category_l1 || '其他'] || 0) + 1;
    const yr = String(record.publish_date || '').slice(0,4) || '未知';
    value.yearBreakdown[yr] = (value.yearBreakdown[yr] || 0) + 1;
    values.set(code, value);
  });
  return [...values.values()].map(item => ({...item, name: String(item.name), value: item.value, categories: Object.entries(item.categories).sort((a,b) => b[1]-a[1]), yearBreakdown: Object.entries(item.yearBreakdown).sort((a,b) => b[0].localeCompare(a[0]))}));
}

function mapFeatures() {
  if (state.level === 'province') return maps.province.features;
  if (state.level === 'city') return maps.city.features.filter(feature => !state.province || String(feature.properties.province_adcode) === String(state.province));
  return maps.county.features.filter(feature => !state.city || String(feature.properties.city_adcode) === String(state.city));
}
function mapPayload() { return {type:'FeatureCollection', features: mapFeatures()}; }

function updateCrumb() {
  const items = ['全国'];
  if (state.province) items.push(nameFor('province', state.province));
  if (state.city) items.push(nameFor('city', state.city));
  if (state.county) items.push(nameFor('county', state.county));
  document.getElementById('crumb').textContent = items.join('  /  ');
  document.getElementById('scope').textContent = levelNames[state.level] + '视图';
}

// ========== 3D Globe ==========
let globeApi = null;
let globeAnimating = false;

function initGlobe() {
  if (globeApi || !window.THREE) return;
  const canvas = document.getElementById('globe');
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 1, 1000);
  camera.position.z = 310;
  const renderer = new THREE.WebGLRenderer({canvas, antialias:true, alpha:true});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(C.ground, 0);

  const group = new THREE.Group();
  scene.add(group);

  // 程序化地球纹理 — 主题化海洋与大陆颜色
  const earthCanvas = document.createElement('canvas');
  earthCanvas.width = 1024; earthCanvas.height = 512;
  const ectx = earthCanvas.getContext('2d');
  ectx.fillStyle = C.ocean; ectx.fillRect(0, 0, 1024, 512);
  ectx.fillStyle = C.land;
  ectx.beginPath(); ectx.ellipse(560, 180, 200, 80, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(540, 300, 70, 90, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(220, 170, 120, 70, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(280, 340, 50, 80, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(800, 340, 60, 35, 0, 0, Math.PI*2); ectx.fill();
  ectx.fillStyle = C.china_highlight;
  ectx.beginPath(); ectx.ellipse(680, 170, 60, 40, 0, 0, Math.PI*2); ectx.fill();

  const earthTexture = new THREE.CanvasTexture(earthCanvas);
  const globe = new THREE.Mesh(
    new THREE.SphereGeometry(100, 64, 32),
    new THREE.MeshPhongMaterial({map: earthTexture, color:0xffffff, emissive:0x03131f, emissiveIntensity:0.8, shininess:15, transparent:true, opacity:.95})
  );
  group.add(globe);

  // 线框层 — 主题accent色
  const accentHex = parseInt(C.accent.replace('#',''), 16);
  const wire = new THREE.Mesh(
    new THREE.SphereGeometry(100.5, 36, 18),
    new THREE.MeshBasicMaterial({color: accentHex, wireframe:true, transparent:true, opacity:.18})
  );
  group.add(wire);

  // 大气层光晕
  const atmosphere = new THREE.Mesh(
    new THREE.SphereGeometry(104, 48, 24),
    new THREE.MeshBasicMaterial({color: accentHex, transparent:true, opacity:.06, side:THREE.BackSide})
  );
  group.add(atmosphere);

  // 外层光晕
  const outerGlow = new THREE.Mesh(
    new THREE.SphereGeometry(108, 32, 16),
    new THREE.MeshBasicMaterial({color: accentHex, transparent:true, opacity:.03, side:THREE.BackSide})
  );
  group.add(outerGlow);

  // 星空背景 — 主题化星色
  const starGeometry = new THREE.BufferGeometry();
  const starPositions = [];
  const starColors = [];
  const sc = C.star_colors;
  for (let i=0; i<600; i++) {
    const theta = (i * 2.39996) % (Math.PI * 2);
    const phi = Math.acos(1 - 2 * ((i + .5) / 600));
    const radius = 180 + (i % 9) * 4;
    starPositions.push(radius * Math.sin(phi) * Math.cos(theta), radius * Math.cos(phi), radius * Math.sin(phi) * Math.sin(theta));
    const colorHex = sc[i % sc.length];
    const cv = parseInt(colorHex.replace('#',''), 16);
    starColors.push((cv >> 16 & 255)/255, (cv >> 8 & 255)/255, (cv & 255)/255);
  }
  starGeometry.setAttribute('position', new THREE.Float32BufferAttribute(starPositions, 3));
  starGeometry.setAttribute('color', new THREE.Float32BufferAttribute(starColors, 3));
  scene.add(new THREE.Points(starGeometry, new THREE.PointsMaterial({size:1.4, transparent:true, opacity:.7, vertexColors:true})));

  // 数据节点 — 各省发光点
  const points = aggregate('province').filter(item => coords[item.name]);
  const nodeGeometry = new THREE.BufferGeometry();
  const nodePositions = points.map(item => {
    const [lon, lat] = coords[item.name];
    const phi = (90 - lat) * Math.PI / 180;
    const theta = (lon + 180) * Math.PI / 180;
    return [-101 * Math.sin(phi) * Math.cos(theta), 101 * Math.cos(phi), 101 * Math.sin(phi) * Math.sin(theta)];
  });
  nodeGeometry.setAttribute('position', new THREE.Float32BufferAttribute(nodePositions, 3));
  const nodeMaterial = new THREE.PointsMaterial({color: accentHex, size:5, transparent:true, opacity:.9, sizeAttenuation:true});
  group.add(new THREE.Points(nodeGeometry, nodeMaterial));

  // 从北京到各省的弧线（飞线效果）
  const beijingCoord = coords['110000'];
  const beijingPhi = (90 - beijingCoord[1]) * Math.PI / 180;
  const beijingTheta = (beijingCoord[0] + 180) * Math.PI / 180;
  const beijingPos = new THREE.Vector3(-101 * Math.sin(beijingPhi) * Math.cos(beijingTheta), 101 * Math.cos(beijingPhi), 101 * Math.sin(beijingPhi) * Math.sin(beijingTheta));

  const arcGroup = new THREE.Group();
  group.add(arcGroup);

  points.forEach((item, idx) => {
    if (item.name === '110000') return;
    const [lon, lat] = coords[item.name];
    const phi = (90 - lat) * Math.PI / 180;
    const theta = (lon + 180) * Math.PI / 180;
    const targetPos = new THREE.Vector3(-101 * Math.sin(phi) * Math.cos(theta), 101 * Math.cos(phi), 101 * Math.sin(phi) * Math.sin(theta));

    const distance = beijingPos.distanceTo(targetPos);
    const midPoint = new THREE.Vector3().addVectors(beijingPos, targetPos).multiplyScalar(0.5);
    const arcHeight = Math.max(15, distance * 0.3);
    midPoint.normalize().multiplyScalar(100 + arcHeight);

    const curve = new THREE.QuadraticBezierCurve3(beijingPos, midPoint, targetPos);
    const arcPoints = curve.getPoints(50);
    const arcGeometry = new THREE.BufferGeometry().setFromPoints(arcPoints);

    const arcLine = new THREE.Line(arcGeometry, new THREE.LineBasicMaterial({
      color: accentHex, transparent: true, opacity: 0.15 + Math.min(0.3, item.value * 0.02)
    }));
    arcGroup.add(arcLine);

    const dotGeometry = new THREE.SphereGeometry(1.5, 8, 8);
    const dotMaterial = new THREE.MeshBasicMaterial({color: accentHex, transparent: true, opacity: 0.9});
    const dot = new THREE.Mesh(dotGeometry, dotMaterial);
    arcGroup.add(dot);

    dot.userData = {curve: curve, progress: Math.random(), speed: 0.002 + Math.random() * 0.003, item: item};
  });

  // 灯光
  scene.add(new THREE.AmbientLight(0x4a7080, 0.6));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
  dirLight.position.set(200, 100, 200);
  scene.add(dirLight);

  const resize = () => {
    const width = canvas.clientWidth || 600;
    const height = canvas.clientHeight || 500;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };

  const animate = () => {
    if (!globeAnimating) { requestAnimationFrame(animate); return; }
    group.rotation.y += .0012;
    group.rotation.x = Math.sin(Date.now() * .00012) * .03;

    arcGroup.children.forEach(child => {
      if (child.userData && child.userData.curve) {
        child.userData.progress += child.userData.speed;
        if (child.userData.progress > 1) child.userData.progress = 0;
        const pos = child.userData.curve.getPoint(child.userData.progress);
        child.position.copy(pos);
        child.material.opacity = 0.4 + Math.sin(child.userData.progress * Math.PI) * 0.5;
      }
    });

    resize();
    renderer.render(scene, camera);
    requestAnimationFrame(animate);
  };

  resize();
  globeAnimating = true;
  animate();

  // 填充地球底部统计
  const allValues = aggregate('province');
  const totalRecords = allValues.reduce((sum, v) => sum + v.value, 0);
  const totalPolicy = allValues.reduce((sum, v) => sum + v.policy, 0);
  const totalLife = allValues.reduce((sum, v) => sum + v.livelihood, 0);
  const elProv = document.getElementById('globe-provinces');
  const elTotal = document.getElementById('globe-total');
  const elPol = document.getElementById('globe-policy');
  const elLife = document.getElementById('globe-livelihood');
  if (elProv) elProv.textContent = allValues.length;
  if (elTotal) elTotal.textContent = totalRecords;
  if (elPol) elPol.textContent = totalPolicy;
  if (elLife) elLife.textContent = totalLife;

  globeApi = {renderer, scene, camera, group, arcGroup, resize, earthTexture};
}

// ========== 地球 → 地图过渡 ==========
function transitionToMap() {
  if (state.transitioning) return;
  state.transitioning = true;

  const canvas = document.getElementById('globe');
  const shell = document.getElementById('globe-shell');
  const mapShell = document.getElementById('map-shell');
  const overlay = document.querySelector('.globe-overlay');

  if (overlay) overlay.style.transition = 'opacity 0.6s ease';
  if (overlay) overlay.style.opacity = '0';

  if (globeApi) {
    let zoomProgress = 0;
    const zoomInterval = setInterval(() => {
      zoomProgress += 0.04;
      if (zoomProgress >= 1) {
        clearInterval(zoomInterval);
        shell.style.transition = 'opacity 0.4s ease';
        shell.style.opacity = '0';
        setTimeout(() => {
          shell.style.display = 'none';
          shell.style.opacity = '1';
          if (overlay) overlay.style.opacity = '1';
          mapShell.style.display = 'block';
          mapShell.style.opacity = '0';
          mapShell.style.transition = 'opacity 0.5s ease';
          chart.resize();
          setMap();
          setTimeout(() => { mapShell.style.opacity = '1'; }, 50);
          state.transitioning = false;
          state.mode = 'map';
          document.getElementById('mode-label').textContent = '中国地图视图';
        }, 400);
        return;
      }
      if (globeApi.group) {
        globeApi.group.rotation.y += 0.03;
        globeApi.camera.position.z = 310 - zoomProgress * 80;
      }
    }, 30);
  } else {
    shell.style.display = 'none';
    mapShell.style.display = 'block';
    chart.resize();
    setMap();
    state.transitioning = false;
    state.mode = 'map';
    document.getElementById('mode-label').textContent = '中国地图视图';
  }
}

function showGlobe() {
  state.mode = 'globe';
  document.getElementById('globe-shell').style.display = 'block';
  document.getElementById('globe-shell').style.opacity = '1';
  document.getElementById('map-shell').style.display = 'none';
  document.getElementById('mode-label').textContent = '3D 地球入口';
  globeAnimating = true;
  initGlobe();
  renderPanel([]);
}

function showMap() {
  if (state.mode === 'globe') {
    transitionToMap();
  } else {
    state.mode = 'map';
    document.getElementById('globe-shell').style.display = 'none';
    document.getElementById('map-shell').style.display = 'block';
    document.getElementById('mode-label').textContent = '中国地图视图';
    globeAnimating = false;
    chart.resize();
    setMap();
  }
}

// ========== 中国地图 ==========
function buildFlyLines(values) {
  if (state.level !== 'province') return [];
  const sorted = values.filter(v => coords[v.name]).sort((a,b) => b.value - a.value);
  if (sorted.length < 2) return [];
  const hub = sorted[0];
  const hubCoord = coords[hub.name];
  const lines = [];
  sorted.slice(1, 12).forEach(target => {
    const targetCoord = coords[target.name];
    if (targetCoord && hubCoord) {
      lines.push({
        fromName: shortName(hub.name),
        toName: shortName(target.name),
        coords: [hubCoord, targetCoord],
        value: target.value
      });
    }
  });
  return lines;
}

function setMap() {
  const mapName = 'active_' + state.level;
  echarts.registerMap(mapName, mapPayload());
  const values = aggregate(state.level);
  const flyLines = buildFlyLines(values);
  const maxVal = Math.max(1, ...values.map(item => item.value));

  const option = {
    backgroundColor: 'transparent',
    animation: true,
    animationDuration: 800,
    animationDurationUpdate: 600,
    animationEasingUpdate: 'cubicInOut',
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(5,16,29,.96)',
      borderColor: C.border,
      borderWidth: 1,
      padding: [10, 14],
      textStyle: {color: C.ink, fontSize: 12},
      formatter: params => {
        if (params.seriesType === 'lines') {
          return `<b>${esc(params.data.fromName)} 流向 ${esc(params.data.toName)}</b><br/>信息流量：${params.data.value} 条`;
        }
        const item = values.find(v => String(v.name) === String(params.name));
        if (!item) return esc(params.name || '');
        const name = esc(nameFor(state.level, item.name));
        const catList = item.categories.slice(0,4).map(([c,n]) => `${esc(c)}:${n}`).join(' · ');
        const yearList = item.yearBreakdown.slice(0,3).map(([y,n]) => `${esc(y)}:${n}`).join(' · ');
        return `<div style="min-width:180px"><b style="font-size:14px;color:${C.accent}">${name}</b><br/>` +
          `<span style="color:${C.muted}">信息总量：</span><b>${item.value}</b> 条<br/>` +
          `<span style="color:${C.muted}">政策类：</span>${item.policy} · <span style="color:${C.muted}">民生类：</span>${item.livelihood}<br/>` +
          `<span style="color:${C.muted}">分类：</span>${catList}<br/>` +
          `<span style="color:${C.muted}">年度：</span>${yearList}<br/>` +
          `<span style="color:${C.muted}">最近更新：</span>${esc(item.latest || '暂无')}</div>`;
      }
    },
    visualMap: {
      min: 0, max: maxVal, calculable: false, show: false,
      inRange: {color: [C.map_low, C.map_mid, C.map_high, C.map_peak]}
    },
    geo: {
      map: mapName, roam: true, silent: false, zoom: 1.12, center: [104, 36],
      itemStyle: {areaColor: C.deep, borderColor: C.border, borderWidth: 1},
      emphasis: {itemStyle: {areaColor: C.map_high, shadowBlur: 20, shadowColor: C.accent + '66'}, label: {color: C.ink, show: true, fontSize: 11}},
      select: {itemStyle: {areaColor: C.amber}, label: {show: true}}
    },
    series: [
      {
        type: 'map', map: mapName, roam: true, zoom: 1.12, center: [104, 36],
        nameProperty: 'adcode', selectedMode: 'single',
        label: {show: false},
        itemStyle: {areaColor: C.deep, borderColor: C.border, borderWidth: 1},
        emphasis: {
          itemStyle: {areaColor: C.map_high, shadowBlur: 18, shadowColor: C.accent + '88'},
          label: {show: true, color: C.ink, fontSize: 12}
        },
        data: values
      },
      ...(state.level === 'province' ? [{
        type: 'effectScatter', coordinateSystem: 'geo',
        data: values.filter(item => coords[item.name]).map(item => ({
          name: shortName(item.name),
          value: [...coords[item.name], item.value]
        })),
        symbolSize: val => Math.max(6, Math.min(24, Math.sqrt(val[2]) * 2.5)),
        rippleEffect: {brushType: 'stroke', scale: 3.5, period: 4},
        itemStyle: {color: C.accent, shadowBlur: 10, shadowColor: C.accent},
        label: {show: true, position: 'right', fontSize: 10, color: C.muted, formatter: '{b}'},
        zlevel: 2
      }] : []),
      ...(flyLines.length > 0 ? [{
        type: 'lines', coordinateSystem: 'geo', zlevel: 3,
        effect: {show: true, period: 5, trailLength: 0.6, symbol: 'arrow', symbolSize: 6, color: C.accent},
        lineStyle: {
          color: C.accent, width: 1, opacity: 0.4, curveness: 0.3
        },
        data: flyLines
      }] : []),
      ...(flyLines.length > 0 ? [{
        type: 'lines', coordinateSystem: 'geo', zlevel: 2,
        lineStyle: {color: C.map_high, width: 1, opacity: 0.15, curveness: 0.3},
        data: flyLines
      }] : [])
    ]
  };

  chart.setOption(option, true);
  updateCrumb();
  renderPanel(values);
}

// ========== 侧边面板 ==========
function renderPanel(values) {
  const rows = currentLevelRecords();
  const chosen = state.level === 'province' ? (state.province || '') : state.level === 'city' ? (state.city || '') : (state.county || '');
  const selected = values.find(item => String(item.name) === String(chosen)) || null;
  const title = chosen ? nameFor(state.level, chosen) : '全国';
  const categories = [...rows.reduce((acc, row) => {
    const key = row.category_l1 || '其他';
    acc.set(key, (acc.get(key) || 0) + 1);
    return acc;
  }, new Map()).entries()].sort((a,b) => b[1]-a[1]);

  const policy = rows.filter(row => ['行业政策','行业补贴','人才政策','养老与社区'].includes(row.category_l1)).length;
  const livelihood = rows.filter(row => ['活动通知','招生信息','公招信息','公务员考试','军队招录'].includes(row.category_l1)).length;

  const yearData = [...rows.reduce((acc, row) => {
    const yr = String(row.publish_date || '').slice(0,4) || '未知';
    acc.set(yr, (acc.get(yr) || 0) + 1);
    return acc;
  }, new Map()).entries()].sort((a,b) => a[0].localeCompare(b[0]));

  document.getElementById('panel-title').textContent = title;
  document.getElementById('panel-scope').textContent = chosen ? `${nameFor('province', state.province || chosen)} · ${levelNames[state.level]}` : '全国汇总';
  document.getElementById('panel-count').textContent = selected ? selected.value : rows.length;
  document.getElementById('panel-policy').textContent = selected ? selected.policy : policy;
  document.getElementById('panel-life').textContent = selected ? selected.livelihood : livelihood;

  const catColors = {'行业政策':C.accent,'行业补贴':C.accent,'人才政策':C.accent,'养老与社区':C.accent,'活动通知':C.amber,'招生信息':C.amber,'公招信息':C.amber,'公务员考试':C.amber,'军队招录':C.amber,'公务员考试':C.amber,'招聘信息':C.amber,'招标信息':C.red,'商业机会':C.red};
  document.getElementById('categories').innerHTML = categories.slice(0,8).map(([name,count]) => {
    const pct = Math.min(100, count / Math.max(1, rows.length) * 100);
    const color = catColors[name] || C.muted;
    return `<div class="bar-row" data-cat="${esc(name)}"><span class="cat-label" style="color:${color}">${esc(name)}</span><div class="bar"><i style="width:${pct}%;background:${color}"></i></div><b>${count}</b></div>`;
  }).join('') || '<div class="muted">当前筛选暂无分类数据</div>';

  if (yearData.length > 0) {
    const maxYear = Math.max(...yearData.map(([y,n]) => n));
    document.getElementById('year-trend').innerHTML = yearData.slice(-6).map(([yr,n]) => {
      const h = Math.max(2, n / Math.max(1, maxYear) * 28);
      return `<div class="trend-bar" title="${esc(yr)}: ${n}条"><span class="trend-val">${n}</span><div class="trend-col" style="height:${h}px"></div><span class="trend-yr">${esc(yr)}</span></div>`;
    }).join('');
  } else {
    document.getElementById('year-trend').innerHTML = '<div class="muted">暂无年度数据</div>';
  }

  const recent = rows.filter(row => !chosen || codeFor(row, state.level) === chosen)
    .sort((a,b) => String(b.publish_date||'').localeCompare(String(a.publish_date||'')))
    .slice(0,8);
  document.getElementById('records').innerHTML = recent.map(row => {
    const catColor = catColors[row.category_l1] || C.muted;
    return `<article class="record" data-id="${esc(row.id)}">
      <div class="record-top">
        <span class="tag" style="color:${catColor};border-color:${catColor}40">${esc(row.category_l1 || '其他')}</span>
        <time>${esc(row.publish_date || '实时')}</time>
      </div>
      <a href="${safeUrl(row.source_url)}" target="_blank" rel="noreferrer">${esc(row.title || '未命名信息')}</a>
      <small>${esc(row.issuing_body || row.gov_level || '官方来源')}</small>
    </article>`;
  }).join('') || '<div class="muted">当前条件下暂无公开信息</div>';
}

// ========== 下钻与导航 ==========
function drill(level, code) {
  if (level === 'province') { state.province = String(code); state.city = null; state.county = null; }
  if (level === 'city') { state.city = String(code); state.county = null; }
  if (level === 'county') { state.county = String(code); }
  state.level = level === 'province' ? 'city' : level === 'city' ? 'county' : 'county';
  setMap();
}

function back() {
  if (state.level === 'county' && state.city) { state.level = 'city'; state.county = null; }
  else if (state.level === 'city' && state.province) { state.level = 'province'; state.province = null; state.city = null; }
  else { state.level = 'province'; state.province = null; state.city = null; state.county = null; }
  setMap();
}

// ========== 事件绑定 ==========
chart.on('click', params => {
  if (params.componentType === 'series' && state.level !== 'county') {
    drill(state.level, params.name);
  }
});

document.getElementById('back').addEventListener('click', back);
document.getElementById('realtime').addEventListener('change', event => { state.realtime = event.target.checked; setMap(); });
document.getElementById('year').addEventListener('change', event => { state.year = event.target.value; setMap(); });
document.getElementById('category').addEventListener('change', event => { state.category = event.target.value; setMap(); });
document.getElementById('enter-map').addEventListener('click', showMap);
document.getElementById('globe-toggle').addEventListener('click', showGlobe);
document.getElementById('map-toggle').addEventListener('click', showMap);
document.getElementById('theme-select').addEventListener('change', event => { applyTheme(event.target.value); });

document.getElementById('categories').addEventListener('click', event => {
  const bar = event.target.closest('.bar-row');
  if (bar) {
    const cat = bar.dataset.cat;
    if (cat) {
      state.category = cat;
      document.getElementById('category').value = cat;
      setMap();
    }
  }
});

window.addEventListener('resize', () => { chart.resize(); if (globeApi) globeApi.resize(); });

// 初始化 — 显示地球
showGlobe();
""".replace("__DATA__", _safe_script_json(build_map_context.__wrapped__ if hasattr(build_map_context, '__wrapped__') else {})).replace("__THEME_ID__", theme_id).replace("__THEME_COLORS__", _safe_script_json(tc)).replace("__ALL_THEMES__", _safe_script_json({tid: {"label": THEME_LABELS[tid], "colors": get_theme_js_colors(tid)} for tid in THEME_ORDER}))


@st.cache_data(ttl=120, show_spinner=False)
def _cached_map_context(records: list[dict[str, Any]]) -> dict[str, Any]:
    return build_map_context(records)


def render_china_map(records: list[dict[str, Any]], theme_id: str = "cyber_neon", height: int = 820) -> None:
    context = _cached_map_context(records)
    tc = get_theme_js_colors(theme_id)
    echarts_source = (VENDOR_DIR / "echarts.min.js").read_text(encoding="utf-8")
    three_source = (VENDOR_DIR / "three.min.js").read_text(encoding="utf-8")

    # 构建JS脚本 — 先替换占位符再替换数据
    script_template = _map_script(theme_id)
    # _map_script 中已经做了占位符替换，但 DATA 需要在这里注入
    script = script_template.replace("__DATA__", _safe_script_json(context))

    map_h = height - 160

    # 主题化CSS — 所有颜色从tc动态注入
    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
:root {{
  --tc-ground: {tc['ground']};
  --tc-panel: {tc['panel']};
  --tc-line: {tc['line']};
  --tc-ink: {tc['ink']};
  --tc-muted: {tc['muted']};
  --tc-accent: {tc['accent']};
  --tc-amber: {tc['amber']};
  --tc-red: {tc['red']};
  --tc-border: {tc['border']};
}}
*{{box-sizing:border-box}}
html,body{{margin:0;background:var(--tc-ground);color:var(--tc-ink);font-family:Inter,Segoe UI,Microsoft YaHei,sans-serif;min-height:100%;overflow-x:hidden}}
body{{padding:16px;overflow:auto}}

/* 纹理底色叠加 */
body::before {{
  content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:
    radial-gradient(ellipse 80% 50% at 50% 0%, rgba(255,255,255,.02), transparent),
    radial-gradient(ellipse 60% 40% at 20% 100%, rgba(255,255,255,.012), transparent),
    linear-gradient(rgba(255,255,255,.01) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.01) 1px,transparent 1px);
  background-size:100% 100%,100% 100%,24px 24px,24px 24px;
}}

.hud{{max-width:1640px;margin:auto;position:relative}}

.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;border-bottom:1px solid var(--tc-line);padding-bottom:14px;position:relative}}
.top::before{{content:'';position:absolute;left:0;bottom:-1px;width:60px;height:2px;background:var(--tc-accent);box-shadow:0 0 8px var(--tc-accent)}}
h1{{font-size:22px;letter-spacing:.06em;margin:0 0 4px;color:var(--tc-ink);font-weight:600}}
.kicker{{font-size:10px;letter-spacing:.22em;color:var(--tc-accent);text-transform:uppercase;margin-bottom:6px;font-weight:600}}
.muted{{color:var(--tc-muted);font-size:12px}}
.live{{display:flex;align-items:center;gap:8px;color:var(--tc-accent);font-size:12px}}
.dot{{width:8px;height:8px;border-radius:50%;background:var(--tc-accent);box-shadow:0 0 0 5px rgba(255,255,255,.06);animation:pulse 2s infinite}}

.toolbar{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:14px 0;padding:10px 14px;background:var(--tc-panel);backdrop-filter:blur(16px) saturate(1.35);-webkit-backdrop-filter:blur(16px) saturate(1.35);border:1px solid var(--tc-line);border-radius:6px;box-shadow:0 3px 14px rgba(0,0,0,.25),inset 0 1px 0 rgba(255,255,255,.02)}}
select,button{{background:var(--tc-panel);color:var(--tc-ink);border:1px solid var(--tc-border);border-radius:3px;padding:7px 10px;font:inherit;font-size:12px;transition:all .2s}}
button{{cursor:pointer}}
button:hover{{border-color:var(--tc-accent);color:var(--tc-accent);box-shadow:0 0 8px rgba(255,255,255,.08)}}
button.active{{background:var(--tc-accent);color:var(--tc-ground);border-color:var(--tc-accent);font-weight:600}}
.crumb{{color:var(--tc-amber);font-size:12px;margin-left:auto;letter-spacing:.04em;font-weight:500}}
#theme-select{{min-width:90px}}

.layout{{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:12px}}

.map-panel{{background:var(--tc-panel);backdrop-filter:blur(20px) saturate(1.4);-webkit-backdrop-filter:blur(20px) saturate(1.4);border:1px solid var(--tc-line);border-radius:6px;position:relative;overflow:hidden;min-height:{map_h}px;box-shadow:0 6px 28px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.03)}}
.map-panel::before,.map-panel::after{{content:'';position:absolute;width:16px;height:16px;border:2px solid var(--tc-accent);z-index:5;pointer-events:none}}
.map-panel::before{{top:0;left:0;border-right:0;border-bottom:0}}
.map-panel::after{{bottom:0;right:0;border-left:0;border-top:0}}
.corner-tr,.corner-bl{{position:absolute;width:16px;height:16px;border:2px solid var(--tc-accent);z-index:5;pointer-events:none}}
.corner-tr{{top:0;right:0;border-left:0;border-bottom:0}}
.corner-bl{{bottom:0;left:0;border-right:0;border-top:0}}

.scan{{position:absolute;left:0;right:0;height:2px;top:0;background:linear-gradient(90deg,transparent,var(--tc-accent),transparent);box-shadow:0 0 12px var(--tc-accent);opacity:.5;z-index:4;animation:scan 6s linear infinite;pointer-events:none}}

.grid-overlay{{position:absolute;inset:0;background-image:linear-gradient(var(--tc-accent)08 1px,transparent 1px),linear-gradient(90deg,var(--tc-accent)08 1px,transparent 1px);background-size:40px 40px;z-index:3;pointer-events:none;opacity:.3}}

.globe-shell,#map-shell{{position:relative;z-index:1;height:{map_h}px;width:100%}}
#globe{{width:100%;height:100%;display:block}}

.globe-overlay{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none;z-index:6}}
.globe-copy{{text-align:center;pointer-events:auto;background:var(--tc-panel);backdrop-filter:blur(20px) saturate(1.4);-webkit-backdrop-filter:blur(20px) saturate(1.4);border:1px solid var(--tc-border);padding:22px 28px;border-radius:6px;box-shadow:0 8px 40px rgba(0,0,0,.4),0 0 40px rgba(255,255,255,.06),inset 0 1px 0 rgba(255,255,255,.04);opacity:.95}}
.globe-copy strong{{display:block;color:var(--tc-ink);font-size:18px;letter-spacing:.08em;margin-bottom:6px;font-weight:600}}
.globe-copy span{{display:block;color:var(--tc-muted);font-size:11px;margin-bottom:14px;letter-spacing:.04em}}
.globe-copy button{{background:var(--tc-accent);color:var(--tc-ground);border-color:var(--tc-accent);font-weight:700;padding:10px 20px;font-size:13px}}
.globe-copy button:hover{{box-shadow:0 0 20px rgba(255,255,255,.12)}}

.globe-stats{{position:absolute;bottom:12px;left:12px;right:12px;display:flex;justify-content:space-between;align-items:center;z-index:6;pointer-events:none}}
.globe-stat{{background:var(--tc-panel);border:1px solid var(--tc-line);padding:6px 10px;border-radius:3px;font-size:11px;color:var(--tc-muted);opacity:.85}}
.globe-stat b{{color:var(--tc-accent);font-size:14px;margin-left:4px}}

#map{{height:{map_h}px;width:100%}}

.side-panel{{background:var(--tc-panel);backdrop-filter:blur(20px) saturate(1.4);-webkit-backdrop-filter:blur(20px) saturate(1.4);border:1px solid var(--tc-line);border-radius:6px;padding:14px;min-height:{map_h}px;position:relative;overflow:hidden;box-shadow:0 6px 28px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.03)}}
.side-panel::before,.side-panel::after{{content:'';position:absolute;width:12px;height:12px;border:2px solid var(--tc-accent);z-index:5;pointer-events:none}}
.side-panel::before{{top:0;left:0;border-right:0;border-bottom:0}}
.side-panel::after{{bottom:0;right:0;border-left:0;border-top:0}}

.side-title{{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:1px solid var(--tc-line);padding-bottom:10px;margin-bottom:10px}}
h2{{font-size:17px;margin:0;color:var(--tc-ink);font-weight:600}}
.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:14px}}
.kpi{{background:var(--tc-deep,#1e2420);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid var(--tc-line);padding:8px 6px;border-radius:4px;text-align:center;position:relative;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.2),inset 0 1px 0 rgba(255,255,255,.02);transition:all .3s cubic-bezier(0.34,1.56,0.64,1)}}
.kpi:hover{{border-color:var(--tc-accent);transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.25),0 0 8px rgba(255,255,255,.04)}}
.kpi::after{{content:'';position:absolute;left:0;top:0;width:100%;height:1px;background:linear-gradient(90deg,transparent,var(--tc-accent),transparent);opacity:.4}}
.kpi span{{display:block;color:var(--tc-muted);font-size:10px;letter-spacing:.04em}}
.kpi b{{display:block;font-size:20px;color:var(--tc-accent);margin-top:3px;font-weight:700}}

h3{{font-size:11px;color:var(--tc-amber);letter-spacing:.1em;margin:14px 0 6px;font-weight:600;text-transform:uppercase}}

.bar-row{{display:grid;grid-template-columns:70px 1fr 22px;gap:6px;align-items:center;font-size:11px;margin:5px 0;color:var(--tc-muted);cursor:pointer;transition:all .2s;padding:2px 0;border-radius:2px}}
.bar-row:hover{{background:rgba(255,255,255,.04);padding-left:4px}}
.cat-label{{font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.bar{{height:4px;background:var(--tc-line);border-radius:4px;overflow:hidden}}
.bar i{{display:block;height:100%;border-radius:4px;transition:width .6s ease}}

#year-trend{{display:flex;align-items:flex-end;gap:4px;height:40px;margin:6px 0;padding:4px 0;border-bottom:1px solid var(--tc-line)}}
.trend-bar{{display:flex;flex-direction:column;align-items:center;flex:1;min-width:0;cursor:default}}
.trend-col{{width:100%;max-width:20px;background:linear-gradient(180deg,var(--tc-accent),var(--tc-accent-dim,#22c55e));border-radius:2px 2px 0 0;transition:height .4s ease;min-height:2px}}
.trend-val{{font-size:9px;color:var(--tc-accent);line-height:1;margin-bottom:1px}}
.trend-yr{{font-size:8px;color:var(--tc-muted);line-height:1;margin-top:2px}}

.record{{border-top:1px solid var(--tc-line);padding:8px 0;transition:all .2s}}
.record:first-child{{border-top:0}}
.record:hover{{padding-left:4px;background:rgba(255,255,255,.02)}}
.record a{{display:block;color:var(--tc-ink);font-size:12px;line-height:1.45;text-decoration:none;margin:4px 0;font-weight:500}}
.record a:hover{{color:var(--tc-accent)}}
.record small{{color:var(--tc-muted);font-size:10px}}
.record-top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:2px}}
.tag{{border:1px solid;padding:1px 5px;border-radius:2px;font-size:9px;font-weight:600;letter-spacing:.02em}}
time{{color:var(--tc-muted);font-size:10px}}

.footer{{margin-top:10px;color:var(--tc-muted);font-size:10px;letter-spacing:.04em;text-align:right;border-top:1px solid var(--tc-line);padding-top:8px;opacity:.7}}

@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
@keyframes scan{{0%{{top:0}}100%{{top:100%}}}}
@keyframes data-flow{{0%{{transform:translateX(-100%)}}100%{{transform:translateX(100%)}}}}
@keyframes entrance-fade{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:translateY(0)}}}}
.entrance-fade{{animation:entrance-fade .5s ease-out both}}

@media(max-width:900px){{
  .layout{{grid-template-columns:1fr}}
  .side-panel{{min-height:0}}
  .crumb{{margin-left:0;width:100%;text-align:right}}
  .top{{flex-direction:column}}
  #map,.map-panel,.globe-shell,#map-shell{{height:420px;min-height:420px}}
  .globe-stats{{display:none}}
  h1{{font-size:18px}}
  .kpis{{grid-template-columns:repeat(3,1fr)}}
}}
</style></head>
<body>
<div class="hud">
  <div class="top entrance-fade">
    <div>
      <div class="kicker">运营中心</div>
      <h1>中国公共政策与民生信息全景</h1>
      <div class="muted">来源证据 · 规则匹配 · 五年历史 · 实时脉搏</div>
    </div>
    <div class="live">
      <span class="dot"></span>
      <span>数据管道在线</span>
      <div class="muted" id="scope">全国视图</div>
    </div>
  </div>

  <div class="toolbar entrance-fade">
    <button id="globe-toggle" class="active">3D 地球</button>
    <button id="map-toggle">中国地图</button>
    <button id="back">返回上级</button>
    <span class="muted" id="mode-label">3D 地球入口</span>
    <select id="year">
      <option value="5y">近五年</option>
      <option value="3y">近三年</option>
      <option value="all">全部历史</option>
      {''.join(f'<option value="{escape(str(year))}">{escape(str(year))}年</option>' for year in context['years'])}
    </select>
    <select id="category">
      <option value="all">全部政策分类</option>
      {''.join(f'<option value="{escape(str(category))}">{escape(str(category))}</option>' for category in context['categories'])}
    </select>
    <select id="theme-select">
      {''.join(f'<option value="{tid}"{" selected" if tid == theme_id else ""}>{THEME_LABELS[tid]}</option>' for tid in THEME_ORDER)}
    </select>
    <label class="muted"><input type="checkbox" id="realtime"/> 只看实时</label>
    <div class="crumb" id="crumb">全国</div>
  </div>

  <div class="layout entrance-fade">
    <section class="map-panel">
      <div class="corner-tr"></div>
      <div class="corner-bl"></div>
      <div class="scan"></div>
      <div class="grid-overlay"></div>
      <div id="globe-shell" class="globe-shell">
        <canvas id="globe"></canvas>
        <div class="globe-overlay">
          <div class="globe-copy">
            <strong>灯塔工作台</strong>
            <span>中国公共政策与民生信息节点</span>
            <button id="enter-map">进入中国地图</button>
          </div>
        </div>
        <div class="globe-stats">
          <div class="globe-stat">监测省份<b id="globe-provinces">0</b></div>
          <div class="globe-stat">信息总量<b id="globe-total">0</b></div>
          <div class="globe-stat">政策类<b id="globe-policy">0</b></div>
          <div class="globe-stat">民生类<b id="globe-livelihood">0</b></div>
        </div>
      </div>
      <div id="map-shell" style="display:none">
        <div id="map"></div>
      </div>
    </section>

    <aside class="side-panel">
      <div class="side-title">
        <div>
          <div class="kicker">Selected Region</div>
          <h2 id="panel-title">全国</h2>
          <div class="muted" id="panel-scope">全国汇总</div>
        </div>
        <span class="muted">LIVE</span>
      </div>

      <div class="kpis">
        <div class="kpi"><span>信息量</span><b id="panel-count">0</b></div>
        <div class="kpi"><span>政策</span><b id="panel-policy">0</b></div>
        <div class="kpi"><span>民生</span><b id="panel-life">0</b></div>
      </div>

      <h3>年度趋势</h3>
      <div id="year-trend"></div>

      <h3>分类分布</h3>
      <div id="categories"></div>

      <h3>最新公开信息</h3>
      <div id="records"></div>

      <div class="footer">字段来自已审核公开来源 · 点击地图继续下钻 · 点击分类可筛选</div>
    </aside>
  </div>
</div>

<script>{three_source}</script>
<script>{echarts_source}</script>
<script>{script}</script>
</body></html>"""

    components.html(html_doc, height=height, scrolling=True)
