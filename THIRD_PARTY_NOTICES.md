# 第三方授权声明 / Third-Party Notices

Beacon OSINT 使用了以下第三方软件与数据。本项目自身的许可见 [`LICENSE`](LICENSE)；
**第三方组件仍适用它们各自的许可**，本项目的非商用限制不改变、也不替代这些许可。

Beacon OSINT incorporates the following third-party software and data. The Project's own
license is in [`LICENSE`](LICENSE). **Third-party components remain under their own
licenses**; the Project's non-commercial restriction neither modifies nor replaces them.

---

## 1. Apache ECharts

| | |
|---|---|
| 位置 / Location | `static/vendor/echarts.min.js` |
| 许可 / License | Apache License 2.0 |
| 授权文本 / License text | `static/vendor/ECHARTS_LICENSE.txt` |
| 上游 / Upstream | https://github.com/apache/echarts |
| 声明位置 / Banner | 文件头部保留原始 Apache-2.0 声明 |

用途：首页中国地图、省级分布热力图与全部 ECharts 图表渲染。
Used for: the China map on the home dashboard, province-level distribution heatmaps, and all chart rendering.

## 2. three.js

| | |
|---|---|
| 位置 / Location | `static/vendor/three.min.js` |
| 许可 / License | MIT |
| 授权文本 / License text | `static/vendor/THREE_LICENSE.txt` |
| 上游 / Upstream | https://github.com/mrdoob/three.js |
| 版本 / Version | r150 系列（该版本起 `build/three.min.js` 已弃用，r160 移除） |

用途：地图区域的三维渲染与交互。
Used for: 3D rendering and interaction on map regions.

## 3. 中国行政区划 GeoJSON

| | |
|---|---|
| 位置 / Location | `assets/geo/*.geojson`、`assets/geo/administrative-*.json` |
| 许可 / License | MIT（见 `assets/geo/PROVINCE_CITY_LICENSE.txt`） |
| 署名 / Attribution | `assets/geo/ATTRIBUTION.md` |

数据来源 / Data sources:

- 几何 / Geometry：`acanzaima/echarts-drill-down` → `public/geojson/china-province.geojson`（MIT）
  - https://github.com/acanzaima/echarts-drill-down
- 行政区划代码 / Administrative codes：`uiwjs/province-city-china`，包版本 8.5.8（MIT）
  - https://github.com/uiwjs/province-city-china

> ⚠️ **上游条款提示 / Upstream terms caveat**
> `acanzaima/echarts-drill-down` 的上游 README 说明其 PBF/GeoJSON 原始数据来自
> **AntV L7 GISDATA**，并提示**商业部署需单独核实上游数据条款**。
> 本项目采用非商用许可（见 `LICENSE`），恰好规避该风险；若你计划将本项目用于商业
> 场景，**必须先自行核实 AntV L7 GISDATA 的数据授权**。
>
> The upstream README states the PBF/GeoJSON source data comes from **AntV L7 GISDATA**
> and that **commercial deployment must separately verify the upstream data terms**.
> The Project uses a non-commercial license (see `LICENSE`), which avoids this risk;
> if you intend commercial use, **verify AntV L7 GISDATA's data terms yourself first**.

## 4. Python 依赖 / Python dependencies

完整清单见 [`requirements.txt`](requirements.txt)。其中若干依赖为可选增强项，
其 import 均包在 `try/except` 中，缺失时不会导致启动失败：

Some are optional enhancements; their imports are wrapped in `try/except` and a missing
package degrades gracefully rather than breaking startup:

| 包 / Package | 用途 / Purpose | 缺失后果 / If missing |
|---|---|---|
| `cloudscraper` / `curl-cffi` / `cloakbrowser` | WAF 回退层 | 遇 403/412 的站点静默采不到数据 |
| `pymupdf` | PDF 正文抽取 | PDF 公告无法抽取正文 |
| `trafilatura` + `lxml_html_clean` | 正文主体抽取 | 正文退化为整页 `get_text()`，被站点框架文字污染 |
| `playwright` / `scrapy-playwright` | 动态渲染回退 | JS 渲染的列表页取不到链接 |
| `crawl4ai` | 高级正文抽取 | 回退到内置抽取器 |
| `gliner2[local]` | 分类/字段抽取增强 | 分类规则仍可用，精度下降 |
| `blazerules` | 订阅规则匹配 | 订阅功能不可用 |

各包许可请查阅其各自仓库：Consult each package's own repository for its license.

---

## 5. 采集数据 / Collected data

本项目**不随仓库分发**任何采集到的政务公告原文、数据库或派生数据集（见 `.gitignore`）。

**No collected government-affairs documents, databases, or derived datasets are
distributed with this repository** (see `.gitignore`).

此类内容的著作权与使用规则由**原始发布机关**决定，不适用本项目的许可证。使用者须自行
遵守目标站点的 robots 协议、服务条款与适用法律法规。

Copyright in such content is determined by its **original issuing authority**; the Project's
license does not apply to it. You are responsible for complying with each target site's
robots policy, terms of service, and applicable law.